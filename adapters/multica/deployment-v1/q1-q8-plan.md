# Q1–Q8 verification plan (deduped; D4 executes)

Merge Q2+Q3+Q6+Q7 into one small docs/read-only chain after D3. D1 does not run 05/06. D1 does not create the new 05.

| Id | When | Minimum evidence | Not this batch |
| --- | --- | --- | --- |
| Q1 | D4 | 06 independently reads six live agents, Squad, mapping, Skill ids/content vs this after-state | Screenshot/name-only |
| Q2 | D4 | New 04 task with target + package + source only; real bound selfcheck command+result | Task-local instruction paste proving global bind |
| Q3 | D2 then D4 | New 05 (pre-D2 identity) reviews this exact correction SHA; later may review the Q2 artifact | Rename Feature Reviewer; reuse 03 review; review `58ba5d8` or `119812c` |
| Q4 | D4 synthetic dir | Wrong task/role, missing source, old-role/stale package refusals; no production writes | Production sabotage |
| Q5 | D4 | Selected real project source in authorized scope; existing Finding visibility; do not empty production | Empty synthetic as substitute for production facts |
| Q6 | D4 | `04 → Lead → 05 → Lead → 06 → Lead`; specialists do not start the next business role; no SAFE_DISPATCH | Auto-dispatch proven |
| Q7 | D4 | New work uses approved main/deploy path and source; not the old YZT-88 worktree | Git Merge without consumption proof |
| Q8 | D4 | One synthetic missing-package stop + one resume after fix | Global exactly-once |

## D1 limited tests this correction

- Bound worker selfcheck: READY / USE_EXISTING / exit 0; package `CTX-software-engineer-ca95e5f1cce82f65`; observer `01a09521-7050-7b5e-b2e9-5aa218a020c7`
- Findings snapshot `a6737e12…`; binding `9af94753…`; open Finding visible, not associated
- Adapter LF `ed373149…` matches
- Candidate `58ba5d8` clean, 83 ahead of `95c434d`
- Isolated branch before this commit: `119812c` clean, 85 ahead of `95c434d`
- Static/synthetic: after-state and necessary Skills grepped for UUID reuse, SAFE_DISPATCH, autonomous refresh, Finding drain, Feature Reviewer routing (see `static-validation.md`)
- CLI: `agent skills` has add/list/set only; `agent skills remove` unavailable
- Did **not** run full discover, production ledger scans, live apply, or the 580-test suite on this attempt
