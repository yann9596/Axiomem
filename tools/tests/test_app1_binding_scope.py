import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('app1_binding', ROOT / 'adapters/multica/project-bindings/teachers-app1/build_binding.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class BindingScopeTests(unittest.TestCase):
    def setUp(self):
        self.issues = {'root': {'id': 'root', 'project_id': 'p', 'parent_issue_id': None},
                       'child': {'id': 'child', 'project_id': 'p', 'parent_issue_id': 'root'}}

    def verify(self, refs=None):
        return helper.validate_task_scope(refs or ['multica://issue/child'], 'root', 'p', self.issues.__getitem__)

    def test_child_and_root_allowed(self):
        self.assertEqual(self.verify()[0]['parent_chain'], ['child', 'root'])
        self.assertEqual(self.verify(['multica://issue/root'])[0]['parent_chain'], ['root'])

    def test_cross_project_rejected(self):
        self.issues['child']['project_id'] = 'other'
        with self.assertRaises(ValueError): self.verify()

    def test_unrelated_task_rejected(self):
        self.issues['child']['parent_issue_id'] = None
        with self.assertRaises(ValueError): self.verify()

    def test_cycle_rejected(self):
        self.issues['child']['parent_issue_id'] = 'child'
        with self.assertRaises(ValueError): self.verify()

    def test_unknown_root_project_rejected(self):
        self.issues['root']['project_id'] = None
        with self.assertRaises(ValueError): self.verify()

    def test_explicit_cli_identity(self):
        self.assertEqual(helper.cli_prefix('cli.exe', 'profile', 'workspace'),
                         ['cli.exe', '--profile', 'profile', '--workspace-id', 'workspace'])


if __name__ == '__main__': unittest.main()
