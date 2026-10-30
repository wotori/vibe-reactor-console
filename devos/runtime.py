"""Application services: workers and explicit actions, kept outside drawing/state."""

from pathlib import Path
import time
import uuid

from .agents import AgentCoordinator, prepare_workspaces
from .presets import config_path, preferences, save
from .recordings import Recorder, Replay
from .task_runner import TaskRunner
from .telemetry import DataEvent, TelemetryHub, utc_now
from .terminals import TerminalManager


LIVE_ACTIONS = frozenset(("task", "handoff", "handoff-send", "event", "mission-pause", "mission-validate",
                          "arena-mode", "data-mode", "record", "terminal-start"))


class Runtime:
    def __init__(self, args, session):
        self.args, self.session = args, session
        self.started = time.monotonic()
        self.hub = self.replay = self.recorder = None
        self.terminals = TerminalManager(cwd=args.cwd)
        self.terminals.configure("left", args.terminal_command, args.cwd)
        self.terminals.configure("right", args.right_command, args.cwd)
        self.arena = AgentCoordinator(mode=session.arena_mode)
        self.tasks = TaskRunner(callback=self.submit)
        self._arena_sequence, self._event_sequence = 0, 0
        self._handoff_states = {}
        self._source_session = uuid.uuid4().hex[:12]
        self.live_snapshot = None
        self._closed = False
        try:
            self.reset_hub()
            if args.record:
                self.start_recording(args.record)
            if args.replay:
                self.replay = Replay(args.replay)
                session.choose_screen("replay")
                session.seed = self.replay.snapshot().seed
                parameters = {key: value for key, value in self.replay.manifest.get("parameters", {}).items()
                              if key not in ("data_mode", "arena_mode")}
                session.apply_preferences(parameters)
                session.choose_screen("replay")
                # A replay manifest is presentation data, never a control message.
                session.pending_actions.clear()
            if args.mission:
                workspaces = {"codex": args.workspace_left, "claude": args.workspace_right}
                if args.prepare_workspaces:
                    workspaces = prepare_workspaces(args.cwd or Path.cwd(), args.prepare_workspaces,
                                                    mode=args.workspace_mode)
                self.arena.start_mission(args.mission, workspaces, scenario=args.scenario,
                                         max_handoffs=args.handoffs, timeout=args.mission_timeout,
                                         mode=args.agents)
                session.choose_screen("arena")
            if args.terminal or args.terminal_command:
                session.handle("terminal")
                self.ensure_terminal()
            if args.right_command:
                self.terminals.create("right")
        except Exception:
            self.close()
            raise

    def reset_hub(self):
        if self.hub:
            self.hub.close()
        args, mode = self.args, self.session.data_mode
        live = mode != "demo"
        self.hub = TelemetryHub(seed=self.session.seed if self.session.seed is not None else 42,
                                source_file=args.source_file if live else None,
                                source_url=args.source_url if live else None,
                                source_stream=args.source_stream if live else None,
                                heart_file=args.heart_file if live else None,
                                git_root=args.git_root, demo=mode != "live")
        self.hub.add_listener(self.record_event)

    def record_event(self, event, elapsed=0):
        if self.recorder:
            self.recorder.record(event, time.monotonic() - self.recording_started)

    def submit(self, event):
        return self.hub.submit(event) if self.hub else False

    def event(self, topic, payload, mode="live"):
        self._event_sequence += 1
        stamp = utc_now()
        self.submit(DataEvent(self._source_session + "-" + str(self._event_sequence), topic, "workspace",
                              self._source_session, self._event_sequence, mode, stamp, stamp, payload))

    def start_recording(self, path=None):
        if self.recorder:
            raise RuntimeError("A recording is already active")
        if path is None:
            directory = config_path().parent / "recordings"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".jsonl")
        self.recording_started = time.monotonic()
        self.recorder = Recorder(path, seed=self.session.seed if self.session.seed is not None else 42,
                                 parameters=preferences(self.session), include_pulse=self.args.record_pulse)
        self.session.status_message = "Recording started: " + str(path)

    def ensure_terminal(self):
        session = self.session
        while "terminal-start" in session.pending_actions:
            session.pending_actions.remove("terminal-start")
        if session.screen_id == "replay" or not session.terminal_visible or session.stream_view:
            session.terminal_focused = False
            return
        slot = session.terminal_slot
        self.terminals.focus(slot)
        try:
            frame = self.terminals.snapshot(slot)
            if frame.dead and session.terminal_focused:
                self.terminals.close(slot)
            self.terminals.create(slot)
        except (OSError, RuntimeError) as exc:
            session.status_message = str(exc)
            session.terminal_focused = False
        session.terminal_frame = self.terminals.snapshot(slot)

    def action(self, action):
        session = self.session
        if session.screen_id == "replay" and action in LIVE_ACTIONS:
            session.status_message = "Replay is read-only; switch screens to control live services."
            return
        if action == "terminal-start":
            if session.terminal_focused:
                self.ensure_terminal()
        elif action == "save":
            path = save(self.args.config_path, preferences(session))
            session.status_message = "Preferences saved: " + str(path)
        elif action == "data-mode":
            self.reset_hub()
        elif action == "arena-mode":
            if self.arena.mode in ("mission", "relay") and self.arena.status not in (
                    "CANCELLED", "FAILED", "READY_FOR_USER", "NEEDS_USER"):
                session.arena_mode = self.arena.mode
                session.status_message = "Stop the active managed mission before changing Arena mode."
                return
            self.arena.close()
            self.arena = AgentCoordinator(mode=session.arena_mode)
            self._arena_sequence = 0
            self._handoff_states.clear()
        elif action == "record":
            if self.recorder:
                self.recorder.close()
                session.recording_status = self.recorder.snapshot()
                self.recorder = None
                session.status_message = "Recording stopped."
            else:
                self.start_recording()
        elif action == "event":
            if session.data_mode != "live":
                self.hub.update(3.0)
                self.arena.step_demo() if self.arena.mode == "demo" else None
        elif action in ("handoff", "handoff-send"):
            if self.arena.mode == "demo":
                self.arena.step_demo()
            elif self.arena.mode == "watch":
                session.status_message = "Watch: terminals are human-owned; no automatic relay."
            else:
                sender = "codex" if session.terminal_slot == "left" else "claude"
                recipient = "claude" if sender == "codex" else "codex"
                card = next((item for item in self.arena.snapshot().cards if item.id == sender), None)
                if card and card.status == "SUCCEEDED" and card.summary:
                    text = session.composer_text if action == "handoff-send" else card.summary
                    self.arena.send_handoff(sender, recipient, text)
                else:
                    session.status_message = "Wait for a completed public reply before handing it off."
        elif action == "mission-pause":
            if self.arena.status == "PAUSED":
                self.arena.resume()
            else:
                self.arena.pause()
        elif action == "mission-stop":
            self.arena.stop()
        elif action == "mission-validate":
            if not self.args.task:
                session.status_message = "Configure an independent check using --task 'command arguments'."
            else:
                self.arena.start_validation([self.args.task], workspace=(
                    "codex" if session.terminal_slot == "left" else "claude"))
                session.status_message = "Validating selected isolated workspace against its content hash."
        elif action == "task":
            if self.args.task:
                self.tasks.start(self.args.task, cwd=self.args.cwd)
                session.status_message = "Running configured task; result uses actual exit code."
            else:
                session.status_message = "Supply --task 'command arguments' to configure a check."
        elif action.startswith("replay-"):
            if not self.replay:
                session.status_message = "Open an event recording using --replay FILE."
            elif action == "replay-play":
                self.replay.playing = not self.replay.playing
            elif action == "replay-next":
                self.replay.next_failure()
            else:
                self.replay.seek(self.replay.playhead + (-5 if action == "replay-back" else 5))

    def update(self, dt):
        session = self.session
        while session.pending_actions:
            action = session.pending_actions.popleft()
            try:
                self.action(action)
            except (OSError, ValueError, RuntimeError) as exc:
                session.status_message = str(exc)
        paused = session.paused or session.menu_open
        self.live_snapshot = self.hub.update(dt, demo_paused=paused)
        # Managed workers continue draining while menus are open; only Demo pauses.
        if self.arena.mode != "demo" or not paused:
            self.arena.update(dt)
        session.arena = self.arena.snapshot()
        self.capture_arena_events()
        if self.replay:
            if session.screen_id == "replay":
                if not session.menu_open:
                    self.replay.update(dt)
                session.publish_telemetry(self.replay.telemetry_snapshot())
                session.visual_time = self.replay.playhead
                session.protocol_time = self.replay.playhead
            else:
                session.publish_telemetry(self.live_snapshot)
            session.replay_status = self.replay.snapshot()
        else:
            session.publish_telemetry(self.live_snapshot)
        session.task_snapshot = self.tasks.snapshot()
        if self.recorder:
            session.recording_status = self.recorder.snapshot()
        session.terminal_slots = self.terminals.snapshots()
        session.terminal_frame = self.terminals.snapshot(session.terminal_slot)

    def capture_arena_events(self):
        arena = self.arena.snapshot()
        for message in arena.handoffs:
            previous = self._handoff_states.get(message.message_id)
            if previous != message.status:
                self.event("agent.handoff.ready" if previous is None else "agent.handoff.updated", {
                    "message_id": message.message_id, "handoff_sequence": message.sequence,
                    "from": message.sender, "to": message.recipient, "summary": message.summary,
                    "status": message.status, "expected_result": message.expected_result,
                    "artifacts": [getattr(item, "id", str(item)) for item in message.artifacts]},
                    "demo" if arena.mode == "demo" else "live")
                self._handoff_states[message.message_id] = message.status
        for event in arena.events:
            if event.sequence > self._arena_sequence:
                self.event("arena." + event.kind, {"actor": event.actor, "text": event.text},
                           "demo" if arena.mode == "demo" else "live")
                self._arena_sequence = event.sequence

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.tasks.close()
        self.arena.close()
        self.capture_arena_events()
        self.terminals.close()
        if self.hub:
            # Jobs may publish their final cancellation/exit event during close.
            # Drain those accepted events while the recorder is still alive.
            self.hub.update(0, demo_paused=True)
            self.hub.close()
        if self.recorder:
            self.recorder.close()
        if self.replay:
            self.replay.close()
