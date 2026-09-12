# QA baselines for D4 (formed from accepted materials; 02 owns PE authority)

## Product Expectation

Human-accepted for this batch: reduce ordinary collaboration cost; accurate context; allow manual start and correction; Lead is the only normal router; new 05/06 duties. Not APP1/Web-ImageGen product quality.

Authority to confirm: 02 Context Engineer (D1 lists this as a Lead-routed 02 dependency; 04 does not write Canonical / Role Profile).

## Design Baseline

Supervised manual start; explicit package; bound SELF_CHECK; explicit Findings source; role mapping from profiles; results return to Lead; failures handled by humans. Auto assignment / T06 full discover / SAFE_DISPATCH / Finding drain / strong worker gate remain deferred.

## Build / config

- Memory main before: `95c434d` (0)
- Code start: `58ba5d8` (0 dirty / 83 ahead of main)
- Historical D1 pin: `119812c` (0 dirty / 85 ahead; CHANGES_REQUIRED)
- This correction bundle: `42ca7fc` (86 ahead of main); **not** `58ba5d8` / `119812c`
- Skill content: shared handoff LF `sha256:a7326f93…`; delivery-review and product-quality-gate as patched in this commit
- Six-role after-state files under `after/`
- Live snapshot after D3 (not yet)

## Review

D2 `APPROVE / CHANGES_REQUIRED / ESCALATE` on this exact correction candidate. 03 historical read-only review is not this gate. Pre-D2 new 05 bootstrap is a Lead/Human dependency, not proof of D3.
