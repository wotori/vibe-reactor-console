"""Actual file/diff evidence and explicit asset declarations behind Atlas UI."""

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from devos import atlas
from devos.atlas import AtlasCatalog, AtlasInspection, AtlasInspector, AtlasNode, AtlasSelection
from devos.protocol import protocol_frame
from devos.repository import CommandResult


def wait_for(inspector, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = inspector.snapshot()
        if not snapshot.pending:
            return snapshot
        time.sleep(.005)
    raise AssertionError("Atlas worker did not complete within the test deadline")


def manifest_for(path="avatar.glb", digest=None, count=None):
    rendition = {"id": "desktop", "profile": "humanoid-glb-v1", "path": path}
    if digest is not None:
        rendition["sha256"] = digest
    if count is not None:
        rendition["byte_count"] = count
    return {"schema_version": 1, "source_id": "local-fixture", "assets": [{
        "identity": "ekza:avatar:example", "kind": "avatar", "label": "Example avatar",
        "revisions": [{"id": "r1", "renditions": [rendition], "approvals": [{
            "consumer": "ekza-space", "rendition": "desktop", "status": "approved"}]}]}]}


class RepositoryAtlasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "DEVOS Fixture")
        self.git("config", "user.email", "devos@example.invalid")
        (self.root / "hello.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "hello.py")
        self.git("commit", "-qm", "fixture")
        self.inspector = AtlasInspector()
        self.addCleanup(self.inspector.close)

    def git(self, *arguments):
        return subprocess.run(("git",) + arguments, cwd=self.root, capture_output=True, check=True)

    def inspect(self, path):
        pending = self.inspector.request_repo(self.root, path)
        self.assertEqual(pending.state, "pending")
        return wait_for(self.inspector)

    def test_actual_source_staged_and_working_diffs_have_separate_provenance(self):
        path = self.root / "hello.py"
        path.write_text("value = 2\n")
        self.git("add", "hello.py")
        path.write_text("value = 3\n")
        index_before = (self.root / ".git" / "index").read_bytes()
        inspection = self.inspect("hello.py")
        self.assertEqual((inspection.state, inspection.status_code), ("ready", "MM"))
        self.assertEqual(inspection.source_text, "value = 3\n")
        self.assertIn("+value = 2", inspection.staged_diff)
        self.assertIn("-value = 2", inspection.unstaged_diff)
        self.assertIn("+value = 3", inspection.unstaged_diff)
        self.assertIn("STAGED / INDEX", inspection.diff_text)
        self.assertIn("UNSTAGED / WORKING TREE", inspection.diff_text)
        self.assertEqual(inspection.revision, self.git("rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual(inspection.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(inspection.hash_complete)
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index_before)
        self.assertIn("hello.py", inspection.handoff_context)
        self.assertIn(inspection.revision, inspection.handoff_context)
        self.assertIn("untrusted project data", inspection.handoff_context)

    def test_untracked_case_and_unicode_filename_preserve_exact_source_identity(self):
        name = "AbC_角色 and spaces.py"
        text = "message = '真实 source'\n"
        (self.root / name).write_text(text, encoding="utf-8")
        inspection = self.inspect(name)
        self.assertEqual((inspection.state, inspection.path, inspection.status_code), ("ready", name, "??"))
        self.assertEqual(inspection.selection.label, name)
        self.assertEqual(inspection.source_text, text)
        self.assertFalse(inspection.diff_text)
        self.assertIn("真实 source", inspection.handoff_context)

    def test_git_magic_pathspec_filename_cannot_select_other_files(self):
        name = ":(glob)*.py"
        (self.root / name).write_text("selected = True\n")
        (self.root / "hello.py").write_text("UNRELATED_PRIVATE_MARKER = 1\n")
        inspection = self.inspect(name)
        self.assertEqual(inspection.state, "ready")
        self.assertEqual(inspection.status_code, "??")
        self.assertEqual(inspection.source_text, "selected = True\n")
        self.assertNotIn("UNRELATED_PRIVATE_MARKER", inspection.diff_text)

    def test_deleted_tracked_file_keeps_real_diff_without_fabricating_source(self):
        (self.root / "hello.py").unlink()
        inspection = self.inspect("hello.py")
        self.assertEqual((inspection.state, inspection.exists, inspection.status_code), ("ready", False, " D"))
        self.assertIn("-value = 1", inspection.unstaged_diff)
        self.assertIsNone(inspection.sha256)
        self.assertIn("deleted", inspection.source_text)

    def test_binary_and_non_utf8_files_never_claim_a_text_preview(self):
        for name, raw in (("asset.bin", b"glTF\0\xffbinary"), ("invalid.txt", b"\xff\xfe\x80")):
            with self.subTest(name=name):
                (self.root / name).write_bytes(raw)
                inspection = self.inspect(name)
                self.assertTrue(inspection.binary)
                self.assertIn("binary or non-UTF-8", inspection.source_text)
                self.assertEqual(inspection.sha256, hashlib.sha256(raw).hexdigest())
                self.assertTrue(inspection.hash_complete)

    def test_truncated_utf8_tail_is_not_mislabeled_binary_and_hash_limit_is_honest(self):
        self.inspector.close()
        self.inspector = AtlasInspector(source_limit=256, hash_limit=512)
        self.addCleanup(self.inspector.close)
        raw = b"a" * 255 + "中文".encode("utf-8") + b"b" * 1000
        (self.root / "large.txt").write_bytes(raw)
        inspection = self.inspect("large.txt")
        self.assertTrue(inspection.truncated)
        self.assertFalse(inspection.binary)
        self.assertIsNone(inspection.sha256)
        self.assertFalse(inspection.hash_complete)
        self.assertLessEqual(len(inspection.source_text), 256)
        self.assertLessEqual(len(inspection.handoff_context), 12000)
        self.assertIn("not fully hashed", inspection.handoff_context)

    def test_control_bytes_are_escaped_but_sha256_is_of_original_file_bytes(self):
        raw = b"# \x1b[31mred\r\nvalue = 1\x07\n"
        (self.root / "controls.py").write_bytes(raw)
        inspection = self.inspect("controls.py")
        self.assertNotIn("\x1b", inspection.source_text)
        self.assertNotIn("\x07", inspection.source_text)
        self.assertIn("\\x1b", inspection.source_text)
        self.assertEqual(inspection.sha256, hashlib.sha256(raw).hexdigest())

    def test_absolute_traversal_and_git_internal_paths_are_rejected_before_io(self):
        with patch("devos.atlas.os.open", side_effect=AssertionError("Invalid path performed IO")):
            for name in ("../outside", "/etc/passwd", ".git/config", "folder/../../outside", "bad\x00path"):
                with self.subTest(path=name), self.assertRaises(ValueError):
                    self.inspector.request_repo(self.root, name)

    def test_symlinks_and_special_files_are_rejected_without_dereferencing_or_blocking(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("NEVER READ THIS")
        self.addCleanup(outside.unlink)
        (self.root / "link.py").symlink_to(outside)
        (self.root / "directory-link").symlink_to(self.root, target_is_directory=True)
        for name in ("link.py", "directory-link/hello.py"):
            inspection = self.inspect(name)
            self.assertEqual(inspection.state, "error")
            self.assertNotIn("NEVER READ THIS", inspection.source_text)
            self.assertFalse(inspection.handoff_context)
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "pipe")
            started = time.monotonic()
            inspection = self.inspect("pipe")
            self.assertLess(time.monotonic() - started, .8)
            self.assertEqual(inspection.state, "error")

    def test_missing_or_nonrepository_selection_is_an_explicit_error(self):
        self.assertEqual(self.inspect("does-not-exist").state, "error")
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / "file.py").write_text("print('not a repository')")
            self.inspector.request_repo(other, "file.py")
            inspection = wait_for(self.inspector)
            self.assertEqual(inspection.state, "error")
            self.assertFalse(inspection.source_text)

    def test_newer_selection_wins_and_slow_worker_does_not_block_request_or_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()

        def slow(root, path, *args):
            self.assertNotEqual(threading.get_ident(), main_thread)
            entered.set()
            release.wait(1)
            return AtlasInspection(AtlasSelection("repository", path, path, path), "ready", path=path)

        with patch("devos.atlas._inspect_repo", side_effect=slow):
            start = time.monotonic()
            self.inspector.request_repo(self.root, "first.py")
            self.assertTrue(entered.wait(.5))
            self.inspector.request_repo(self.root, "second.py")
            self.assertEqual(self.inspector.snapshot().selection.id, "second.py")
            self.assertLess(time.monotonic() - start, .5)
            release.set()
            inspection = wait_for(self.inspector)
        self.assertEqual(inspection.path, "second.py")

    def test_no_follow_open_rejects_symlink_swapped_after_path_validation(self):
        # Validation is lexical only; NOFOLLOW must still protect the actual
        # eventual worker open rather than relying on a previous resolve().
        (self.root / "later.py").symlink_to(self.root / "hello.py")
        inspection = self.inspect("later.py")
        self.assertEqual(inspection.state, "error")

    def test_worker_timeout_is_explicit_and_all_git_calls_have_a_shared_deadline(self):
        main_thread, timeouts = threading.get_ident(), []

        def timed_out(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), main_thread)
            timeouts.append(kwargs["timeout"])
            return CommandResult(-9, "", timed_out=True)

        with patch("devos.atlas.run_bounded", side_effect=timed_out):
            inspection = self.inspect("hello.py")
        self.assertEqual(inspection.state, "error")
        self.assertIn("deadline", inspection.error)
        self.assertEqual(len(timeouts), 1)
        self.assertTrue(0 < timeouts[0] <= self.inspector.timeout)

    def test_source_modified_while_diff_is_collected_cannot_be_handed_off_as_one_snapshot(self):
        original, changed = atlas._git, []

        def mutate_during_diff(root, arguments, *args):
            if arguments[0] == "diff" and not changed:
                (self.root / "hello.py").write_text("changed_during_inspection = True\n")
                changed.append(True)
            return original(root, arguments, *args)

        with patch("devos.atlas._git", side_effect=mutate_during_diff):
            inspection = self.inspect("hello.py")
        self.assertFalse(inspection.consistent)
        self.assertIn("changed during inspection", inspection.error)
        self.assertFalse(inspection.handoff_context)


class AssetCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.inspector = AtlasInspector()
        self.addCleanup(self.inspector.close)

    def manifest(self, document):
        path = self.root / "assets.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def test_protocol_fixture_is_pure_and_does_not_claim_verified_or_executed_assets(self):
        with patch("devos.atlas.os.open", side_effect=AssertionError("Fixture performed IO")), \
                patch("devos.atlas.run_bounded", side_effect=AssertionError("Fixture started a command")):
            catalog = AtlasCatalog.from_protocol(protocol_frame(0, "space"))
        self.assertEqual(len(catalog.nodes), 6)
        self.assertEqual({node.kind for node in catalog.nodes}, {"identity", "revision", "rendition", "approval", "verify", "consumer"})
        self.assertTrue(all(node.origin == "fixture" and node.mode == "demo" for node in catalog.nodes))
        verify = next(node for node in catalog.nodes if node.kind == "verify")
        self.assertIn("NOT VERIFIED", verify.get("verification"))
        self.inspector.request_node(catalog, verify.id)
        inspection = wait_for(self.inspector)
        self.assertFalse(inspection.hash_complete)
        self.assertIn("fixture", inspection.verification)
        consumer = next(node for node in catalog.nodes if node.kind == "consumer")
        self.assertEqual(consumer.get("execution"), "NOT EXECUTED")

    def test_manifest_graph_keeps_declared_bytes_approvals_and_consumers_distinct(self):
        document = manifest_for(digest="a" * 64, count=123)
        with patch("devos.atlas.os.open", side_effect=AssertionError("Pure parsing read a model")):
            catalog = AtlasCatalog.from_dict(document)
        self.assertEqual(catalog.origin, "local-manifest")
        rendition = next(node for node in catalog.nodes if node.kind == "rendition")
        self.assertEqual((rendition.get("declared_sha256"), rendition.get("declared_size_bytes")), ("a" * 64, 123))
        approval = next(node for node in catalog.nodes if node.kind == "approval")
        self.assertIn("exact bytes not specified", approval.get("scope"))
        self.assertEqual(approval.get("status"), "approved")
        self.assertEqual(approval.get("execution"), "NOT EXECUTED")
        self.assertTrue(all(edge.origin == "local-manifest" for edge in catalog.edges))

    def test_selected_rendition_reads_actual_bytes_and_checks_hash_and_size_only(self):
        raw = b"glTF\0fixture model bytes"
        (self.root / "avatar.glb").write_bytes(raw)
        path = self.manifest(manifest_for(digest=hashlib.sha256(raw).hexdigest(), count=len(raw)))
        pending = self.inspector.request_asset_manifest(path)
        self.assertTrue(pending.pending)
        loaded = wait_for(self.inspector)
        self.assertEqual(loaded.state, "ready")
        catalog = loaded.catalog
        rendition = next(node for node in catalog.nodes if node.kind == "rendition")
        with patch("devos.atlas.run_bounded", side_effect=AssertionError("Asset inspection executed a consumer")):
            self.inspector.request_node(catalog, rendition.id)
            inspection = wait_for(self.inspector)
        self.assertEqual((inspection.hash_verified, inspection.size_verified), (True, True))
        self.assertIn("LOCAL BYTES MATCH", inspection.verification)
        self.assertIn("consumer admission not executed", inspection.verification)
        self.assertTrue(inspection.binary)

    def test_hash_mismatch_and_missing_bytes_are_never_reported_as_verified(self):
        (self.root / "avatar.glb").write_bytes(b"actual bytes")
        catalog = AtlasCatalog.from_manifest(self.manifest(manifest_for(digest="0" * 64, count=999)))
        rendition = next(node for node in catalog.nodes if node.kind == "rendition")
        self.inspector.request_node(catalog, rendition.id)
        inspection = wait_for(self.inspector)
        self.assertEqual((inspection.hash_verified, inspection.size_verified), (False, False))
        self.assertIn("MISMATCH", inspection.verification)
        (self.root / "avatar.glb").unlink()
        self.inspector.request_node(catalog, rendition.id)
        self.assertEqual(wait_for(self.inspector).state, "error")

    def test_manifest_selection_refuses_symlinked_files_and_model_paths(self):
        path = self.manifest(manifest_for())
        alias = self.root / "linked.json"
        alias.symlink_to(path)
        self.inspector.request_asset_manifest(alias)
        self.assertEqual(wait_for(self.inspector).state, "error")
        (self.root / "avatar.glb").symlink_to(path)
        catalog = AtlasCatalog.from_manifest(path)
        rendition = next(node for node in catalog.nodes if node.kind == "rendition")
        self.inspector.request_node(catalog, rendition.id)
        self.assertEqual(wait_for(self.inspector).state, "error")

    def test_invalid_graph_references_hashes_sizes_duplicates_and_paths_are_rejected(self):
        cases = []
        for path in ("../outside.glb", "/absolute.glb", ".git/config", "https://example.invalid/model.glb"):
            cases.append(manifest_for(path=path))
        cases.append(manifest_for(digest="not-a-sha256"))
        cases.append(manifest_for(count=True))
        cases.append(manifest_for(count=-1))
        bad_reference = manifest_for()
        bad_reference["assets"][0]["revisions"][0]["approvals"][0]["rendition"] = "missing"
        cases.append(bad_reference)
        duplicate = manifest_for()
        duplicate["assets"].append(duplicate["assets"][0])
        cases.append(duplicate)
        contradictory_hashes = manifest_for(digest="a" * 64)
        contradictory_hashes["assets"][0]["revisions"][0]["approvals"][0]["sha256"] = "b" * 64
        cases.append(contradictory_hashes)
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ValueError):
                AtlasCatalog.from_dict(document)

    def test_frozen_catalog_and_inspection_cannot_expose_nested_mutable_metadata(self):
        mutable = {"nested": [1, 2]}
        node = AtlasNode("id", "identity", "ID", mutable)
        mutable["nested"].append(3)
        self.assertEqual(node.get("nested"), (1, 2))
        with self.assertRaises(FrozenInstanceError):
            node.label = "changed"
        catalog = AtlasCatalog([node], [])
        self.assertIsInstance(catalog.nodes, tuple)
        self.assertIsInstance(catalog.edges, tuple)
        inspection = AtlasInspection(metadata=mutable)
        self.assertEqual(dict(inspection.metadata)["nested"], (1, 2, 3))
        with self.assertRaises(FrozenInstanceError):
            inspection.state = "ready"

    def test_manifest_size_bound_and_unknown_node_leave_an_explicit_error(self):
        path = self.root / "large.json"
        path.write_bytes(b" " * (1024 * 1024 + 1))
        self.inspector.request_asset_manifest(path)
        self.assertIn("1 MiB", wait_for(self.inspector).error)
        catalog = AtlasCatalog.from_protocol(protocol_frame(0))
        with self.assertRaises(ValueError):
            self.inspector.request_node(catalog, "not-a-node")
        self.inspector.close()
        with self.assertRaises(RuntimeError):
            self.inspector.request_repo(self.root, "file.py")
