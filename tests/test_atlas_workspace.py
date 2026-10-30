"""Actual Runtime Atlas flows: workers, selected local evidence and opt-in export."""

from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devos.cli import parser, runtime_options
from devos.presets import preferences
from devos.renderer import Renderer
from devos.runtime import Runtime
from devos.session import Session
from devos.sources import SourceProvider
from devos.terminal import Canvas
from tests.test_atlas import manifest_for
from tests.test_rendering import Screen


class AtlasWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        # None of these normal inspection/context actions may launch a job.
        for target in ("devos.agents.AgentCoordinator.start_mission", "devos.terminals.TerminalManager.create",
                       "devos.task_runner.TaskRunner.start"):
            guard = patch(target, side_effect=AssertionError("Inspection unexpectedly launched a job"))
            guard.start()
            self.addCleanup(guard.stop)

    def git(self, *arguments):
        return subprocess.run(("git",) + arguments, cwd=self.root, capture_output=True, check=True)

    def repository(self):
        self.git("init", "-q")
        self.git("config", "user.name", "DEVOS Runtime Fixture")
        self.git("config", "user.email", "runtime@example.invalid")
        (self.root / "alpha.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "beta.py").write_text("other = 'initial'\n", encoding="utf-8")
        self.git("add", "alpha.py", "beta.py")
        self.git("commit", "-qm", "fixture")
        (self.root / "alpha.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "alpha.py")
        (self.root / "alpha.py").write_text("value = 3\n", encoding="utf-8")

    def start(self, *arguments):
        args = parser().parse_args(["--no-config", "--bundled", "--screen", "atlas", "--agents", "watch",
            "--data-mode", "live", "--cwd", str(self.root)] + list(arguments))
        session = Session(sources=SourceProvider(bundled=True), **runtime_options(args))
        runtime = Runtime(args, session)
        self.addCleanup(runtime.close)
        return runtime, session

    def wait_until(self, runtime, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            runtime.update(0)
            if predicate(runtime.session):
                return runtime.session.atlas_inspection
            time.sleep(.005)
        self.fail("Runtime did not publish the expected local Atlas evidence; last state: " +
                  repr(runtime.session.atlas_inspection))

    def open_repository(self):
        self.repository()
        runtime, session = self.start("--git-root", str(self.root))
        self.wait_until(runtime, lambda item: item.telemetry.repository is not None)
        session.handle("atlas")  # D chooses repository mode; no worker is started by drawing.
        self.wait_until(runtime, lambda item: item.atlas_inspection.state == "ready" and
            item.atlas_inspection.path == "alpha.py")
        return runtime, session

    def render(self, session):
        screen = Screen(44, 160)
        Renderer(Canvas(screen, color=False)).draw(session)
        return screen.text()

    def test_repository_worker_selection_refresh_and_context_remain_read_only(self):
        runtime, session = self.open_repository()
        original_index = (self.root / ".git" / "index").read_bytes()
        inspection = session.atlas_inspection
        self.assertEqual((inspection.origin, inspection.mode, inspection.source_text), ("git", "live", "value = 3\n"))
        self.assertIn("+value = 2", inspection.staged_diff)
        self.assertIn("-value = 2", inspection.unstaged_diff)
        self.assertIn("+value = 3", inspection.unstaged_diff)
        self.assertEqual(inspection.revision, self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual(inspection.sha256, hashlib.sha256(b"value = 3\n").hexdigest())
        self.assertEqual((self.root / ".git" / "index").read_bytes(), original_index)
        session.handle("down")
        runtime.update(0)
        self.assertEqual(session.atlas_inspection.selection.id, "beta.py")
        self.wait_until(runtime, lambda item: item.atlas_inspection.state == "ready" and
                        item.atlas_inspection.source_text == "other = 'initial'\n")
        (self.root / "beta.py").write_text("other = 'latest local bytes'\n", encoding="utf-8")
        session.handle("reload")
        runtime.update(0)
        self.wait_until(runtime, lambda item: item.atlas_inspection.state == "ready" and
                        item.atlas_inspection.source_text == "other = 'latest local bytes'\n")
        session.handle("accept")
        self.assertEqual(session.inspector_kind, "atlas")
        session.inspector_kind = None
        session.handle("handoff")  # H on Atlas prepares content, never a job.
        self.assertIn("beta.py", session.prepared_context)
        self.assertIn("latest local bytes", session.prepared_context)
        self.assertIn("untrusted project data", session.prepared_context)
        self.assertEqual(tuple(session.pending_actions), ())
        self.assertFalse(session.composer_open)
        self.assertEqual(runtime.arena.workers, {})
        self.assertEqual(runtime.terminals._terminals, {})
        self.assertEqual(runtime.tasks.snapshot().state, "idle")

        # Opening an editable handoff later still requires a completed public reply.
        session.choose_screen("arena")
        session.arena = replace(session.arena, mode="relay", cards=(
            replace(session.arena.cards[0], status="SUCCEEDED", summary="PUBLIC COMPLETED REPLY"),) + session.arena.cards[1:])
        session.handle("handoff")
        self.assertTrue(session.composer_open)
        self.assertIn("PUBLIC COMPLETED REPLY", session.composer_text)
        self.assertIn(session.prepared_context, session.composer_text)
        session.composer_key(ord("!"))
        self.assertTrue(session.composer_text.endswith("!"))
        self.assertEqual(tuple(session.pending_actions), ())
        session.composer_key(27)
        self.assertFalse(session.composer_open)
        self.assertEqual(runtime.arena.workers, {})

    def test_selection_change_cannot_prepare_or_export_previous_files_context(self):
        runtime, session = self.open_repository()
        self.assertEqual(session.atlas_inspection.path, "alpha.py")
        session.handle("down")
        self.assertEqual(session.atlas_selected.path, "beta.py")
        # Input can arrive before the next Runtime tick publishes pending state.
        session.handle("handoff")
        self.assertFalse(session.prepared_context, "H must wait for the currently selected file")
        runtime.args.export_dir = str(self.root / "exports")
        with self.assertRaises((ValueError, RuntimeError)):
            runtime.action("atlas-export")
        self.assertFalse(tuple((self.root / "exports").glob("*-context.txt")))

    def test_manifest_rendition_uses_actual_bytes_and_failed_reload_has_no_fixture_fallback(self):
        raw = b"glTF\0actual-local-fixture-bytes\xff"
        (self.root / "avatar.glb").write_bytes(raw)
        manifest = self.root / "assets.json"
        manifest.write_text(json.dumps(manifest_for(digest=hashlib.sha256(raw).hexdigest(), count=len(raw))))
        runtime, session = self.start("--asset-manifest", str(manifest))
        self.wait_until(runtime, lambda item: item.asset_catalog is not None)
        self.assertTrue(session.asset_manifest_expected)
        self.assertEqual((session.asset_catalog.mode, session.asset_catalog.origin), ("live", "local-manifest"))
        rendition_index = next(index for index, item in enumerate(session.asset_catalog.nodes) if item.kind == "rendition")
        for _ in range(rendition_index):
            session.handle("down")
        inspection = self.wait_until(runtime, lambda item: item.atlas_inspection.state == "ready" and
            item.atlas_inspection.selection.kind == "rendition" and item.atlas_inspection.hash_complete)
        self.assertEqual((inspection.sha256, inspection.size_bytes), (hashlib.sha256(raw).hexdigest(), len(raw)))
        self.assertEqual((inspection.hash_verified, inspection.size_verified), (True, True))
        self.assertIn("consumer admission not executed", inspection.verification)
        self.assertEqual(runtime.arena.workers, {})
        manifest.write_text('{"schema_version": 1, "assets": "not an array"}')
        session.handle("reload")
        self.wait_until(runtime, lambda item: item.atlas_inspection.state == "error")
        self.assertIsNone(session.asset_catalog)
        self.assertFalse(session.atlas_inspection.handoff_context)
        text = self.render(session)
        self.assertIn("UNAVAILABLE", text)
        self.assertNotIn("DEMO FIXTURE", text)
        self.assertNotIn("Example avatar", text)

    def test_initial_malformed_manifest_is_unavailable_until_explicit_valid_refresh(self):
        manifest = self.root / "assets.json"
        manifest.write_text("{ malformed JSON")
        runtime, session = self.start("--asset-manifest", str(manifest))
        self.wait_until(runtime, lambda item: item.atlas_inspection.state == "error")
        self.assertIsNone(session.asset_catalog)
        self.assertIn("UNAVAILABLE", self.render(session))
        manifest.write_text(json.dumps(manifest_for()))
        # Changing a file alone never invents a successful catalog reload.
        runtime.update(0)
        self.assertIsNone(session.asset_catalog)
        session.handle("reload")
        self.wait_until(runtime, lambda item: item.asset_catalog is not None and item.atlas_inspection.state == "ready")
        self.assertNotEqual(session.asset_catalog.origin, "fixture")

    def test_palette_export_is_explicit_exact_non_overwriting_and_blocked_in_stream_view(self):
        runtime, session = self.open_repository()
        destination = self.root / "exports"
        runtime.args.export_dir = str(destination)
        expected = session.atlas_inspection.handoff_context
        self.assertFalse(destination.exists(), "Inspection alone must not write an export")
        session.handle("palette")
        for char in "Atlas / export selected context":
            session.palette_key(ord(char))
        self.assertEqual(len(session.palette_commands), 1)
        session.palette_key(13)
        self.assertEqual(tuple(session.pending_actions), ("atlas-export",))
        with patch("devos.runtime.time.strftime", return_value="fixture-export"), \
             patch("devos.runtime.uuid.uuid4", return_value=SimpleNamespace(hex="abcdef000000")):
            runtime.update(0)
            path = destination / "fixture-export-abcdef-context.txt"
            self.assertEqual(path.read_bytes(), expected.encode("utf-8"))
            session.pending_actions.append("atlas-export")
            runtime.update(0)
            self.assertIn("File exists", session.status_message)
            self.assertEqual(path.read_bytes(), expected.encode("utf-8"))
            self.assertEqual(tuple(destination.iterdir()), (path,))
        session.stream_view = True
        session.pending_actions.append("atlas-export")
        runtime.update(0)
        self.assertIn("before export", session.status_message)
        self.assertEqual(tuple(destination.iterdir()), (path,))
        session.prepared_context = ""
        session.handle("handoff")
        self.assertFalse(session.prepared_context)


class RuntimeBearerSourceTests(unittest.TestCase):
    def test_token_option_is_env_name_only_and_does_not_read_it_during_parse_or_preferences(self):
        with patch.dict(os.environ, {"DEVOS_TEST_BEARER": "nonsecret-runtime-fixture"}), \
             patch("devos.telemetry.urllib.request.urlopen", side_effect=AssertionError("Parsing cannot connect")):
            args = parser().parse_args(["--no-config", "--bundled", "--source-token-env", "DEVOS_TEST_BEARER"])
            with self.assertRaisesRegex(ValueError, "explicit --source-url"):
                runtime_options(args)
            args = parser().parse_args(["--no-config", "--bundled", "--source-token-env", "DEVOS_TEST_BEARER",
                                        "--source-url", "http://127.0.0.1:1/fixture"])
            session = Session(sources=SourceProvider(bundled=True), **runtime_options(args))
            self.assertNotIn("source_token_env", preferences(session))
            self.assertNotIn("nonsecret-runtime-fixture", repr(preferences(session)))
            with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(["--source-token-env", "not a variable name!"])

    def test_runtime_forwards_environment_bearer_to_explicit_local_http_source(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                body = json.dumps({"schema_version": 1, "active_sessions": 37}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEVOS_TEST_BEARER": "nonsecret-runtime-fixture"}):
            args = parser().parse_args(["--no-config", "--bundled", "--agents", "watch", "--data-mode", "live",
                "--cwd", directory, "--source-url", "http://127.0.0.1:{}/aggregate".format(server.server_port),
                "--source-token-env", "DEVOS_TEST_BEARER"])
            session = Session(sources=SourceProvider(bundled=True), **runtime_options(args))
            runtime = Runtime(args, session)
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    runtime.update(0)
                    measured = session.telemetry.metric("site.active_sessions_60s")
                    if measured.value == 37:
                        break
                    time.sleep(.005)
                self.assertEqual((measured.value, measured.mode), (37, "live"))
                self.assertTrue(received)
                self.assertEqual(set(received), {"Bearer nonsecret-runtime-fixture"})
                self.assertNotIn("nonsecret-runtime-fixture", repr(preferences(session)))
                self.assertNotIn("nonsecret-runtime-fixture", repr(session.telemetry))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
