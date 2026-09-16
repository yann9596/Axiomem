"""Review a named non-code artifact without weakening legacy or QA gates."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import cartifact as ac
sys.path.insert(0, str(ROOT / 'skills/multica-context-handoff/scripts'))
import artifact_gate


class ReviewSubjectTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((ROOT / 'tools/fixtures/artifact-contract/store-chain.json').read_text(encoding='utf-8'))['envelopes']

    def request(self, kind):
        row = [r for r in self.rows if r['artifact_type'] == kind][-1]
        ref = {k: row[k] for k in ('artifact_type', 'artifact_id', 'version')}
        return dict(schema_version='1.0', kind='artifact_ready_check_request',
                    target_role='delivery-reviewer', review_level='R1',
                    requirements=[ref], reviewed_artifact=ref.copy())

    def check(self, req):
        return ac.artifact_ready_check(ac.ArtifactStore(self.rows), req)

    def test_design_and_product_subject_ready(self):
        for kind in ('design_baseline', 'product_expectation'):
            with self.subTest(kind=kind):
                result = self.check(self.request(kind))
                self.assertEqual(result['status'], 'ARTIFACT_READY', result)

    def test_legacy_still_requires_implementation(self):
        req = self.request('design_baseline'); del req['reviewed_artifact']
        self.assertEqual(self.check(req)['status'], 'ARTIFACT_NOT_READY')

    def test_different_subject_is_rejected(self):
        req = self.request('design_baseline'); req['reviewed_artifact']['artifact_id'] += '-other'
        self.assertEqual(self.check(req)['status'], 'ARTIFACT_NOT_READY')

    def test_stale_superseded_and_wrong_owner_rejected(self):
        original = copy.deepcopy(self.rows)
        for field, value in (('status', 'stale'), ('status', 'superseded'), ('owner_role', 'software-engineer')):
            self.rows = copy.deepcopy(original)
            [r for r in self.rows if r['artifact_type'] == 'design_baseline'][-1][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.check(self.request('design_baseline'))['status'], 'ARTIFACT_NOT_READY')

    def test_qa_keeps_full_baseline(self):
        req = self.request('design_baseline'); req.update(target_role='qa', review_level='R2')
        self.assertEqual(self.check(req)['status'], 'ARTIFACT_NOT_READY')

    def test_unresolved_version_rejected(self):
        req = self.request('design_baseline')
        req['requirements'][0]['version'] = req['reviewed_artifact']['version'] = 'latest'
        self.assertEqual(self.check(req)['status'], 'ARTIFACT_NOT_READY')

    def test_subject_roundtrip_and_substitution_fails_freshness(self):
        req = self.request('design_baseline')
        store = ac.ArtifactStore(self.rows)
        ready, exported = artifact_gate.evaluate_ready(
            ac, store, 'delivery-reviewer', req['requirements'], 'R1', req['reviewed_artifact'])
        self.assertEqual(ready['status'], 'ARTIFACT_READY')
        extracted = artifact_gate.extract_dependency_records(exported)
        self.assertEqual(extracted['reviewed_artifact'], req['reviewed_artifact'])
        inputs = dict(previous_requirements=req['requirements'],
                      previous_digest=extracted['digest'], current_requirements=req['requirements'],
                      target_role='delivery-reviewer', review_level='R1',
                      reviewed_artifact=req['reviewed_artifact'],
                      previous_reviewed_artifact=extracted['reviewed_artifact'])
        self.assertFalse(artifact_gate.evaluate_freshness(ac, store, **inputs)['stale'])
        inputs['reviewed_artifact'] = self.request('product_expectation')['reviewed_artifact']
        self.assertTrue(artifact_gate.evaluate_freshness(ac, store, **inputs)['stale'])
        inputs['reviewed_artifact'] = None
        self.assertTrue(artifact_gate.evaluate_freshness(ac, store, **inputs)['stale'])

if __name__ == '__main__':
    unittest.main()
