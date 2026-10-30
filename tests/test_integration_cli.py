from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


class IntegrationCliTests(unittest.TestCase):
    def test_standalone_candidate_has_actual_changes_and_conflicts_with_no_terminal(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('base', 'left', 'right'):
                (root / name).mkdir()
                (root / name / 'shared.py').write_text('original = 1\n')
            (root / 'left' / 'left.py').write_text('left = 1\n')
            (root / 'right' / 'right.py').write_text('right = 1\n')
            result = subprocess.run([sys.executable, '-m', 'devos', '--integrate',
                                     *[str(root / name) for name in ('base', 'left', 'right', 'candidate')]],
                                    cwd=project, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'candidate' / 'left.py').is_file())
            self.assertTrue((root / 'candidate' / 'right.py').is_file())
            self.assertIn('NOT VERIFIED', result.stdout)
            (root / 'left' / 'shared.py').write_text('original = 2\n')
            (root / 'right' / 'shared.py').write_text('original = 3\n')
            result = subprocess.run([sys.executable, '-m', 'devos', '--integrate',
                                     *[str(root / name) for name in ('base', 'left', 'right', 'conflicted')]],
                                    cwd=project, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            report = json.loads((root / 'conflicted' / '.devos' / 'integration-report.json').read_text())
            self.assertEqual(report['status'], 'needs-user')
            self.assertEqual((root / 'conflicted' / 'shared.py').read_text(), 'original = 1\n')
            self.assertEqual((root / 'left' / 'shared.py').read_text(), 'original = 2\n')

    def test_candidate_rejects_mission_before_any_model_or_directory_creation(self):
        project = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-m', 'devos', '--integrate', '/missing/base', '/missing/left',
                                 '/missing/right', '/missing/dest', '--mission', 'must never start'],
                                cwd=project, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('standalone', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
