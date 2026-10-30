"""Screen layouts and strict console rendering over frozen source snapshots."""

from dataclasses import replace
from types import MappingProxyType
import unittest

from devos.agents import AgentCard, ArenaSnapshot, Handoff
from devos.recordings import RecordingStatus, ReplayStatus
from devos.repository import RepoFile, RepoSnapshot
from devos.screen_registry import SCREENS, SCREEN_BY_ID, render_screen, screen_layout
from devos.session import Session
from devos.telemetry import DataEvent, MetricSample, ProviderStatus, TelemetryHub, TelemetrySnapshot
from devos.terminal import Canvas

from tests.test_rendering import Screen


class PreviewRenderer:
    """No subprocess/curses calls; source pixels must fit a strict screen."""
    def __init__(self, screen):
        self.canvas = Canvas(screen, color=False)
        self.canvas.configure("neon", "terminal")
        self.calls = []

    def cockpit(self, session, rows, cols):
        self.calls.append(("cockpit", rows, cols))

    def shell(self, session, rect, slot=None):
        self.calls.append(("shell", slot, rect))
        p = self.canvas.panel(rect, "REAL TERMINAL / " + slot.upper())
        p.put(0, 0, "ONLY INPUT OWNER RECEIVES KEYS", "main")


def sample(key, value, unit, quality="ok", mode="live", window=None):
    return MetricSample(key, value, unit, "fixture", "run-1", 1, None, 0, mode,
                        quality=quality, age_seconds=.8, window_seconds=window)


def fixture_session():
    session = Session(seed=42, animation="gravity-well")
    session.screen_id, session.focused_panel, session.zoomed = "cockpit", None, False
    session.lab_binding, session.intensity = "online", "cinematic"
    session.terminal_slots, session.terminal_slot = (), "left"
    session.terminal_visible, session.terminal_focused = False, False
    session.atlas_view, session.status_message = "assets", ""
    metrics = (sample("site.active_sessions_60s", 28, "sessions", window=60),
               sample("site.pageviews_per_min", 84, "views/min", window=60),
               sample("body.heart_rate", 72, "bpm"))
    event = DataEvent("event-1", "site.pages", "fixture", "run-1", 2, "live", None,
                      "2026-10-31T12:00:00Z", {"pages": ()})
    repository = RepoSnapshot("/fixture", "main", "abc123", (
        RepoFile("devos/session.py", " M"), RepoFile("tests/test_screens.py", "??"),
        RepoFile("README.md")), 2, "ok")
    session.telemetry = TelemetrySnapshot(metrics=metrics, events=(event,), elapsed=12,
        pages=(("/", 9), ("/ekza", 12), ("/about", 7)), rr_intervals=(830, 840, 826, 833),
        heart_contact="good", heart_timing_basis="notification_received", repository=repository)
    cards = (AgentCard("codex", "Codex", "implementer", "RUNNING", "Implement exact asset byte validation."),
             AgentCard("claude", "Claude", "reviewer", "WAITING", "Prepare boundary cases."))
    handoff = Handoff("handoff-1", 1, "codex", "claude", "Review the immutable revision patch.", "List boundary failures.")
    session.arena = ArenaSnapshot("demo", "RUNNING", cards, (), (handoff,), (), 12, 6, 120)
    session.replay_status = ReplayStatus("fixture.jsonl", 4, 12, True, 2, 20, 42)
    session.recording_status = RecordingStatus("fixture.jsonl", False, 20, 2048)
    session.visual_parameters = lambda: MappingProxyType({"energy": .6, "particle_count": 90, "pulse_bpm": 72})
    return session


class ScreenLayoutTests(unittest.TestCase):
    def test_seven_screens_are_registered_with_lazy_renderers(self):
        self.assertEqual(tuple(item.id for item in SCREENS),
                         ("cockpit", "visual-lab", "arena", "site", "body", "atlas", "replay"))
        self.assertEqual(len(SCREEN_BY_ID), 7)
        self.assertTrue(all(callable(item.render) and item.panels for item in SCREENS))

    def test_layouts_fit_and_never_overlap_through_focus_zoom_and_resize(self):
        for spec in SCREENS:
            for rows, cols in ((16, 48), (24, 80), (24, 100), (36, 120), (44, 160), (60, 240)):
                for focus in (None,) + spec.panels:
                    for zoomed in (False, True):
                        with self.subTest(screen=spec.id, size=(rows, cols), focus=focus, zoom=zoomed):
                            cells = set()
                            for identifier, rect in screen_layout(spec.id, rows, cols, focus, zoomed):
                                self.assertIn(identifier, spec.panels)
                                self.assertGreaterEqual(rect.width, 5)
                                self.assertGreaterEqual(rect.height, 3)
                                self.assertGreaterEqual(rect.y, 3)
                                self.assertLessEqual(rect.y + rect.height, rows - 1)
                                self.assertGreaterEqual(rect.x, 1)
                                self.assertLess(rect.x + rect.width, cols)
                                occupied = {(x, y) for y in range(rect.y, rect.y + rect.height)
                                            for x in range(rect.x, rect.x + rect.width)}
                                self.assertFalse(cells & occupied)
                                cells |= occupied

    def test_compact_arena_reveals_selected_pty_and_wide_arena_has_both(self):
        narrow = screen_layout("arena", 24, 80, "terminal-right")
        self.assertIn("terminal-right", dict(narrow))
        self.assertNotIn("terminal-left", dict(narrow))
        wide = dict(screen_layout("arena", 44, 160))
        self.assertIn("terminal-left", wide)
        self.assertIn("terminal-right", wide)
        self.assertEqual(screen_layout("arena", 10, 40), ())


class ConsoleScreenTests(unittest.TestCase):
    def draw(self, session, rows=44, cols=160):
        screen = Screen(rows, cols)
        renderer = PreviewRenderer(screen)
        render_screen(renderer, session, rows, cols)
        return screen.text(), renderer

    def test_all_console_views_draw_frozen_snapshots_after_resize(self):
        session = fixture_session()
        before = (session.sim.elapsed, session.sim.rng.getstate(), session.telemetry, session.arena,
                  session.replay_status, dict(session.visual_parameters()))
        for spec in SCREENS:
            session.screen_id = spec.id
            for rows, cols in ((16, 48), (24, 80), (36, 120), (44, 160), (60, 240)):
                with self.subTest(screen=spec.id, size=(rows, cols)):
                    text, renderer = self.draw(session, rows, cols)
                    if spec.id == "cockpit":
                        self.assertEqual(renderer.calls, [("cockpit", rows, cols)])
                    else:
                        self.assertIn(spec.title, text)
                        self.assertIn("INPUT:", text)
                    self.assertEqual(before, (session.sim.elapsed, session.sim.rng.getstate(), session.telemetry,
                        session.arena, session.replay_status, dict(session.visual_parameters())))

    def test_every_panel_is_renderable_in_zoom(self):
        session = fixture_session()
        session.zoomed = True
        for spec in SCREENS[1:]:
            session.screen_id = spec.id
            for panel in spec.panels:
                session.focused_panel = panel
                if panel.startswith("terminal-"):
                    session.terminal_slot = panel.rsplit("-", 1)[1]
                for rows, cols in ((16, 48), (44, 160)):
                    self.draw(session, rows, cols)

    def test_site_counters_keep_units_window_and_source_quality(self):
        session = fixture_session()
        session.screen_id = "site"
        text, _ = self.draw(session)
        for label in ("ACTIVE SESSIONS / 60s", "28", "VIEWS / MIN", "84", "AGGREGATES", "source unknown", "sessions"):
            self.assertIn(label, text)
        sample0 = replace(session.telemetry.metrics[0], value=None, quality="unavailable")
        session.telemetry = replace(session.telemetry, metrics=(sample0,))
        text, _ = self.draw(session)
        self.assertIn("LIVE / UNAVAILABLE", text)
        self.assertNotIn("DEMO", text)

    def test_body_bpm_is_not_presented_as_ecg_and_rr_does_not_appear_from_bpm(self):
        session = fixture_session()
        session.screen_id = "body"
        text, _ = self.draw(session)
        self.assertIn("72 BPM", text)
        self.assertIn("NOT ECG", text)
        self.assertIn("MEASURED RR INTERVALS", text)
        session.telemetry = replace(session.telemetry, rr_intervals=())
        text, _ = self.draw(session)
        self.assertIn("NO RR SAMPLES", text)
        heart = replace(session.telemetry.metrics[-1], value=None, quality="unavailable")
        session.telemetry = replace(session.telemetry, metrics=(heart,))
        text, _ = self.draw(session)
        self.assertIn("-- BPM", text)
        self.assertIn("NO SIGNAL / NEUTRAL IDLE", text)

    def test_repository_edges_are_labelled_and_fixture_is_separate(self):
        session = fixture_session()
        session.screen_id = "atlas"
        text, _ = self.draw(session)
        self.assertIn("DEMO FIXTURE", text)
        session.atlas_view = "repository"
        text, _ = self.draw(session)
        self.assertIn("DIRECTORY EDGES / REAL GIT SNAPSHOT", text)
        self.assertIn("session.py", text)
        self.assertNotIn("DEMO FIXTURE", text)

    def test_all_sources_missing_is_an_explicit_usable_state(self):
        session = fixture_session()
        session.telemetry = session.arena = session.replay_status = session.recording_status = None
        for spec in SCREENS[1:]:
            session.screen_id = spec.id
            text, _ = self.draw(session)
            self.assertIn("NO SOURCE", text)
            self.assertNotIn("LIVE / OK", text)

    def test_no_measurements_does_not_make_live_provider_demo(self):
        session = fixture_session()
        session.screen_id = "site"
        session.telemetry = TelemetrySnapshot(providers=(ProviderStatus("http", "live"),))
        text, _ = self.draw(session)
        self.assertNotIn("DEMO", text)
        self.assertIn("LIVE", text)

    def test_stream_view_masks_private_signals_paths_messages_and_status(self):
        session = fixture_session()
        session.stream_view = True
        session.status_message = "/Users/private/workspace"
        for screen_id in ("visual-lab", "arena", "site", "body", "atlas", "replay"):
            session.screen_id = screen_id
            session.lab_binding = "pulse"
            session.atlas_view = "repository"
            text, _ = self.draw(session)
            for private in ("72 BPM", "830", "/ekza", "/about", "session.py", "/Users/private",
                            "Review the immutable revision patch", "exact asset byte validation"):
                self.assertNotIn(private, text)
            self.assertIn("STREAM VIEW", text)

    def test_stream_view_visual_does_not_encode_a_private_bpm_in_intensity(self):
        session = fixture_session()
        del session.visual_parameters  # Exercise the actual Session binding engine.
        session.screen_id, session.lab_binding, session.stream_view = "visual-lab", "pulse", True
        before, _ = self.draw(session)
        metrics = tuple(replace(item, value=140) if item.key == "body.heart_rate" else item
                        for item in session.telemetry.metrics)
        session.telemetry = replace(session.telemetry, metrics=metrics)
        after, _ = self.draw(session)
        self.assertEqual(before, after)

    def test_expired_online_sample_hides_retained_page_counts_and_activity_clouds(self):
        clock = [0.0]
        hub = TelemetryHub(demo=False, start_workers=False, clock=lambda: clock[0])
        self.addCleanup(hub.close)
        hub.submit(DataEvent("online-1", "metric.sample", "fixture", "run-1", 1, "live", None,
            "2026-10-31T12:00:00Z", {"key": "site.active_sessions_60s", "value": 3,
                                      "unit": "sessions", "window_seconds": 60, "quality": "ok"}))
        hub.submit(DataEvent("pages-2", "site.pages", "fixture", "run-1", 2, "live", None,
            "2026-10-31T12:00:00Z", {"pages": [{"path": "/active-page", "active_sessions": 3}]}))
        session = fixture_session()
        session.screen_id, session.telemetry = "site", hub.update()
        text, _ = self.draw(session)
        self.assertIn("/active-page", text)
        clock[0] = 61
        session.telemetry = hub.snapshot()
        self.assertIsNone(session.telemetry.metric("site.active_sessions_60s").value)
        self.assertTrue(session.telemetry.pages, "History must remain available for inspection")
        text, _ = self.draw(session)
        self.assertNotIn("/active-page", text)
        self.assertNotIn("1 dot ~", text)
        self.assertIn("CURRENT PAGE ACTIVITY UNAVAILABLE", text)
        self.assertIn("UNAVAILABLE", text)

    def test_lab_describes_implemented_transform_and_site_uses_actual_source_flags(self):
        session = fixture_session()
        session.screen_id = "visual-lab"
        text, _ = self.draw(session)
        self.assertIn("log1p / clamped density", text)
        self.assertNotRegex(text, r"\bEMA\b")
        session.screen_id, session.telemetry = "site", None
        text, _ = self.draw(session)
        self.assertIn("--source-file", text)
        self.assertIn("--source-url", text)


if __name__ == "__main__":
    unittest.main()
