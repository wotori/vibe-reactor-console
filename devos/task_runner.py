"""Explicit, bounded local command execution with honest exit-code results."""

from dataclasses import dataclass, replace
import os
import selectors
import signal
import subprocess
import threading
import time
import uuid
from typing import Optional, Tuple

from .repository import run_bounded
from .telemetry import DataEvent, utc_now


@dataclass(frozen=True)
class TaskSnapshot:
    run_id: str = ""
    argv: Tuple[str, ...] = ()
    cwd: str = ""
    state: str = "idle"
    exit_code: Optional[int] = None
    elapsed_seconds: float = 0.0
    output: str = ""
    truncated: bool = False
    error: str = ""
    mode: str = "live"
    revision: str = "--"


class TaskRunner:
    """A task starts only through start(argv), never by reading an event/preset."""

    def __init__(self, callback=None, output_limit=65536):
        self.callback, self.output_limit = callback, max(1024, int(output_limit))
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._snapshot, self._thread, self._process = TaskSnapshot(), None, None
        self._session, self._sequence = "task-runner-" + uuid.uuid4().hex[:12], 0

    def start(self, argv, cwd=None, timeout=300.0):
        if isinstance(argv, str) or not argv or any(not isinstance(item, str) or "\0" in item for item in argv):
            raise ValueError("Task command must be a nonempty argv sequence")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("A task is already running")
            self._stop.clear()
            self._snapshot = TaskSnapshot(uuid.uuid4().hex[:12], tuple(argv), os.path.abspath(cwd or os.getcwd()), "running")
            self._thread = threading.Thread(target=self._run, args=(max(0.05, float(timeout)),),
                                            name="devos-task", daemon=True)
            self._thread.start()
        return self._snapshot.run_id

    def _event(self, topic, payload):
        self._sequence += 1
        event = DataEvent(self._session + "-" + str(self._sequence), topic, "task-runner",
                          self._session, self._sequence, "live", utc_now(), utc_now(), payload)
        if self.callback:
            try:
                accepted = self.callback(event)
                if accepted is False:
                    with self._lock:
                        self._snapshot = replace(self._snapshot, error="BACKPRESSURE: task event was not accepted")
            except Exception:
                with self._lock:
                    self._snapshot = replace(self._snapshot, error="Task event callback failed")

    def _terminate(self, process):
        # Descendants can keep the stdout pipe open even after the leader exits.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=0.3)

    def _run(self, timeout):
        with self._lock:
            initial = self._snapshot
        try:
            revision = run_bounded(("git", "--no-optional-locks", "rev-parse", "--verify", "HEAD"),
                                   cwd=initial.cwd, timeout=1.0, limit=4096)
            if revision.returncode == 0:
                initial = replace(initial, revision=revision.output.strip()[:64])
                with self._lock:
                    self._snapshot = initial
        except OSError:
            pass
        self._event("task.test.started", {"run_id": initial.run_id, "argv": initial.argv,
                                          "revision": initial.revision,
                                          "text": "Explicit command started; test counts unknown"})
        started, output, truncated, state, error = time.monotonic(), bytearray(), False, "completed", ""
        process, selector = None, selectors.DefaultSelector()
        try:
            process = subprocess.Popen(initial.argv, cwd=initial.cwd, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            with self._lock:
                self._process = process
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                elapsed = time.monotonic() - started
                if self._stop.is_set() or elapsed >= timeout:
                    state = "cancelled" if self._stop.is_set() else "timed_out"
                    self._terminate(process)
                for key, _ in selector.select(0.03):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    allowed = max(0, self.output_limit - len(output))
                    output.extend(chunk[:allowed])
                    truncated = truncated or len(chunk) > allowed
                with self._lock:
                    text = output.decode("utf-8", "replace")
                    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 and ord(ch) != 127 else "?" for ch in text)
                    self._snapshot = replace(initial, elapsed_seconds=elapsed, output=text, truncated=truncated)
            code = process.wait(timeout=0.5)
        except Exception as failure:
            state, code, error = "error", None, type(failure).__name__ + ": " + str(failure)[:160]
            if process:
                self._terminate(process)
        finally:
            selector.close()
            if process and process.stdout:
                process.stdout.close()
            with self._lock:
                self._process = None
        self._event("task.test.completed", {"run_id": initial.run_id, "exit_code": code,
                                            "revision": initial.revision,
                                            "state": state, "result": "passed" if state == "completed" and code == 0 else "failed",
                                            "text": "Exit code only; test counts and coverage unknown"})
        with self._lock:
            self._snapshot = replace(self._snapshot, state=state, exit_code=code,
                                     elapsed_seconds=time.monotonic() - started,
                                     error=error or self._snapshot.error)

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def cancel(self):
        self._stop.set()

    def close(self):
        self.cancel()
        if self._thread:
            self._thread.join(timeout=1.5)
