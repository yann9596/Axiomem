#!/usr/bin/env python3
"""Fixed SYNTHETIC request calibration across exact repository snapshots.

This is not replay of original live task requests (not included in the audit).
No publication, assignment, device, Findings drain or production mutation occurs.
The requests use an explicit empty synthetic Findings input, never a real source.
"""
from pathlib import Path
import argparse
import sys, json
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', type=Path, required=True, help='isolated source snapshot')
args = parser.parse_args()
root = args.repo.resolve()
if not (root / 'tools/chandoff_plan.py').is_file():
    parser.error('not a context repository snapshot')
sys.path[:0] = [str(root / 'tools')]
import chandoff_plan as p,chandoff_compose as c,chandoff_finalize as f,chandoff_note as note
roles=['engineering-lead','context-engineer','solution-architect','software-engineer','delivery-reviewer','qa']
topics=['P01 platform storage implementation integration A03 A08 NOT_RUN 设备签名 当前基线','G01 G02 G06 homework roster L1 schema4 教师结束 实现 边界']
rows=[]
size=lambda d:len(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode())
for i,role in enumerate(roles):
 for j,topic in enumerate(topics):
  req={'schema_version':'1.1','kind':'prepare_handoff_request','task_ref':f'multica://issue/11111111-2222-4333-8444-{i*2+j+1:012d}',
   'project':{'project_id':'teachers-app1'},'target':{'role':role},'purpose':'implementation',
   'task_snapshot':{'title':topic,'description':'OFFLINE synthetic fixed request; no live Findings/authority/platform receipt is asserted.',
    'requirements':['preserve exact source, approved scope, current blockers and NOT_RUN boundaries'],
    'acceptance_criteria':['byte accounting without hidden truncation'],'relevant_decisions':[]},
   'caller':{'role':'engineering-lead'},'options':{'limit':8}}
  pe=p.prepare_handoff_plan(req,findings=[])
  row={'fixture_id':f'{role}:{j+1}','request_sha256':__import__('hashlib').sha256(json.dumps(req,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'plan_status':pe['status']}
  if pe['status']=='PLAN_READY':
   row['plan_bytes']=size(pe['plan']); proposed=c.subset_result(pe['plan']);ce=c.compose_semantic(pe,proposed)
   result=f.finalize_handoff(pe,ce,req,clock=lambda:'2026-09-28T00:00:00Z');pkg=result['package']
   row.update(status=result['status'],package_bytes=size(pkg),envelope_bytes=size(result),facts=len(pkg['current_facts']),checkpoint_entries=len(pkg['team_state_slice'])+len(pkg['project_state_slice']),source_refs=len(pkg['source_refs']),fact_bytes=[size(x) for x in pkg['current_facts']],stop_reason=result['escalation'].get('reason'))
   if result['status']=='READY':
    body,_=note.render_note_record(result,prepared_by='11111111-2222-4333-8444-000000000000',prepared_at='2026-09-28T00:00:00Z')
    row['transport_bytes']=len(body.encode())
  else:row['stop_reason']=pe.get('escalation',{}).get('reason')
  rows.append(row)
print(json.dumps({'kind':'offline-fixed-request-calibration','live_platform_evidence':False,'semantic_selector':'deterministic subset_result; not a measured live LLM selection','rows':rows},ensure_ascii=False,indent=2))
