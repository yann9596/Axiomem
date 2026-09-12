# Correction-1 targeted static / synthetic validation

No live apply. No production ledger. No full discover.

## Checks run

| Check | Result |
| --- | --- |
| Bound selfcheck READY / USE_EXISTING | package `CTX-software-engineer-ca95e5f1cce82f65`; observer `01a09521-7050-7b5e-b2e9-5aa218a020c7`; exit 0 |
| `--role software-engineer` added | packaged request omitted `role`; argv completed |
| Snapshot / binding | `a6737e12…` / `9af94753…`; open Finding visible, not associated |
| Adapter LF | `ed373149…` |
| Kept candidate | `58ba5d8` clean, `95c434d..58ba5d8` = 83 |
| Isolated branch before this commit | `119812c` clean, `95c434d..HEAD` = 85 |
| `binding-plan.json` | `json.loads` ok |
| `agent skills remove --help` | command does not exist; only `add` / `list` / `set` |
| After-state required-action leftovers | none of: `UUID 可在 cutover 时沿用`, `用户行为疑问交 Feature Reviewer`, `按需调用 … Feature Reviewer`, `走有界自刷新路径`, `Then TASK_FINDING_DRAIN`, specialist `BEFORE HANDOFF — 向下游专业角色派发`, imperative `- drain task Findings` |
| Necessary Skills | `delivery-review` and `product-quality-gate` no longer instruct `TASK_FINDING_DRAIN`; they forbid SAFE_DISPATCH / autonomous refresh |
| SAFE_DISPATCH remaining mentions | only prohibitions / retain-disabled classification |

## Not run

- Live `agent create` / `skill import` / `squad update`
- `agent skills set`
- Memory main Merge
- 580-test suite
- T06 full discover
