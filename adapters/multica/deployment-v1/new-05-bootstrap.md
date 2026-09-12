# New 05 Delivery Reviewer — pre-D2 bootstrap vs D3 full apply

04 does **not** create this live agent. Lead / Human executes the bounded pre-D2 steps. D2 then reviews this exact correction candidate. D3 is a later, separate apply.

## Pre-D2 bootstrap (bounded; required before D2)

Goal: an independent 05 that can review **this** D1 correction candidate. Not a six-role cutover.

1. Create a **new** agent. Display name: `05 Delivery Reviewer`. Logical role: `delivery-reviewer`.
2. New platform UUID is assigned at create time. **Do not reuse** `b6335f8e-8147-45f7-aac0-8079d85423b5`.
3. `multica agent update <NEW_05> --instructions` from `after/delivery-reviewer.md` (full replacement, not the Feature Reviewer persona).
4. Create/import `delivery-review` from `skills/delivery-review/SKILL.md` (correction-1 text; no `TASK_FINDING_DRAIN`).
5. `multica agent skills add <NEW_05> --skill-ids aab482f9-b9ff-4b78-8394-1f13e2b319c0` (`parent-handoff-wake`) if missing, then add the new `delivery-review` id.
6. Runtime: existing available local runtime. Read-only review. No product write. No Canonical write.
7. Scope: review this D1 correction candidate only. **Do not** join business Squad routing, rewrite other agents, or swap QA skills.

Forbidden in pre-D2:

- Renaming live `05 Feature Reviewer`
- Copying old 05 sessions, packages, or instruction digest `sha256:51cd0ddd…`
- Letting the new 05 certify its own bootstrap as the only proof (Lead/Human confirms create; 06 later reads live config in D4)
- Importing/binding shared handoff to 01–04/06
- Squad instruction rewrite
- QA `milestone-quality-gate` unbind
- `agent skills set` replace-all

## D3 full application (blocked until D2 APPROVE + Human publish confirmation)

Then, and only then:

- Import `multica-context-handoff` and `product-quality-gate`
- `agent skills add` shared handoff to 01–04, 06, and the new 05
- `agent skills add` `product-quality-gate` to 06
- Apply `after/*.md` instructions to 01–04/06 and squad
- `squad member add` the new 05; remove old 05 from normal Squad routing
- Logical unbind of `milestone-quality-gate` remains **CLI-unavailable** (see `cli-operations.md`); do not use replace-all `set`

Exact supported commands and rollback: `cli-operations.md`.
