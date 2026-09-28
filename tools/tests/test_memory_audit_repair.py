#!/usr/bin/env python3
"""Offline regression for the paused-project audit repair. No platform writes.

Fixtures are explicitly synthetic; a passing test is not a live authority or
consumer SELF_CHECK receipt. Existing frozen schemas and negative paths stay.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / "tests")]
import context_quality as quality
import context_dependencies as deps
import handoff_parse_cache as caching
import memory_health
import chandoff_plan as plan
import chandoff_finalize as finalize
import chandoff_selfcheck as selfcheck
import chandoff_note as note
from cdata import load_all_docs
from schema_mini import Schema, load_schema_file
from yaml_mini import parse_yaml
from test_handoff_finalize import sample_request, accept
from test_handoff_note import valid_envelope, record_body, comment, fake_cli, resolve, publish

ROOT = TOOLS.parent


class PolicyFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / quality.POLICY_PATH
        self.path.parent.mkdir(parents=True)
        self.policy = quality.load_policy()
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.policy), encoding="utf-8")

    def assess(self, stage, value, **kwargs):
        return quality.assess(stage, value, root=self.root, **kwargs)

    def test_utf8_bytes_not_character_or_token_count(self):
        self.assertEqual(quality.byte_size("语文"), 8)
        self.assertEqual(self.assess("transport", "语文")["metrics"]["transport_bytes"], 6)

    def test_exact_limit_admitted_and_next_byte_refused(self):
        self.policy['limits']['transport_bytes'] = 6; self.save()
        self.assertFalse(self.assess('transport', '语文')['blocked'])
        self.assertTrue(self.assess('transport', '语文!')['blocked'])

    def test_missing_policy_fails_closed(self):
        self.path.unlink()
        with self.assertRaises(quality.QualityPolicyError): self.assess('package', {})

    def test_invalid_policy_fields_and_types(self):
        for mutation in ('missing', 'zero', 'bool', 'unknown', 'bad-mode'):
            with self.subTest(mutation=mutation):
                self.policy = quality.load_policy()
                if mutation == 'missing': self.policy['limits'].pop('fact_bytes')
                elif mutation == 'zero': self.policy['limits']['fact_bytes'] = 0
                elif mutation == 'bool': self.policy['limits']['fact_bytes'] = True
                elif mutation == 'unknown': self.policy['override'] = True
                else: self.policy['mode'] = 'unlimited'
                self.save()
                with self.assertRaises(quality.QualityPolicyError): self.assess('package', {})

    def test_observe_is_visible_not_claimed_pass(self):
        self.policy['mode'] = 'observe'; self.policy['limits']['package_bytes'] = 1; self.save()
        report = self.assess('package', {})
        self.assertFalse(report['ok']); self.assertFalse(report['blocked']); self.assertTrue(report['violations'])

    def test_policy_revision_changes(self):
        before = quality.policy_revision(self.policy)
        self.policy['limits']['fact_bytes'] += 1
        self.assertNotEqual(before, quality.policy_revision(self.policy))

    def test_single_fact_overflow_does_not_require_many_items(self):
        p = {'current_facts': [{'ref': 'fact:TEST', 'statement': '长' * 3000}]}
        report = self.assess('package', p)
        self.assertIn('fact_bytes', [r['limit_key'] for r in report['violations']])
        self.assertTrue(any(r['object_ref'] == 'fact:TEST' for r in report['violations']))

    def test_checkpoint_count_and_item_bytes_are_separate(self):
        entries = [{'id': str(i), 'summary': 's'} for i in range(25)]
        report = self.assess('package', {'project_state_slice': entries})
        self.assertIn('checkpoint_entries', [v['limit_key'] for v in report['violations']])
        report = self.assess('package', {'project_state_slice': [{'id': 'cp', 'summary': 'x' * 5000}]})
        self.assertIn('checkpoint_entry_bytes', [v['limit_key'] for v in report['violations']])

    def test_references_and_sections_are_bounded(self):
        for p, key in [({'source_refs': ['repo://x/a'] * 129}, 'source_refs'),
                       ({'source_refs': ['repo://x/' + 'a' * 17000]}, 'source_refs_bytes'),
                       ({'current_facts': [{'statement': 'x' * 7000}] * 5}, 'current_facts_bytes'),
                       ({'project_state_slice': [{'summary': 'x' * 3500}] * 8}, 'checkpoint_slices_bytes')]:
            with self.subTest(key=key): self.assertIn(key, [r['limit_key'] for r in self.assess('package',p)['violations']])

    def test_duplicates_measured_without_mutation(self):
        p = {'source_refs': ['repo://x/a', 'repo://x/a']}; before=copy.deepcopy(p)
        self.assertEqual(self.assess('package',p)['metrics']['duplicate_top_level_refs'],1)
        self.assertEqual(p,before)

    def test_plan_inspects_full_admitted_source_not_short_summary(self):
        p = {'candidates': {'facts': [{'id': 'f', 'summary': 'short'}]}}
        report = self.assess('plan',p,source_docs=[{'id':'f','statement':'x'*20000}])
        self.assertTrue(report['blocked'])
        self.assertEqual(report['violations'][0]['object_ref'], 'f')

    def test_unselected_or_foreign_source_not_silently_added(self):
        self.assertTrue(self.assess('plan', {'candidates':{'facts':[]}}, source_docs=[{'id':'foreign','statement':'x'*20000}])['ok'])

    def test_envelope_and_actual_transport_checked(self):
        e=valid_envelope();e['package']['task_evidence']=['x'*80000]
        self.assertTrue(self.assess('envelope',e)['blocked'])
        self.assertTrue(self.assess('transport',' '*100000)['blocked'])

    def test_request_options_cannot_override_trusted_policy(self):
        e={'current_facts':[{'statement':'x'*10000}], 'options':{'fact_bytes':999999999,'mode':'observe'}}
        self.assertTrue(self.assess('package',e)['blocked'])


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.req=sample_request(project={'project_id':'teachers-app1'},task_snapshot={
            'title':'implementation integration platform storage governance','description':'P01 platform storage integration review',
            'requirements':['preserve exact source'],'acceptance_criteria':['blocked boundaries retained'],'relevant_decisions':[]})
        self.doc=next(d for d in load_all_docs() if d.get('id')=='FACT-TAPP1-000012')

    def test_real_plan_and_finalize_schema_unchanged(self):
        p=plan.prepare_handoff_plan(self.req,findings=[],docs=[self.doc]);self.assertEqual(p['status'],'PLAN_READY')
        result=finalize.finalize_handoff(p,accept(p),self.req,docs=[self.doc]);self.assertEqual(result['status'],'READY',result)
        for name,value in [('context-handoff/context-plan.schema.json',p['plan']),('context-handoff/prepare-handoff-result.schema.json',result)]:
            schema=load_schema_file(name);self.assertEqual(Schema(schema,schema).validate(value),[])
        self.assertTrue(p['dependency_shadow']['shadow_only'])

    def test_plan_blocks_large_source_before_compose(self):
        d=copy.deepcopy(self.doc);d['statement']='x'*20000
        p=plan.prepare_handoff_plan(self.req,findings=[],docs=[d])
        self.assertEqual(p['status'],'BLOCKED');self.assertIsNone(p['plan']);self.assertIn('CONTEXT_BUDGET_OVERFLOW',p['escalation']['reason'])

    def test_missing_policy_plan_returns_blocked_not_ready(self):
        with patch.object(quality,'load_policy',side_effect=quality.QualityPolicyError('missing')):
            p=plan.prepare_handoff_plan(self.req,findings=[],docs=[self.doc])
        self.assertEqual(p['status'],'BLOCKED');self.assertEqual(p['escalation']['reason'],'missing')

    def test_finalize_rechecks_policy_after_plan(self):
        p=plan.prepare_handoff_plan(self.req,findings=[],docs=[self.doc]);a=accept(p)
        policy=quality.load_policy();policy['limits']['fact_bytes']=10
        with patch.object(quality,'load_policy',return_value=policy):
            result=finalize.finalize_handoff(p,a,self.req,docs=[self.doc])
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('CONTEXT_BUDGET_OVERFLOW',result['escalation']['reason'])
        self.assertEqual(result['package']['current_facts'],[])
        schema=load_schema_file('context-handoff/prepare-handoff-result.schema.json')
        self.assertEqual(Schema(schema,schema).validate(result),[])

    def test_publication_refused_before_any_cli_write(self):
        e=valid_envelope();e['package']['task_evidence']=['x'*80000];cli=fake_cli()
        with self.assertRaises(note.AdapterError):publish(e,cli=cli)
        self.assertEqual(cli.calls['argv'],[])

    def test_actual_transport_budget_is_enforced(self):
        e=valid_envelope();cli=fake_cli();policy=quality.load_policy();policy['limits']['transport_bytes']=100
        with patch.object(quality,'load_policy',return_value=policy):
            with self.assertRaises(note.AdapterError):publish(e,cli=cli)
        self.assertEqual(cli.calls['argv'],[])

    def test_post_artifact_gate_checks_full_envelope_before_sealing(self):
        e=valid_envelope();e['package']['task_evidence']=[{'artifact_id':'synthetic','payload':'x'*80000}]
        before=copy.deepcopy(e);result,report=quality.guard_final_envelope(e)
        self.assertEqual(e,before);self.assertEqual(result['status'],'BLOCKED')
        self.assertTrue(report['blocked']);self.assertEqual(result['package']['task_evidence'],e['package']['task_evidence'])
        self.assertIn('CONTEXT_BUDGET_OVERFLOW',result['package']['blocked_by'])

    def test_post_artifact_preserves_previous_failure_reason(self):
        e=valid_envelope();e['status']='BLOCKED';e['escalation']={'required':True,'reason':'ARTIFACT_NOT_READY'}
        e['package']['task_evidence']=['x'*80000]
        result,_=quality.guard_final_envelope(e)
        self.assertEqual(result['escalation']['reason'],'ARTIFACT_NOT_READY')

    def test_old_oversized_envelope_cannot_stay_ready(self):
        e=valid_envelope();e['package']['task_evidence']=['x'*80000]
        req={'task_ref':e['task_ref'],'role':e['role']}
        with patch.object(selfcheck,'current_fingerprint',return_value=e['built_from']['task_fingerprint']):
            result=selfcheck.check_package(req,e,current=e['built_from'])
        self.assertEqual(result['status'],'REFRESH_REQUIRED');self.assertIn('package_stale',result['reasons'])

    def test_malformed_policy_consumer_blocks(self):
        e=valid_envelope();req={'task_ref':e['task_ref'],'role':e['role']}
        with patch.object(selfcheck,'current_fingerprint',return_value=e['built_from']['task_fingerprint']), patch.object(quality,'load_policy',side_effect=quality.QualityPolicyError('missing')):
            result=selfcheck.check_package(req,e,current=e['built_from'])
        self.assertEqual(result['status'],'BLOCKED')

    def test_budgets_do_not_hide_prior_blocked_verdict(self):
        e=valid_envelope();e['status']='BLOCKED';e['package']['task_evidence']=['x'*80000]
        with patch.object(selfcheck,'current_fingerprint',return_value=e['built_from']['task_fingerprint']):
            result=selfcheck.check_package({'task_ref':e['task_ref'],'role':e['role']},e,current=e['built_from'])
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('package_not_ready',result['reasons'])


class CacheTests(unittest.TestCase):
    def test_identical_content_reuses_parse_and_returns_copy(self):
        cache=caching.ParseCache();calls=[]
        def parser(s):calls.append(s);return {'data':[s]}
        a=cache.parse(('x',),'v1','hello',parser);a['data'].append('corrupt')
        b=cache.parse(('x',),'v1','hello',parser)
        self.assertEqual(b,{'data':['hello']});self.assertEqual(len(calls),1)

    def test_changed_body_parser_revision_and_namespace_miss(self):
        cache=caching.ParseCache();calls=[]
        def parser(s):calls.append(s);return {'s':s}
        for ns,rev,body in [(('x',),'v1','a'),(('x',),'v1','b'),(('x',),'v2','b'),(('y',),'v2','b')]:cache.parse(ns,rev,body,parser)
        self.assertEqual(len(calls),4)

    def test_cache_has_hard_size_and_entry_limits(self):
        cache=caching.ParseCache(max_bytes=80,max_entries=2)
        parser=lambda s:{'s':s}
        for i in range(12):cache.parse((), 'v', str(i), parser)
        self.assertLessEqual(cache.stats()['entries'],2);self.assertLessEqual(cache.stats()['accounted_bytes'],80)
        cache.parse((),'v','x'*200,parser);self.assertLessEqual(cache.stats()['accounted_bytes'],80)

    def live(self):
        docs=[comment('a',record_body(),created_at='2026-09-09T15:00:00Z')]
        cli=fake_cli(comments=docs);resolve(cli)
        return docs,cli

    def test_every_discovery_still_reads_live_thread(self):
        docs,cli=self.live();resolve(cli)
        self.assertEqual(len(cli.calls['argv']),2);self.assertGreater(cli._handoff_parse_cache.stats()['hits'],0)
        self.assertEqual(cli._handoff_parse_cache.stats()['network_reads_saved'],0)

    def test_newer_broken_record_never_falls_back_to_cache(self):
        docs,cli=self.live();docs.append(comment('b',record_body()+'garbage',created_at='2026-09-09T16:00:00Z'))
        with self.assertRaises(note.LatestHandoffInvalidError):resolve(cli)

    def test_newer_unbindable_record_blocks(self):
        docs,cli=self.live();docs.append(comment('b','/note\n\nCONTEXT_HANDOFF_RECORD v1\n',created_at='2026-09-09T16:00:00Z'))
        with self.assertRaises(note.LatestHandoffInvalidError):resolve(cli)

    def test_edit_same_comment_id_is_reparsed(self):
        docs,cli=self.live();docs[0]['content']+='garbage'
        with self.assertRaises(note.LatestHandoffInvalidError):resolve(cli)

    def test_deleted_comment_is_not_resurrected(self):
        docs,cli=self.live();docs.clear();self.assertFalse(resolve(cli)['found'])

    def test_package_id_conflict_still_detected_with_cached_first(self):
        docs,cli=self.live();env=valid_envelope();env['package']['rules'][0]['statement']='changed'
        docs.append(comment('b',record_body(env),created_at='2026-09-09T16:00:00Z'))
        with self.assertRaises(note.PackageIdConflictError):resolve(cli)

    def test_cross_task_and_role_do_not_return_cached_package(self):
        docs,cli=self.live()
        self.assertFalse(resolve(cli,task_ref='multica://issue/YZT-12345')['found'])
        self.assertFalse(resolve(cli,role='qa')['found'])


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        for folder in ['team-context','project-context','migration']:
            shutil.copytree(ROOT/folder,self.root/folder)
        self.scope={'type':'project','project_id':'teachers-app1','projects':[],'task_id':None}
        self.role='software-engineer';self.before=self.get()

    def get(self,**kw):return deps.build_manifest(kw.pop('scope',self.scope),kw.pop('role',self.role),root=self.root,**kw)
    def write(self,path,text):p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def change(self,path):p=self.root/path;p.write_text(p.read_text()+'\naudit_probe: true\n')

    def test_shadow_manifest_never_changes_global_authority(self):
        self.assertTrue(self.before['shadow_only']);self.assertTrue(self.before['global_check_unchanged'])
        self.assertEqual(deps.compare(self.before,self.get())['decision'],'scoped_unchanged')

    def test_unrelated_project_fact_does_not_change_scope_digest(self):
        p=next((self.root/'project-context/web-imagegen/facts').glob('*.yaml'));self.change(p.relative_to(self.root))
        self.assertEqual(self.before['digest'],self.get()['digest'])

    def test_current_fact_change_triggers(self):
        self.change('project-context/teachers-app1/facts/FACT-TAPP1-000012.yaml')
        self.assertNotEqual(self.before['digest'],self.get()['digest'])

    def test_new_applicable_rule_detected_without_being_selected(self):
        self.write('project-context/teachers-app1/rules/RULE-TAPP1-999999.yaml', 'id: RULE-TAPP1-999999\nscope:\n  type: project\n  project_id: teachers-app1\nstatus: active\n')
        self.assertNotEqual(self.before['digest'],self.get()['digest'])

    def test_declared_scope_not_physical_directory_controls_manifest(self):
        self.write('project-context/web-imagegen/facts/FACT-WIMG-999999.yaml','id: FACT-WIMG-999999\nscope:\n  type: project\n  project_id: teachers-app1\nstatus: active\n')
        self.assertNotEqual(self.before['digest'],self.get()['digest'])

    def test_deletion_revocation_checkpoint_policy_role_authority_detected(self):
        for rel in ['team-context/policies/retention.yaml','team-context/roles/software-engineer.yaml','migration/authority-evidence.yaml','project-context/teachers-app1/checkpoint.yaml']:
            with self.subTest(path=rel):
                p=self.root/rel;raw=p.read_bytes();self.change(rel)
                self.assertNotEqual(self.before['digest'],self.get()['digest']);p.write_bytes(raw)
        (self.root/'project-context/teachers-app1/facts/FACT-TAPP1-000012.yaml').unlink()
        self.assertNotEqual(self.before['digest'],self.get()['digest'])

    def test_unrelated_role_does_not_change_digest(self):
        self.change('team-context/roles/qa.yaml');self.assertEqual(self.before['digest'],self.get()['digest'])

    def test_scope_change_and_artifact_dependency_change_detected(self):
        scoped=self.get(scope={'type':'cross_project','projects':['teachers-app1','web-imagegen'],'project_id':None,'task_id':None})
        self.assertNotEqual(self.before['digest'],scoped['digest'])
        a=self.get(package={'task_evidence':[{'version':'1'}]});b=self.get(package={'task_evidence':[{'version':'2'}]})
        self.assertNotEqual(a['digest'],b['digest'])

    def test_incomplete_or_old_manifest_keeps_conservative_global(self):
        (self.root/'team-context/roles/software-engineer.yaml').unlink()
        current=deps.observe(self.scope,self.role,root=self.root)
        self.assertFalse(current['complete']);self.assertEqual(deps.compare(self.before,current)['decision'],'conservative_global_required')
        self.assertEqual(deps.compare(None,self.before)['decision'],'conservative_global_required')

    def test_invalid_path_or_unknown_scope_never_searches_machine(self):
        for scope,role in [({'type':'project','project_id':'../private'},self.role),(self.scope,'../../secret'),({'type':'unknown'},self.role)]:
            self.assertFalse(deps.observe(scope,role,root=self.root)['complete'])


class CurationAndHealthTests(unittest.TestCase):
    def test_old_statements_verbatim_and_normalized_refs_accounted(self):
        mapping=json.loads((ROOT/'docs/repairs/2026-09-28/canonical-migration.json').read_text())
        self.assertEqual(len(mapping['reference_normalizations']),5)
        for item in mapping['old_fact_mappings']:
            d=parse_yaml((ROOT/f"project-context/teachers-app1/facts/{item['old_id']}.yaml").read_text())
            self.assertEqual(d['status'],'superseded')
            self.assertEqual(hashlib.sha256(d['statement'].encode()).hexdigest(),item['statement_sha256'])
            self.assertEqual(hashlib.sha256(json.dumps(d['source_refs'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),item['normalized_source_refs_sha256'])
            self.assertTrue(item['current_ids']);self.assertTrue(item['revalidate_on'])

    def test_all_fact_and_checkpoint_schemas_including_superseded(self):
        for path in (ROOT/'project-context').glob('*/facts/*.yaml'):
            schema=load_schema_file('fact.schema.json');self.assertEqual(Schema(schema,schema).validate(parse_yaml(path.read_text())),[],str(path))
        schema=load_schema_file('checkpoint.schema.json')
        self.assertEqual(Schema(schema,schema).validate(parse_yaml((ROOT/'project-context/teachers-app1/checkpoint.yaml').read_text())),[])

    def test_new_note_helpers_have_no_network_or_canonical_write_calls(self):
        import ast
        for name in ['context_quality.py','handoff_parse_cache.py']:
            tree=ast.parse((TOOLS/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    self.assertNotIn(node.func.attr,{'write_text','write_bytes','unlink','rmdir','mkdir','connect','urlopen','run','Popen'})
                if isinstance(node,ast.Import):
                    self.assertFalse(any(x.name in {'socket','requests','urllib','subprocess'} for x in node.names))

    def test_current_fact_size_and_meaning_boundaries(self):
        docs=[d for d in load_all_docs() if d.get('id','').startswith('FACT-TAPP1-') and d.get('status')=='active']
        self.assertEqual(len(docs),7)
        all_text='\n'.join(d['statement'] for d in docs)
        for term in ['AMBIGUOUS','identity_matched=false','NOT_READY','CHANGES_REQUIRED','NOT_RUN','OBSERVATION_ORDER_UNRESOLVED','MIGRATION_REQUIRED','teacher_stopped','已阻断','0.2.4','0.1.2','UNAVAILABLE','NOT_AVAILABLE','A03','A08','V01–V10']:
            self.assertIn(term,all_text)
        self.assertTrue(memory_health.health(project='teachers-app1')['ok'])

    def test_no_old_next_action_reintroduced(self):
        d=parse_yaml((ROOT/'project-context/teachers-app1/checkpoint.yaml').read_text())
        self.assertEqual(len(d['next']),4);self.assertTrue(any('阻断' in e['summary'] for e in d['open']))
        self.assertFalse(any('从 ba8e32d' in x for x in d['next']))

    def test_unchanged_ancient_supersession_chain(self):
        a=parse_yaml((ROOT/'project-context/teachers-app1/facts/FACT-TAPP1-000002.yaml').read_text())
        self.assertEqual(a['status'],'superseded');self.assertTrue(any(ref.endswith('/FACT-TAPP1-000001.yaml') for ref in a['relations']['supersedes']))

    def test_source_change_narrows_and_does_not_mutate(self):
        p=ROOT/'project-context/teachers-app1/facts/FACT-TAPP1-000012.yaml';before=p.read_bytes()
        report=memory_health.source_changes('teachers-app-one',['entry/src/main/ets/entryability/EntryAbility.ets'])
        self.assertIn('FACT-TAPP1-000012',[r['id'] for r in report['affected']]);self.assertEqual(report['canonical_writes'],0);self.assertEqual(report['agent_triggers'],0)
        self.assertEqual(before,p.read_bytes())

    def test_unrelated_repo_no_impacted_source(self):
        self.assertEqual(memory_health.source_changes('not-this-project',['README.md'])['affected'],[])



class ForwardSourcePinTests(unittest.TestCase):
    def test_new_source_pin_reproduces_and_does_not_rewrite_history(self):
        import chandoff_assignment as assignment
        from chandoff_instructions import digest_skill_dir
        current = digest_skill_dir(ROOT / 'skills/multica-context-handoff')['bundle_digest']
        self.assertEqual(assignment.ACCEPTED_SKILL_BUNDLES, (current,))
        self.assertEqual(assignment.FORWARD_SKILL_BUNDLE,
                         'sha256:30adc7319e0d8c65a4f7ec4c5018d127ec144f34e19807b839da8341ab6ccc73')
        self.assertNotEqual(current, assignment.FORWARD_SKILL_BUNDLE)
        self.assertIsNone(assignment.u05_mapping()['feature_reviewer_resolves_to'])

    def test_old_source_bundle_still_fails_closed(self):
        import chandoff_assignment as assignment
        from chandoff_instructions import digest_skill_dir
        old = digest_skill_dir(ROOT / 'skills/multica-context-handoff')
        old['bundle_digest'] = assignment.FORWARD_SKILL_BUNDLE
        with patch.object(assignment, 'digest_skill_dir', return_value=old):
            with self.assertRaises(assignment.U05BundleError): assignment.u05_mapping()

if __name__=='__main__':unittest.main()
