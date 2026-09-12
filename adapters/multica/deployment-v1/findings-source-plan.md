# Findings source plan

Do not infer the Findings root from the current git worktree.

## This attempt (already bound)

Lead comment `01a09516-ebcf-7ce2-870e-f078acb0a390` on YZT-66 (correction-1 authority; do not reuse `01a094ec`):

```text
source_id: web-imagegen-runtime-findings
root: D:/AI/multica-memory/runtime/v1.1/findings
reader_resolved: D:\AI\multica-memory\runtime\v1.1\findings
layout: flat-finding-json-v1
project_id: web-imagegen
task_ref: multica://issue/YZT-73
synthetic: false
```

Worker selfcheck used that binding. Snapshot `sha256:a6737e12…`. Binding `sha256:9af94753…`. Open `FIND-WIMG-HO00-000001` stays visible and **not** associated to YZT-73. Do not re-associate, empty, or close it to manufacture READY. Do not drain Findings.

## After D3 (plan only)

- Code consumption: Memory **main** at the approved release SHA, passed as `--repo`. Not `D:/AI/worktrees/multica-memory-yzt-88-u12fsb` and not an implicit checkout root.
- Findings root stays the authorized path above unless Lead issues a new source-binding comment. No data cutover in this batch.
- Placeholder skill ids must be replaced by real catalog ids at apply time. Pre-D2 may resolve `delivery-review`; D3 resolves the rest.
