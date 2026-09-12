# Findings source plan

Do not infer the Findings root from the current git worktree.

## This attempt (already bound)

Lead comment `01a094ec-877b-7d97-9be5-ce65dcd27a8c` on YZT-66:

```text
source_id: web-imagegen-runtime-findings
root: D:\AI\multica-memory\runtime\v1.1\findings
layout: flat-finding-json-v1
project_id: web-imagegen
task_ref: multica://issue/YZT-73
synthetic: false
```

Worker selfcheck used that binding. Snapshot `sha256:a6737e12…`. Open `FIND-WIMG-HO00-000001` stays visible and **not** associated to YZT-73. Do not re-associate, empty, or close it to manufacture READY.

## After D3 (plan only)

- Code consumption: Memory **main** at the approved release SHA, passed as `--repo`. Not `D:/AI/worktrees/multica-memory-yzt-88-u12fsb` and not an implicit checkout root.
- Findings root stays the authorized path above unless Lead issues a new source-binding comment. No data cutover in this batch.
- Placeholder skill ids must be replaced by real catalog ids at apply time. This D1 bundle still uses placeholders because import has not happened.
