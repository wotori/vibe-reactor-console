from dataclasses import replace
import unittest

from devos.agents import AgentCoordinator, Artifact, ValidationReport
from devos.renderer import Renderer
from devos.session import Session
from devos.sources import SourceProvider
from devos.task_runner import TaskSnapshot
from devos.terminal import Canvas
from tests.test_rendering import Screen


class InspectionTests(unittest.TestCase):
    def test_actual_task_output_and_independent_validation_are_visible_and_redacted(self):
        session = Session(sources=SourceProvider(bundled=True))
        session.task_snapshot = TaskSnapshot('run-42', ('test-command',), '/private/work', 'failed', 7,
                                             output='ACTUAL_EXIT_OUTPUT', revision='abc123')
        arena = AgentCoordinator()
        report = ValidationReport('SUCCEEDED', ('python3', 'check.py'), '/private/work', 'abc123',
                                  'manifest-42', 1, .1, 0, 'CHECK_STDOUT', 'CHECK_STDERR')
        session.arena = replace(arena.snapshot(), validation_reports=(report,), artifacts=(
            Artifact('patch-42', 'patch', 'abc123', 'codex', text='EXACT_PATCH_BYTES'),))
        for kind, marker in (('task', 'ACTUAL_EXIT_OUTPUT'), ('mission', 'CHECK_STDOUT')):
            session.handle('inspect-' + kind)
            for size in ((4, 12), (16, 48), (44, 144)):
                screen = Screen(*size)
                Renderer(Canvas(screen, color=False)).draw(session)
                if size == (44, 144):
                    self.assertIn(marker, screen.text())
                    if kind == 'mission':
                        self.assertIn('CHECK_STDERR', screen.text())
                        self.assertIn('manifest-42', screen.text())
                        self.assertIn('EXACT_PATCH_BYTES', screen.text())
            session.stream_view = True
            screen = Screen(44, 144)
            Renderer(Canvas(screen, color=False)).draw(session)
            self.assertNotIn(marker, screen.text())
            self.assertNotIn('/private/work', screen.text())
            session.stream_view = False
