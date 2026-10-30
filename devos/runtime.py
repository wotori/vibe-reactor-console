"""Application services: workers and explicit actions, kept outside drawing/state."""

from pathlib import Path
import hashlib
import json
import time
import uuid

from .agents import AgentCoordinator, prepare_workspaces
from .presets import config_path, preferences
from .settings import SettingsStore, local_preferences, save as save_settings
from .recordings import Recorder, Replay
from .task_runner import TaskRunner
from .telemetry import DataEvent, TelemetryHub, utc_now, thaw
from .terminals import TerminalManager
from .atlas import AtlasCatalog, AtlasInspector
from .playback import (arena_document, presentation_document, PresentationSnapshot,
                       export_scene_capsule)
from .reports import export_mission_report
from .binding_replay import ReplayBindings
from .bindings import BindingConfig, BINDING_BY_ID


LIVE_ACTIONS = frozenset(("task", "handoff", "handoff-send", "event", "mission-pause", "mission-validate",
                          "arena-mode", "data-mode", "record", "terminal-start"))


class Runtime:
    def __init__(self, args, session):
        self.args, self.session = args, session
        self.started = time.monotonic()
        self.hub = self.replay = self.recorder = None
        self.replay_bindings = None
        self.terminals = TerminalManager(cwd=args.cwd)
        self.terminals.configure("left", args.terminal_command, args.cwd)
        self.terminals.configure("right", args.right_command, args.cwd)
        self.arena = AgentCoordinator(mode=session.arena_mode)
        self.tasks = TaskRunner(callback=self.submit)
        self.atlas = AtlasInspector()
        self._atlas_key = self._fixture_key = None
        self._presentation_key = self._recorded_arena_key = self._replay_parameters_key = None
        self._arena_sequence, self._event_sequence = 0, 0
        self._handoff_states = {}
        self._source_session = uuid.uuid4().hex[:12]
        self.live_snapshot = None
        self._closed = False
        self._ui_ready = False
        self.settings = SettingsStore(args.config_path, enabled=not args.no_config and not args.replay)
        session.settings_enabled = self.settings.enabled
        self._settings_retry_at = 0
        try:
            self.reset_hub()
            if args.asset_manifest:
                session.asset_manifest_expected = True
                session.atlas_inspection = self.atlas.request_asset_manifest(args.asset_manifest)
            else:
                session.asset_catalog = AtlasCatalog.from_protocol(session.protocol)
            if args.record:
                self.start_recording(args.record)
            if args.replay:
                self.replay = Replay(args.replay)
                self.replay_bindings = ReplayBindings(self.replay.events, self.replay.event_offsets,
                                                     self.replay.manifest.get("parameters", {}))
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
            self._ui_ready = True
            if getattr(args, "config_warning", ""):
                session.status_message = args.config_warning
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
                                token_env=args.source_token_env if live else None,
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

    def record_control(self, topic, payload, mode="live", offset=None):
        if not self.recorder:
            return
        self._event_sequence += 1
        stamp = utc_now()
        event = DataEvent(self._source_session + "-" + str(self._event_sequence), topic, "presentation",
                          self._source_session, self._event_sequence, mode, stamp, stamp, payload)
        self.recorder.record(event, max(0, time.monotonic() - self.recording_started) if offset is None else offset)

    def capture_presentation(self, force=False):
        if not self.recorder:
            return
        session = self.session
        parameters = preferences(session)
        paused = session.paused or session.menu_open or not self._ui_ready
        signature = json.dumps((parameters, paused), sort_keys=True)
        if force or signature != self._presentation_key:
            self.record_control("presentation.changed", presentation_document(parameters,
                                session.visual_time, session.protocol_time, paused), offset=0 if force else None)
            self._presentation_key = signature

    def capture_recorded_arena(self, arena, force=False):
        if not self.recorder:
            return
        document = arena_document(arena, include_text=True, include_paths=False)
        signature = dict(document, elapsed=0)
        key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        if force or key != self._recorded_arena_key:
            self.record_control("arena.snapshot", document, "demo" if arena.mode == "demo" else "live",
                                offset=0 if force else None)
            self._recorded_arena_key = key

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
        self.capture_presentation(force=True)
        self.capture_recorded_arena(self.arena.snapshot(), force=True)
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
        if session.screen_id == "replay" and action in LIVE_ACTIONS and not (action == "record" and self.recorder):
            session.status_message = "Replay is read-only; switch screens to control live services."
            return
        if action == "terminal-start":
            if session.terminal_focused:
                self.ensure_terminal()
        elif action == "save":
            if self.args.no_config or self.replay:
                session.status_message = "Settings saving is disabled for --no-config and Replay sessions."
                return
            path = save_settings(self.args.config_path, local_preferences(session))
            session.status_message = "Preferences saved: " + str(path)
        elif action == "source-project":
            if session.screen_id == "replay" or session.stream_view:
                session.project_error = "Switch to a private live workspace before changing the source folder."
                return
            text = session.project_text.strip()
            if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
                text = text[1:-1]
            try:
                path = Path(text).expanduser().resolve() if text else None
                if path is not None and not path.is_dir():
                    raise ValueError("Project folder does not exist or is not a directory.")
                session.sources.set_project_root(str(path) if path is not None else None)
            except (OSError, ValueError, RuntimeError) as exc:
                session.project_error = str(exc)
                return
            session.project_editor = False
            session.project_error = ""
            session.terminal_visible = session.terminal_focused = False
            session.choose_screen("cockpit")
            session.status_message = "Project source indexing / N next file / R reread / U folder" if path else "Scene sources restored / U project folder"
        elif action in ("capsule-export", "mission-export", "atlas-export"):
            parent = Path(self.args.export_dir) if self.args.export_dir else config_path().parent / "exports"
            parent.mkdir(parents=True, exist_ok=True)
            basename = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            if action == "capsule-export":
                presentation = self.replay.presentation_snapshot() if session.screen_id == "replay" and self.replay else PresentationSnapshot(
                    preferences(session), session.visual_time, session.protocol_time, session.paused)
                path = parent / (basename + ".capsule.json")
                export_scene_capsule(path, presentation, seed=session.seed if session.seed is not None else 42,
                                     bookmarks=self.replay.bookmarks if self.replay else ())
            elif action == "mission-export":
                snapshot = self.replay.arena_snapshot() if session.screen_id == "replay" and self.replay else self.arena.snapshot()
                path = export_mission_report(parent / (basename + "-mission"), snapshot)
            else:
                text = session.selected_atlas_context
                if not text or session.stream_view:
                    raise ValueError("Select and inspect readable Atlas content before export.")
                path = parent / (basename + "-context.txt")
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(text)
            session.status_message = "Exported: " + str(path)
        elif action == "atlas-select":
            self._atlas_key = None
            self.update_atlas(force=True)
        elif action == "atlas-refresh":
            self._atlas_key = None
            if session.atlas_view == "assets" and self.args.asset_manifest:
                session.asset_catalog = None
                session.atlas_inspection = self.atlas.request_asset_manifest(self.args.asset_manifest)
            else:
                self.update_atlas(force=True)
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
                self.capture_presentation()
                self.record_control("recording.stopped", {"reason": "user"})
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
                    if action == "handoff-send":
                        session.prepared_context = ""
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
            if action == "replay-bookmark" and self.recorder and not self.replay:
                self.recorder.bookmark(offset_seconds=time.monotonic() - self.recording_started)
                session.status_message = "Bookmark added to the active recording."
            elif not self.replay:
                session.status_message = "Open an event recording using --replay FILE."
            elif action == "replay-play":
                self.replay.playing = not self.replay.playing
            elif action == "replay-next":
                self.replay.next_failure()
            elif action in ("replay-speed-up", "replay-speed-down"):
                self.replay.cycle_speed(1 if action == "replay-speed-up" else -1)
            elif action == "replay-bookmark":
                self.replay.add_bookmark()
            elif action == "replay-next-bookmark":
                self.replay.next_bookmark()
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
        self.capture_recorded_arena(session.arena)
        if self.replay:
            if session.screen_id == "replay":
                if not session.menu_open:
                    self.replay.update(dt)
                presentation = self.replay.presentation_snapshot()
                parameters = thaw(presentation.parameters)
                signature = json.dumps(parameters, sort_keys=True, default=dict)
                if signature != self._replay_parameters_key:
                    session.apply_preferences({key: value for key, value in parameters.items()
                                               if key not in ("screen", "data_mode", "arena_mode")})
                    self._replay_parameters_key = signature
                session.replay_presentation = presentation
                session.visual_time, session.protocol_time = presentation.visual_time, presentation.protocol_time
                session.replay_arena = session.arena = self.replay.arena_snapshot()
                session.publish_telemetry(self.replay.telemetry_snapshot(), dt, timeline=self.replay.playhead)
                recorded_config = (BindingConfig.from_dict(parameters["binding_config"]) if "binding_config" in parameters
                                   else BINDING_BY_ID[parameters.get("lab_binding", "online")].config)
                override = session.current_binding_config if session.current_binding_config != recorded_config else None
                session.binding_frame = self.replay_bindings.seek(self.replay.playhead, config=override,
                                                                 stream_view=session.stream_view)
                session.replay_bookmarks = self.replay.bookmarks
            else:
                session.publish_telemetry(self.live_snapshot, dt, timeline=self.live_snapshot.elapsed)
            session.replay_status = self.replay.snapshot()
        else:
            session.publish_telemetry(self.live_snapshot, dt, timeline=self.live_snapshot.elapsed)
        self.update_atlas()
        self.capture_presentation()
        session.task_snapshot = self.tasks.snapshot()
        if self.recorder:
            session.recording_status = self.recorder.snapshot()
        session.terminal_slots = self.terminals.snapshots()
        session.terminal_frame = self.terminals.snapshot(session.terminal_slot)
        self.persist_settings()

    def persist_settings(self, force=False):
        if not self._ui_ready or self.replay:
            return
        now = time.monotonic()
        if not force and now < self._settings_retry_at:
            return
        try:
            self.settings.update(self.session, force=force)
            self._settings_retry_at = 0
        except (OSError, ValueError, TypeError) as exc:
            self._settings_retry_at = now + 5
            self.session.status_message = "Could not save settings: " + str(exc)

    def update_atlas(self, force=False):
        session = self.session
        current = self.atlas.snapshot()
        if session.asset_manifest_expected:
            if current.catalog is not None and current.state == "ready":
                session.asset_catalog = current.catalog
        else:
            frame = session.protocol
            key = (frame.identity, frame.revision, frame.consumer.project_id, frame.profile, frame.stage)
            if key != self._fixture_key:
                session.asset_catalog = AtlasCatalog.from_protocol(frame)
                self._fixture_key = key
        if session.screen_id == "atlas" or force:
            selected = session.atlas_selected
            if selected is not None:
                if session.atlas_view == "repository":
                    repo = session.telemetry.repository
                    key = ("repo", repo.root, selected.path, id(repo))
                    if key != self._atlas_key:
                        current = self.atlas.request_repo(repo.root, selected.path)
                        self._atlas_key = key
                else:
                    key = ("asset", id(session.asset_catalog), selected.id)
                    if key != self._atlas_key:
                        current = self.atlas.request_node(session.asset_catalog, selected.id)
                        self._atlas_key = key
        session.atlas_inspection = current

    def capture_arena_events(self, arena=None):
        arena = arena or self.arena.snapshot()
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
        self.persist_settings(force=True)
        self.tasks.close()
        final_arena = self.arena.snapshot()
        self.arena.close()
        if final_arena.status in ("RUNNING", "PAUSING", "PAUSED", "READY", "DEMO"):
            final_arena = self.arena.snapshot()
        self.capture_arena_events(final_arena)
        self.capture_recorded_arena(final_arena)
        self.terminals.close()
        self.atlas.close()
        self.session.sources.close()
        if self.hub:
            # Jobs may publish their final cancellation/exit event during close.
            # Drain those accepted events while the recorder is still alive.
            self.hub.update(0, demo_paused=True)
            self.hub.close()
        if self.recorder:
            self.record_control("recording.stopped", {"reason": "exit"})
            self.recorder.close()
        if self.replay:
            self.replay.close()
