# New 05 Delivery Reviewer bootstrap (D1 candidate only)

04 does **not** create this live agent. Lead / Human creates it, then D2 uses it for the formal review of this release candidate.

## Required identity

- Display name: `05 Delivery Reviewer`
- Logical role: `delivery-reviewer`
- New platform UUID: assigned at create time. **Do not reuse** `b6335f8e-8147-45f7-aac0-8079d85423b5`.
- Instructions: `after/delivery-reviewer.md` (full replacement, not the Feature Reviewer persona)
- Skills after import: `parent-handoff-wake` (existing id `aab482f9-…`), `delivery-review` (import from `skills/delivery-review/`), `multica-context-handoff` (import from `skills/multica-context-handoff/`)
- Forbidden skills: `external-signal-research`, `feature-correctness-review`
- Runtime: existing available local runtime. Read-only review. No product write. No Canonical write.
- Scope this batch: review this D1 manifest only; do not join business Squad routing until D3.

## Reject

- Renaming live `05 Feature Reviewer`
- Copying old 05 sessions, packages, or instruction digest `sha256:51cd0ddd…`
- Letting the new 05 certify its own bootstrap as the only proof (Lead/Human confirms create; 06 later reads live config)
