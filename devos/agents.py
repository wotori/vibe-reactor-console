"""Bounded, explicit agent turns. Terminal pixels are never a control protocol.

Constructing the coordinator starts no subprocesses and never calls a model.
Demo is a finite local story; Watch terminals remain human-owned. Relay/Mission
use completed JSONL results, separate sessions and distinct working directories.
"""

from collections import deque
from dataclasses import dataclass
import hashlib
import difflib
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import threading
import time
import uuid


MAX_MESSAGE = 16384
PROVIDERS = ("codex", "claude")


def bounded_text(value, limit=MAX_MESSAGE):
    safe = "".join(c for c in str(value) if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)
    return safe.encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")


@dataclass(frozen=True)
class Capabilities:
    available: bool
    can_stream: bool = False
    can_resume: bool = False
    can_cancel: bool = True
    can_request_approval: bool = False
    can_report_usage: bool = False
    can_report_cost: bool = False
    read_only: bool = False
    workspace_write: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AgentCard:
    id: str
    name: str
    role: str
    status: str = "IDLE"
    summary: str = ""
    session_id: str = ""
    usage: tuple = ()
    cost: object = None


@dataclass(frozen=True)
class ArenaEvent:
    sequence: int
    kind: str
    actor: str
    text: str
    timestamp: float


@dataclass(frozen=True)
class Artifact:
    id: str
    kind: str
    sha256: str
    producer: str
    base: object = None
    paths: tuple = ()
    text: str = ""


@dataclass(frozen=True)
class Handoff:
    message_id: str
    sequence: int
    sender: str
    recipient: str
    summary: str
    expected_result: str
    status: str = "ready"
    artifacts: tuple = ()
    reply_to: object = None
    type: str = "review_request"


@dataclass(frozen=True)
class ArenaSnapshot:
    mode: str
    status: str
    cards: tuple
    events: tuple
    handoffs: tuple
    artifacts: tuple
    elapsed: float
    max_handoffs: int
    deadline: float
    reason: str = ""
    verified: bool = False
    validation_reports: tuple = ()
    phase: str = "idle"
    fix_cycles: int = 0


@dataclass(frozen=True)
class TurnResult:
    status: str
    text: str = ""
    session_id: str = ""
    usage: tuple = ()
    cost: object = None
    error: str = ""


@dataclass(frozen=True)
class ValidationReport:
    status: str
    argv: tuple
    cwd: str
    revision: object
    artifact_hash: str
    started: float
    elapsed: float
    exit_code: object
    stdout: str
    stderr: str


class CLIAdapter:
    """Version-gated public interfaces. Authentication is left to each CLI."""
    def __init__(self, provider, executable=None):
        if provider not in PROVIDERS:
            raise ValueError("Unknown agent provider")
        self.provider = provider
        self.executable = executable or shutil.which(provider)
        self.capabilities = None

    def detect(self):
        if not self.executable:
            self.capabilities = Capabilities(False, reason=self.provider + " CLI not installed")
            return self.capabilities
        args = [self.executable, "exec", "--help"] if self.provider == "codex" else [self.executable, "--help"]
        try:
            help_text = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       timeout=5, check=False).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError) as exc:
            self.capabilities = Capabilities(False, reason=str(exc))
            return self.capabilities
        if self.provider == "codex":
            supported = all(flag in help_text for flag in ("--json", "--sandbox", "--ignore-user-config", "resume"))
        else:
            supported = all(flag in help_text for flag in ("--output-format", "--resume", "--verbose", "--tools",
                                                          "--restricted", "--strict-mcp-config", "--mcp-config",
                                                          "--settings", "--disable-slash-commands", "--permission-mode"))
        self.capabilities = Capabilities(supported, supported, supported, True, False, supported,
                                         supported and self.provider == "claude", supported, supported,
                                         "" if supported else "Installed CLI lacks required structured/restricted flags")
        return self.capabilities

    def command(self, profile="read-only", session_id=None):
        if profile not in ("read-only", "workspace-write"):
            raise ValueError("Unsupported tool profile")
        caps = self.capabilities or self.detect()
        if not caps.available or not getattr(caps, profile.replace("-", "_")):
            raise RuntimeError(caps.reason or "Requested tool profile is unsupported")
        if session_id is not None and (not session_id or len(session_id) > 128 or
                                       any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in session_id)):
            raise ValueError("Invalid provider session ID")
        if self.provider == "codex":
            command = [self.executable, "exec", "--ignore-user-config", "--sandbox", profile,
                       "-c", 'approval_policy="never"', "--skip-git-repo-check"]
            return command + (["resume", "--json", session_id, "-"] if session_id else ["--json", "-"])
        # Restricted mode confines built-in file tools to cwd and removes code
        # execution. Empty strict MCP prevents tools loaded from local settings.
        tools = "Read,Glob,Grep" + (",Edit,Write" if profile == "workspace-write" else "")
        command = [self.executable, "-p", "--output-format", "stream-json", "--verbose",
                   "--restricted", "--tools", tools, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--disable-slash-commands", "--settings", '{"disableAllHooks":true}',
                   "--permission-mode", "acceptEdits" if profile == "workspace-write" else "dontAsk"]
        return command + (["--resume", session_id] if session_id else [])


class ManagedTurn:
    """One cancellable subprocess, with independently drained bounded pipes."""
    def __init__(self, argv, cwd, prompt, provider, timeout=900):
        if len(prompt.encode("utf-8")) > MAX_MESSAGE:
            raise ValueError("Managed prompt exceeds 16 KiB")
        self.argv, self.cwd, self.prompt = tuple(argv), str(cwd), prompt
        self.provider, self.timeout = provider, timeout
        self.events = deque(maxlen=256)
        self.result = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._process = None
        self._thread = threading.Thread(target=self._run, name="devos-agent-" + provider, daemon=True)
        self._thread.start()

    def drain(self):
        with self._lock:
            result = tuple(self.events)
            self.events.clear()
            return result, self.result

    def cancel(self):
        self._cancel.set()

    def close(self):
        self.cancel()
        self._thread.join(timeout=4)

    def _run(self):
        session, text, usage, cost, final, protocol_error = "", "", (), None, False, ""
        stderr = bytearray()
        state_lock = threading.Lock()

        def consume_stdout(pipe):
            nonlocal session, text, usage, cost, final, protocol_error
            count = 0
            try:
                while True:
                    raw = pipe.readline(1024 * 1024 + 1)
                    if not raw:
                        break
                    count += 1
                    if len(raw) > 1024 * 1024 or count > 10000:
                        protocol_error = "Protocol stream exceeds bounded limits"
                        self._cancel.set()
                        break
                    try:
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise ValueError("Expected JSON object")
                    except (ValueError, UnicodeDecodeError):
                        protocol_error = "Malformed JSON from managed adapter; nothing relayed"
                        continue
                    kind = event.get("type", "")
                    # Only public completed messages enter the handoff contract.
                    if self.provider == "codex":
                        if kind == "thread.started":
                            session = bounded_text(event.get("thread_id", ""), 128)
                        elif kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                            text = bounded_text(event["item"].get("text", ""))
                        elif kind == "turn.completed":
                            final = True
                            usage = tuple(sorted((str(k), v) for k, v in event.get("usage", {}).items()
                                                 if isinstance(v, (int, float))))
                        elif kind in ("turn.failed", "error"):
                            protocol_error = bounded_text(event.get("message", event.get("error", "Provider error")), 1024)
                    else:
                        if kind == "system" and event.get("subtype") == "init":
                            session = bounded_text(event.get("session_id", ""), 128)
                        elif kind == "result":
                            final = not event.get("is_error", False) and event.get("subtype", "success") == "success"
                            session = bounded_text(event.get("session_id", session), 128)
                            text = bounded_text(event.get("result", ""))
                            usage = tuple(sorted((str(k), v) for k, v in event.get("usage", {}).items()
                                                 if isinstance(v, (int, float))))
                            cost = event.get("total_cost_usd") if isinstance(event.get("total_cost_usd"), (float, int)) else None
                            if not final:
                                protocol_error = text or "Claude returned an error result"
                    if kind in ("thread.started", "turn.completed", "result", "error", "turn.failed"):
                        with self._lock:
                            self.events.append((kind, session))
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                protocol_error = "Invalid protocol event: " + str(exc)
                self._cancel.set()
            finally:
                pipe.close()

        def consume_stderr(pipe):
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    with state_lock:
                        stderr.extend(chunk)
                        if len(stderr) > 65536:
                            del stderr[:-65536]
            finally:
                pipe.close()

        try:
            self._process = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             start_new_session=(os.name == "posix"))
            readers = [threading.Thread(target=consume_stdout, args=(self._process.stdout,), daemon=True),
                       threading.Thread(target=consume_stderr, args=(self._process.stderr,), daemon=True)]
            for thread in readers:
                thread.start()
            try:
                self._process.stdin.write(self.prompt.encode("utf-8"))
                self._process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            deadline = time.monotonic() + self.timeout
            timed_out = False
            while self._process.poll() is None:
                if self._cancel.wait(.02) or time.monotonic() >= deadline:
                    timed_out = not self._cancel.is_set()
                    self._terminate()
                    break
            for thread in readers:
                thread.join(timeout=1)
            if any(thread.is_alive() for thread in readers):
                # A background descendant may still own a pipe after its parent
                # exits. It belongs to this disposable worker group, not the UI.
                self._terminate()
                for thread in readers:
                    thread.join(timeout=1)
            if self._cancel.is_set() and not protocol_error:
                result = TurnResult("CANCELLED", session_id=session, error="Local worker stopped; remote completion unconfirmed")
            elif timed_out:
                result = TurnResult("DELIVERY_UNKNOWN", session_id=session, error="Turn deadline reached; do not resend automatically")
            elif protocol_error:
                result = TurnResult("FAILED", text, session, usage, cost, protocol_error)
            elif self._process.returncode == 0 and final and text:
                result = TurnResult("SUCCEEDED", text, session, usage, cost)
            elif self._process.returncode:
                result = TurnResult("FAILED", session_id=session,
                                    error=bounded_text(stderr.decode("utf-8", "replace"), 2048) or "Agent process failed")
            else:
                result = TurnResult("DELIVERY_UNKNOWN", session_id=session,
                                    error="Process exited without a completed public result; no automatic retry")
        except (OSError, subprocess.SubprocessError) as exc:
            result = TurnResult("FAILED", error=str(exc))
        finally:
            if self._process is not None and self._process.poll() is None:
                self._terminate()
        with self._lock:
            self.result = result

    def _terminate(self):
        try:
            if os.name == "posix":
                os.killpg(self._process.pid, signal.SIGTERM)
            else:
                self._process.terminate()
            self._process.wait(timeout=1)
            if os.name == "posix":
                # The parent may already have exited while descendants inherited
                # its stdout/stderr. Reap the entire group we created.
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(self._process.pid, signal.SIGKILL)
            else:
                self._process.kill()
            self._process.wait(timeout=1)
        except (ProcessLookupError, OSError):
            pass


class ValidationRun(ManagedTurn):
    """An explicit argv check bound to a concrete workspace content hash."""
    def __init__(self, argv, cwd, artifact_hash, revision=None, timeout=60):
        if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
            raise ValueError("Validation needs a literal argv sequence")
        self.artifact_hash, self.revision = artifact_hash, revision
        super().__init__(argv, cwd, "", "validation", timeout)

    def _run(self):
        outputs = [bytearray(), bytearray()]
        started = time.monotonic()
        status, exit_code = "FAILED", None

        def read(pipe, target):
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    target.extend(chunk)
                    if len(target) > 65536:
                        del target[:-65536]
            finally:
                pipe.close()

        try:
            self._process = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             start_new_session=(os.name == "posix"))
            readers = [threading.Thread(target=read, args=(pipe, output), daemon=True)
                       for pipe, output in zip((self._process.stdout, self._process.stderr), outputs)]
            for reader in readers:
                reader.start()
            while self._process.poll() is None:
                if self._cancel.wait(.02) or time.monotonic() - started >= self.timeout:
                    status = "CANCELLED" if self._cancel.is_set() else "TIMED_OUT"
                    self._terminate()
                    break
            else:
                status = "SUCCEEDED" if self._process.returncode == 0 else "FAILED"
            exit_code = self._process.poll()
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                self._terminate()
                for reader in readers:
                    reader.join(timeout=1)
        except (OSError, subprocess.SubprocessError) as exc:
            outputs[1].extend(str(exc).encode())
        finally:
            if self._process is not None and self._process.poll() is None:
                self._terminate()
        report = ValidationReport(status, self.argv, self.cwd, self.revision, self.artifact_hash,
                                  started, time.monotonic() - started, exit_code,
                                  bounded_text(outputs[0].decode("utf-8", "replace"), 65536),
                                  bounded_text(outputs[1].decode("utf-8", "replace"), 65536))
        with self._lock:
            self.result = report


def validate_workspaces(workspaces):
    if set(workspaces) != set(PROVIDERS):
        raise ValueError("Mission needs separate codex and claude workspace paths")
    paths = {name: Path(path).resolve(strict=True) for name, path in workspaces.items()}
    if any(not path.is_dir() for path in paths.values()):
        raise ValueError("Workspace must be a directory")
    left, right = paths.values()
    if left == right or left in right.parents or right in left.parents:
        raise ValueError("Parallel agents cannot share or nest working directories")
    return {name: str(path) for name, path in paths.items()}


def _workspace_bytes(root, relative):
    """Read one bounded regular file without following a final symlink."""
    root, relative = Path(root).resolve(strict=True), Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Artifact path escapes its workspace")
    path = root / relative
    if root not in path.resolve(strict=True).parents or path.is_symlink():
        raise ValueError("Artifact symlink escapes are unsupported")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError("Artifact must be a regular file of at most 1 MiB")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            result = stream.read(1024 * 1024 + 1)
        if len(result) > 1024 * 1024:
            raise ValueError("Artifact grew beyond the 1 MiB limit")
        return result
    finally:
        os.close(fd)


def snapshot_manifest(root, max_files=2000, max_bytes=32 * 1024 * 1024):
    """Hash only bounded regular files; no secrets, links or external file URLs."""
    root = Path(root).resolve(strict=True)
    entries, total = [], 0
    excluded = {".git", ".env", ".codex", ".claude", "node_modules", ".venv", "__pycache__", ".devos",
                ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "build", "dist", "target",
                ".npmrc", ".pypirc", "credentials", "credentials.json", "secrets", "secrets.json", "id_rsa", "id_ed25519"}
    for directory, dirs, files in os.walk(str(root), followlinks=False):
        dirs[:] = sorted(name for name in dirs if name.casefold() not in excluded and not name.endswith(".egg-info")
                         and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if (name.casefold().startswith(".env") or name.casefold() in excluded or path.is_symlink() or
                    path.suffix.casefold() in (".pem", ".key", ".p12", ".pfx", ".keystore")):
                continue
            info = path.stat()
            size = info.st_size
            if not stat.S_ISREG(info.st_mode) or size > 1024 * 1024:
                continue
            if len(entries) >= max_files or total + size > max_bytes:
                raise ValueError("Workspace snapshot exceeds file/byte limits; select a smaller project")
            data = _workspace_bytes(root, path.relative_to(root))
            total += len(data)
            entries.append((str(path.relative_to(root)), hashlib.sha256(data).hexdigest()))
    encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode()
    return tuple(entries), hashlib.sha256(encoded).hexdigest()


def prepare_workspaces(source, destination, mode="snapshot"):
    """Explicit setup helper; retains all outputs and never resets/cleans a repo."""
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if not source.is_dir():
        raise ValueError("Mission source must be a directory")
    if destination == source or source in destination.parents:
        raise ValueError("Mission destination must be outside the source project")
    if destination.exists():
        raise ValueError("Mission destination must not already exist")
    try:
        base = subprocess.run(["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=5)
        revision = base.stdout.decode().strip() if base.returncode == 0 else None
    except OSError:
        revision = None
    if mode == "worktree" and revision is None:
        raise ValueError("Worktree mode needs an existing commit; use snapshot mode or Watch")
    if mode not in ("snapshot", "worktree"):
        raise ValueError("Unknown workspace mode")
    # Snapshot mode deliberately includes selected uncommitted source bytes.
    # Worktrees instead use one explicit HEAD commit, never the dirty source.
    entries, digest = snapshot_manifest(source) if mode == "snapshot" else (None, None)
    destination.mkdir(parents=True)
    result = {}

    def copy_manifest(origin, target):
        target.mkdir()
        for relative, expected in entries:
            data = _workspace_bytes(origin, relative)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Source changed during snapshot; no mission was started")
            copied = target / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(data)
            copied.chmod((Path(origin) / relative).stat().st_mode & 0o777)
        if snapshot_manifest(target)[1] != digest:
            raise ValueError("Copied snapshot does not match the attested base manifest")

    for provider in PROVIDERS:
        target = destination / provider
        if mode == "worktree":
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(source),
                            "worktree", "add", "--detach", str(target), revision],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if entries is None:
                entries, digest = snapshot_manifest(target)
            elif snapshot_manifest(target)[1] != digest:
                raise ValueError("Detached worktrees do not match the selected base commit")
        else:
            copy_manifest(source, target)
        result[provider] = str(target)
    base_path = destination / "base"
    copy_manifest(Path(result["codex"]) if mode == "worktree" else source, base_path)
    (destination / "manifest.json").write_text(json.dumps({"schema_version": 1, "base": revision, "sha256": digest,
                                                         "base_path": str(base_path), "mode": mode,
                                                         "files": entries}, indent=2) + "\n")
    return validate_workspaces(result)


class AgentCoordinator:
    DEMO_STORY = (
        ("codex", "RUNNING", "Builder maps the event-driven scene engine."),
        ("claude", "RUNNING", "Challenger explores stale data and resize cases."),
        ("codex", "SUCCEEDED", "Patch ready: separate scenes, signals and visual bindings."),
        ("handoff", "codex", "Review the bounded event bus and stale-state rendering."),
        ("claude", "RUNNING", "Reviewing the exact public artifact; no hidden reasoning shared."),
        ("claude", "SUCCEEDED", "Add a stale badge and cap event history at 512 entries."),
        ("handoff", "claude", "Apply the stale badge and bounded-history feedback."),
        ("codex", "RUNNING", "Builder applies the review in its own workspace."),
        ("codex", "SUCCEEDED", "Result ready. Demo checks: 12 fixtures passed (simulated)."),
        ("done", "DEMO_COMPLETE", "Finite demo complete. No model calls or project changes occurred."),
    )

    def __init__(self, mode="demo", clock=time.monotonic, adapters=None, worker_factory=ManagedTurn):
        if mode not in ("demo", "watch", "relay", "mission"):
            raise ValueError("Unknown Arena mode")
        self.mode, self.clock, self.worker_factory = mode, clock, worker_factory
        self.adapters = adapters or {name: CLIAdapter(name) for name in PROVIDERS}
        self.status = "DEMO" if mode == "demo" else "IDLE"
        self.cards = {name: AgentCard(name, name.title(), "Builder" if name == "codex" else "Challenger") for name in PROVIDERS}
        self.events, self.handoffs, self.artifacts = deque(maxlen=256), deque(maxlen=64), deque(maxlen=64)
        self.workspaces, self.profiles, self.workers = {}, {}, {}
        self._baselines = {}
        self._completed_results = {}
        self.phase, self.fix_cycles = "idle", 0
        self._review_snapshot = None
        self._acceptance_cases = ""
        self._review_pairs = set()
        self.sessions = {}
        self._sequence = self._demo_index = self._round = 0
        self._seen_messages, self._fingerprints = set(), set()
        self._demo_elapsed = self.elapsed = 0.0
        self.started = None
        self.max_handoffs, self.deadline = 6, 900
        self.reason, self.prompt, self.scenario = "", "", "design-rally"
        self.verified = False
        self.validation = None
        self.validation_reports = []
        self._checks = deque()

    def _event(self, kind, actor, text):
        self._sequence += 1
        self.events.append(ArenaEvent(self._sequence, kind, actor, bounded_text(text, 4096), self.clock()))

    def _card(self, actor, status, summary="", result=None):
        old = self.cards[actor]
        self.cards[actor] = AgentCard(actor, old.name, old.role, status, bounded_text(summary, 4096),
                                     self.sessions.get(actor, old.session_id),
                                     result.usage if result else old.usage, result.cost if result else old.cost)

    def snapshot(self):
        return ArenaSnapshot(self.mode, self.status, tuple(self.cards.values()), tuple(self.events),
                             tuple(self.handoffs), tuple(self.artifacts), self.elapsed,
                             self.max_handoffs, self.deadline, self.reason, self.verified,
                             tuple(self.validation_reports), self.phase, self.fix_cycles)

    def step_demo(self):
        if self.mode != "demo" or self._demo_index >= len(self.DEMO_STORY):
            return False
        actor, status, text = self.DEMO_STORY[self._demo_index]
        self._demo_index += 1
        if actor == "handoff":
            recipient = "claude" if status == "codex" else "codex"
            self.send_handoff(status, recipient, text, expected="Review the demo artifact", dispatch=False)
        elif actor == "done":
            self.status, self.reason = status, text
        else:
            self._card(actor, status, text)
            if status == "SUCCEEDED":
                self._artifact(actor, text)
        self._event("demo", actor, text)
        return True

    def _artifact(self, actor, text):
        artifact = Artifact(uuid.uuid4().hex, "decision_note", hashlib.sha256(text.encode()).hexdigest(), actor, text=text)
        self.artifacts.append(artifact)
        return artifact

    def update(self, dt=0):
        if self.mode == "demo":
            if self.status == "PAUSED":
                return
            self._demo_elapsed += max(0, dt)
            self.elapsed += max(0, dt)
            while self._demo_elapsed >= 4 and self._demo_index < len(self.DEMO_STORY):
                self._demo_elapsed -= 4
                self.step_demo()
            return
        if self.started is not None:
            self.elapsed = max(0, self.clock() - self.started)
        if self.status in ("RUNNING", "PAUSING", "PAUSED") and self.elapsed >= self.deadline:
            self.reason = "DEADLINE / new deliveries stopped"
            self.stop(status="NEEDS_USER")
        for actor, worker in list(self.workers.items()):
            events, result = worker.drain()
            for kind, session in events:
                self._event("provider", actor, kind)
            if result is None:
                continue
            self.workers.pop(actor)
            if result.session_id:
                self.sessions[actor] = result.session_id
            self._card(actor, result.status, result.text or result.error, result)
            self._event("turn", actor, result.status + ": " + (result.text or result.error))
            if result.status == "SUCCEEDED":
                self._completed_results[actor] = bounded_text(result.text)
                self._artifact(actor, result.text)
                self._capture_patch(actor)
                for index, message in enumerate(self.handoffs):
                    if message.recipient == actor and message.status == "delivered":
                        self.handoffs[index] = Handoff(**{**message.__dict__, "status": "replied"})
            else:
                for index, message in enumerate(self.handoffs):
                    if message.recipient == actor and message.status == "delivered":
                        delivery = "DELIVERY_UNKNOWN" if result.status == "DELIVERY_UNKNOWN" else "rejected"
                        self.handoffs[index] = Handoff(**{**message.__dict__, "status": delivery})
            if result.status != "SUCCEEDED" and self.status in ("RUNNING", "PAUSING", "PAUSED", "READY"):
                self.status, self.reason = "NEEDS_USER", result.error or result.status
        self._update_validation()
        if self.status == "PAUSING" and not self.workers:
            self.status = "PAUSED"
        if self.status == "RUNNING" and not self.workers:
            if self.mode == "relay":
                self.status = "READY"
            elif self.scenario == "builder-challenger":
                try:
                    self._advance_builder()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.status, self.reason = "NEEDS_USER", "Builder/Challenger stopped: " + str(exc)
            elif self._round == 0 and all(card.status == "SUCCEEDED" for card in self.cards.values()):
                # Independent first tasks have both completed. Exchange immutable
                # public summaries, never token deltas or peer-provided commands.
                self._round = 1
                summaries = {actor: self.cards[actor].summary for actor in PROVIDERS}
                for actor in PROVIDERS:
                    other = "claude" if actor == "codex" else "codex"
                    if self.status != "RUNNING":
                        break
                    artifacts = tuple(item for item in self.artifacts if item.producer == actor)[-2:]
                    patches = "\n".join(item.text for item in artifacts if item.kind == "patch")
                    self.send_handoff(actor, other, bounded_text(summaries[actor] + ("\n\nACTUAL PATCH:\n" + patches if patches else ""), 12000),
                                      artifacts=artifacts, expected="Critique the completed result against the original task.")
            else:
                self.status = "READY_FOR_USER" if self.scenario == "design-rally" else "NEEDS_USER"
                self.reason = "Completed public results available; independent validation has not run."

    def start_mission(self, prompt, workspaces, scenario="design-rally", max_handoffs=6, timeout=900,
                      profiles=None, mode="mission"):
        if self.workers or self.validation:
            raise RuntimeError("Wait for active turns to finish before starting another mission")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 12000:
            raise ValueError("Mission needs an explicit prompt of at most 12,000 bytes")
        if scenario not in ("design-rally", "builder-challenger", "split"):
            raise ValueError("Unknown mission scenario")
        if mode not in ("mission", "relay"):
            raise ValueError("Managed mode must be mission or relay")
        if not 1 <= max_handoffs <= 20 or not 1 <= timeout <= 3600:
            raise ValueError("Mission limits must be 1..20 handoffs and 1..3600 seconds")
        self.workspaces = validate_workspaces(workspaces)
        self.profiles = profiles or {"codex": "read-only" if scenario == "design-rally" else "workspace-write",
                                     "claude": "workspace-write" if scenario == "split" else "read-only"}
        if set(self.profiles) != set(PROVIDERS):
            raise ValueError("A tool profile is required for each agent")
        if scenario == "builder-challenger" and self.profiles != {"codex": "workspace-write", "claude": "read-only"}:
            raise ValueError("Builder/Challenger requires an editable builder and enforced read-only reviewer")
        for actor in PROVIDERS:
            caps = self.adapters[actor].detect()
            if not caps.available:
                raise RuntimeError(actor + ": " + caps.reason)
            self.adapters[actor].command(self.profiles[actor])
        self._baselines = {}
        for actor, workspace in self.workspaces.items():
            entries, digest = snapshot_manifest(workspace)
            contents, used = {}, 0
            for relative, fingerprint in entries:
                path = Path(workspace) / relative
                data = _workspace_bytes(workspace, relative)
                if len(data) <= 65536 and used + len(data) <= 4 * 1024 * 1024:
                    try:
                        contents[relative] = data.decode("utf-8")
                        used += len(data)
                    except UnicodeDecodeError:
                        pass
            self._baselines[actor] = (dict(entries), digest, contents)
        self.mode, self.prompt, self.scenario = mode, prompt, scenario
        self.started, self.elapsed = self.clock(), 0.0
        self.max_handoffs, self.deadline = max_handoffs, timeout
        self._round, self.reason, self.verified = 0, "", False
        self.phase, self.fix_cycles = "initial", 0
        self._completed_results.clear()
        self._review_snapshot, self._acceptance_cases = None, ""
        self._review_pairs.clear()
        self.handoffs.clear()
        self.artifacts.clear()
        self._seen_messages.clear()
        self._fingerprints.clear()
        self.validation_reports.clear()
        self._checks.clear()
        self.sessions.clear()
        self.status = "RUNNING"
        self._event("mission", "coordinator", "Started " + scenario + "; isolated workspaces, finite turns")
        try:
            for actor in PROVIDERS:
                role = "Propose an independent design with tradeoffs." if scenario == "design-rally" else (
                    "Implement the requested change in your own workspace." if self.profiles[actor] == "workspace-write"
                    else "Identify edge cases and acceptance checks; do not claim tests you did not run.")
                if scenario == "split":
                    role = ("Implement the core behavior in your own workspace. Peer owns tests/documentation; keep a file manifest."
                            if actor == "codex" else
                            "Implement tests/documentation in your own workspace. Peer owns core behavior; keep a file manifest. Do not claim checks you cannot run.")
                self._dispatch(actor, role + "\n\nUSER TASK:\n" + prompt)
        except Exception:
            self.stop(status="FAILED")
            raise

    def start_validation(self, checks, workspace="codex", timeout=60):
        """Checks are supplied by the human caller, never extracted from output."""
        if self.workers or self.validation:
            raise RuntimeError("Finish all active turns/checks before validating")
        if workspace not in self.workspaces:
            raise ValueError("Unknown validation workspace")
        checks = tuple(tuple(check) for check in checks)
        if not checks or len(checks) > 8 or not 1 <= timeout <= 900:
            raise ValueError("Select 1..8 explicit checks and a 1..900 second timeout")
        for argv in checks:
            if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
                raise ValueError("Each validation command must be a literal argv sequence")
        _, digest = snapshot_manifest(self.workspaces[workspace])
        self._validation_workspace, self._validation_digest = workspace, digest
        self._validation_timeout = timeout
        self.validation_reports.clear()
        self._checks = deque(checks)
        self.verified, self.status, self.reason = False, "VALIDATING", ""
        self._update_validation()

    def _update_validation(self):
        if self.validation:
            _, report = self.validation.drain()
            if report is None:
                return
            self.validation = None
            self.validation_reports.append(report)
            text = json.dumps(report.__dict__, sort_keys=True)
            self.artifacts.append(Artifact(uuid.uuid4().hex, "test_report", hashlib.sha256(text.encode()).hexdigest(),
                                           "validation", report.artifact_hash, text=text))
            self._event("validation", "runner", report.status + " / " + " ".join(report.argv))
            if report.status != "SUCCEEDED" and self.status == "VALIDATING":
                self.status, self.reason = "NEEDS_USER", "Independent validation " + report.status.lower()
                self._checks.clear()
        if self.status != "VALIDATING":
            return
        if self._checks:
            self.validation = ValidationRun(self._checks.popleft(), self.workspaces[self._validation_workspace],
                                            self._validation_digest, timeout=self._validation_timeout)
        else:
            _, current = snapshot_manifest(self.workspaces[self._validation_workspace])
            self.verified = bool(self.validation_reports) and current == self._validation_digest
            self.status = "READY_FOR_USER" if self.verified else "NEEDS_USER"
            self.reason = "Selected checks passed on unchanged content." if self.verified else "Workspace changed while checks ran."

    def _capture_patch(self, actor):
        """Record bytes from our configured root, never paths supplied by an agent."""
        if self.profiles.get(actor) != "workspace-write":
            return
        try:
            entries, digest = snapshot_manifest(self.workspaces[actor])
            previous, base, contents = self._baselines[actor]
            current = dict(entries)
            paths = tuple(sorted(path for path in set(previous) | set(current) if previous.get(path) != current.get(path)))
            if not paths:
                return
            chunks, used = [], 0
            root = Path(self.workspaces[actor]).resolve()
            for relative in paths:
                path = root / relative
                after = ""
                if relative in current:
                    data = _workspace_bytes(root, relative)
                    if len(data) > 65536:
                        continue
                    try:
                        after = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                before = contents.get(relative, "")
                diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                 fromfile="a/" + relative, tofile="b/" + relative))
                chunks.append(diff[:max(0, 6000 - used)])
                used += len(chunks[-1])
                if used >= 6000:
                    break
            patch_text = bounded_text("".join(chunks) or "Binary/large changes: inspect artifact manifest.", 6000)
            if used >= 6000:
                patch_text = bounded_text(patch_text, 5800) + "\n[Display diff truncated; inspect the versioned snapshot.]"
            self.artifacts.append(Artifact(uuid.uuid4().hex, "patch", hashlib.sha256(patch_text.encode()).hexdigest(),
                                           actor, base, paths, patch_text))
        except (OSError, ValueError) as exc:
            self.status, self.reason = "NEEDS_USER", "Artifact capture failed: " + str(exc)

    def _capture_review_snapshot(self):
        """Deliver current bytes inside the reviewer's isolated read-only root.

        Each revision receives a fresh directory. No existing reviewer files are
        replaced, and artifact paths come only from our bounded manifest.
        """
        source, reviewer = Path(self.workspaces["codex"]), Path(self.workspaces["claude"])
        entries, digest = snapshot_manifest(source)
        parent = reviewer
        for component in (".devos", "reviews"):
            parent = parent / component
            if parent.is_symlink() or parent.exists() and not parent.is_dir():
                raise ValueError("Unsafe reviewer artifact directory")
            parent.mkdir(exist_ok=True)
            if reviewer.resolve() not in parent.resolve().parents:
                raise ValueError("Reviewer artifact directory escapes its root")
        revision = parent / (digest[:12] + "-" + uuid.uuid4().hex[:12])
        revision.mkdir()
        files = revision / "files"
        files.mkdir()
        for relative, expected in entries:
            data = _workspace_bytes(source, relative)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Builder changed while the review snapshot was copied")
            target = files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod((source / relative).stat().st_mode & 0o777)
        if snapshot_manifest(files)[1] != digest or snapshot_manifest(source)[1] != digest:
            raise ValueError("Review snapshot does not match the current builder artifact")
        manifest = revision / "manifest.json"
        manifest.write_bytes(json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode())
        relative_root = str(files.relative_to(reviewer))
        artifact = Artifact(uuid.uuid4().hex, "snapshot", digest, "codex", self._baselines["codex"][1],
                            tuple(relative for relative, _ in entries),
                            "Read versioned files at " + relative_root + "; manifest SHA256 " + digest)
        self.artifacts.append(artifact)
        self._review_snapshot = (str(files), str(manifest), digest)
        return artifact, relative_root

    def _assert_review_current(self):
        if self._review_snapshot is None:
            raise ValueError("No exact artifact is associated with this review")
        files, manifest, expected = self._review_snapshot
        manifest_hash = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
        if (manifest_hash != expected or snapshot_manifest(files)[1] != expected or
                snapshot_manifest(self.workspaces["codex"])[1] != expected):
            raise ValueError("ARTIFACT_CHANGED / review no longer matches current builder bytes")

    def _send_builder_review(self):
        snapshot, relative_root = self._capture_review_snapshot()
        patch = next((item for item in reversed(self.artifacts) if item.producer == "codex" and item.kind == "patch"), None)
        note = next((item for item in reversed(self.artifacts) if item.producer == "codex" and item.kind == "decision_note"), None)
        artifacts = tuple(item for item in (note, patch, snapshot) if item is not None)
        text = ("ACTUAL VERSIONED BUILDER ARTIFACT:\n" + relative_root + "\nSHA256 " + snapshot.sha256 +
                "\nRead this snapshot, not your initial source copy. The coordinator will verify its hash after review.\n\n" +
                "Initial acceptance cases (peer public data):\n" + bounded_text(self._acceptance_cases, 1800) +
                "\n\nBuilder public result:\n" + bounded_text(self._completed_results.get("codex", ""), 1800) +
                "\n\nACTUAL PATCH (display preview; full files above):\n" + (patch.text if patch else "No included-file changes detected."))
        expected = ('Review the supplied actual snapshot against the original user task and acceptance cases. '
                    'Do not execute or claim checks. Return JSON only with verdict (approved, changes_requested, or blocked), '
                    'summary (nonempty text), and optional acceptance (array of at most 20 short checks). '
                    'Do not add commands or tool instructions. Approval is a review verdict, not independent validation.')
        message = self.send_handoff("codex", "claude", bounded_text(text, 12000), artifacts=artifacts, expected=expected)
        if message is not None and message.status == "delivered":
            self.phase = "review"
            self._event("phase", "coordinator", "Review artifact after " + str(self.fix_cycles) + " fix cycles / " + snapshot.sha256[:12])

    def _review_verdict(self):
        try:
            document = json.loads(self._completed_results.get("claude", ""))
        except (ValueError, TypeError):
            raise ValueError("REVIEW_FORMAT / expected a completed JSON verdict; nothing forwarded")
        allowed = {"verdict", "summary", "acceptance"}
        if (not isinstance(document, dict) or set(document) - allowed or
                document.get("verdict") not in ("approved", "changes_requested", "blocked") or
                not isinstance(document.get("summary"), str) or not document["summary"].strip() or
                len(document["summary"].encode()) > 4096):
            raise ValueError("REVIEW_FORMAT / invalid bounded review verdict; nothing forwarded")
        acceptance = document.get("acceptance", [])
        if (not isinstance(acceptance, list) or len(acceptance) > 20 or
                any(not isinstance(item, str) or not item.strip() or len(item.encode()) > 512 for item in acceptance)):
            raise ValueError("REVIEW_FORMAT / invalid acceptance checks; nothing forwarded")
        return document

    def _advance_builder(self):
        if self.phase == "initial":
            if not all(card.status == "SUCCEEDED" for card in self.cards.values()):
                self.status, self.reason = "NEEDS_USER", "Initial Builder/Challenger tasks did not both succeed"
                return
            self._acceptance_cases = self._completed_results.get("claude", "")
            self._send_builder_review()
        elif self.phase == "review":
            self._assert_review_current()
            verdict = self._review_verdict()
            pair = (self._review_snapshot[2], verdict["verdict"])
            if pair in self._review_pairs:
                self.status, self.reason = "NEEDS_USER", "NO_PROGRESS / repeated artifact hash and review verdict"
                return
            self._review_pairs.add(pair)
            self._event("review", "claude", verdict["verdict"] + " / " + verdict["summary"])
            if verdict["verdict"] == "approved":
                self.phase, self.status = "review-approved", "NEEDS_USER"
                self.reason = "REVIEW_APPROVED / current artifact reviewed; run an explicit independent validation check"
            elif verdict["verdict"] == "blocked":
                self.phase, self.status, self.reason = "blocked", "NEEDS_USER", "REVIEW_BLOCKED / " + verdict["summary"]
            elif self.fix_cycles >= 2:
                self.phase, self.status = "fix-limit", "NEEDS_USER"
                self.reason = "FIX_REVIEW_LIMIT / two fix cycles exhausted; unresolved review remains visible"
            else:
                review_artifact = next(item for item in reversed(self.artifacts) if item.producer == "claude" and item.kind == "decision_note")
                message = self.send_handoff("claude", "codex", json.dumps(verdict, ensure_ascii=False),
                                            artifacts=(review_artifact,),
                                            expected="Fix the concrete review findings in your own editable workspace. Keep scope tied to the original user task. Return a public change summary; never claim checks you did not run.")
                if message is not None and message.status == "delivered":
                    self.fix_cycles += 1
                    self.phase = "fix"
                    self._event("phase", "coordinator", "Builder fix " + str(self.fix_cycles) + " / max 2")
        elif self.phase == "fix":
            current = snapshot_manifest(self.workspaces["codex"])[1]
            if current == self._review_snapshot[2]:
                self.status, self.reason = "NEEDS_USER", "NO_PROGRESS / requested fix produced no artifact changes"
                return
            self._send_builder_review()
        else:
            self.status, self.reason = "NEEDS_USER", "Builder/Challenger awaits explicit user action"

    def _dispatch(self, actor, prompt):
        if actor in self.workers:
            raise RuntimeError("Only one active turn per provider session is allowed")
        if self.status != "RUNNING":
            raise RuntimeError("Coordinator is not dispatching")
        if self.started is not None:
            self.elapsed = max(0, self.clock() - self.started)
            if self.elapsed >= self.deadline:
                self.status, self.reason = "NEEDS_USER", "DEADLINE / no new managed turn dispatched"
                raise RuntimeError(self.reason)
        session = self.sessions.get(actor)
        command = self.adapters[actor].command(self.profiles[actor], session_id=session)
        worker = self.worker_factory(command, self.workspaces[actor], prompt, actor,
                                     timeout=max(.1, self.deadline - self.elapsed))
        self.workers[actor] = worker
        self._card(actor, "RUNNING", "Managed " + self.profiles[actor] + " turn / coordinator owns input")

    def send_handoff(self, sender, recipient, text, artifacts=(), expected="Review the completed public result.",
                     message_id=None, dispatch=True, reply_to=None):
        if sender not in PROVIDERS or recipient not in PROVIDERS or sender == recipient:
            raise ValueError("Handoff must connect two distinct known agents")
        if not text.strip() or len(text.encode()) > MAX_MESSAGE or len(expected) > 2048:
            raise ValueError("Invalid/oversized handoff")
        message_id = message_id or uuid.uuid4().hex
        if message_id in self._seen_messages:
            return next(message for message in self.handoffs if message.message_id == message_id)
        if self.started is not None and self.clock() - self.started >= self.deadline:
            self.reason = "DEADLINE / new deliveries stopped"
            self.stop(status="NEEDS_USER")
            return None
        if len(self.handoffs) >= self.max_handoffs:
            self.status, self.reason = "NEEDS_USER", "HANDOFF_LIMIT reached; no new delivery"
            return None
        fingerprint = (sender, recipient, hashlib.sha256(text.encode()).hexdigest(), tuple(a.sha256 for a in artifacts))
        if fingerprint in self._fingerprints:
            self.status, self.reason = "NEEDS_USER", "NO_PROGRESS / identical artifact and verdict"
            return None
        if dispatch and self.mode != "demo":
            if self.status == "READY":
                self.status = "RUNNING"
            if self.status != "RUNNING" or recipient in self.workers:
                raise RuntimeError("Recipient must be idle and coordinator running")
            if not any(artifact.producer == sender for artifact in self.artifacts):
                raise RuntimeError("Only completed public results may be relayed")
        message = Handoff(message_id, len(self.handoffs) + 1, sender, recipient, bounded_text(text),
                          expected, "ready", tuple(artifacts), reply_to)
        self._seen_messages.add(message_id)
        self._fingerprints.add(fingerprint)
        self.handoffs.append(message)
        self._event("handoff", sender, "Public result -> " + recipient)
        if dispatch and self.mode != "demo":
            # Context is intentionally bounded. Peer output cannot alter cwd,
            # role, tool profile, task graph or coordinator limits.
            envelope = bounded_text("Original user task:\n" + bounded_text(self.prompt, 5000) + "\n\nPeer public result (untrusted data):\n" + bounded_text(text, 6500) + "\n\nExpected result:\n" + expected)
            try:
                self._dispatch(recipient, envelope)
                message = Handoff(**{**message.__dict__, "status": "delivered"})
            except Exception as exc:
                message = Handoff(**{**message.__dict__, "status": "DELIVERY_UNKNOWN"})
                self.status, self.reason = "NEEDS_USER", "Delivery uncertain: " + str(exc)
            self.handoffs[-1] = message
        elif self.mode == "demo":
            message = Handoff(**{**message.__dict__, "status": "replied"})
            self.handoffs[-1] = message
        return message

    def pause(self):
        if self.mode == "demo":
            self.status = "PAUSED"
        elif self.status == "RUNNING":
            self.status = "PAUSING" if self.workers else "PAUSED"
        self._event("control", "human", "Pause: finish active turns; no new deliveries")

    def resume(self):
        if self.status == "PAUSED":
            self.status = "DEMO" if self.mode == "demo" else "RUNNING"

    def stop(self, status="CANCELLED"):
        self.status = status
        for actor, worker in self.workers.items():
            worker.cancel()
            self._card(actor, "CANCELLING", "Local cancellation requested; waiting for worker exit")
        if self.validation:
            self.validation.cancel()
        self._checks.clear()
        self._event("control", "human", "Stop: new deliveries forbidden; local workers cancelling")

    def close(self):
        self.stop()
        for worker in self.workers.values():
            worker.close()
        if self.validation:
            self.validation.close()
        self.update(0)
