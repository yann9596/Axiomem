# SELF_CHECK protocol

Determine whether an already-started role can keep using its existing Role
Context Package for the current task. Run with the verified Context
repository root as `--repo` and a scratch `--out-dir`.

## Step 1 — Resolve the request identity

The frozen `self_check_request` needs `task_ref`, the **current** role (the
role that is about to continue work — never a handoff target role), and the
same normalized task snapshot the caller would send to PREPARE_HANDOFF.

- Recommended deterministic path: first build the request from the **current
  authoritative task content** (an offline PREPARE_HANDOFF snapshot of the
  current issue gives you a fresh `prepare_handoff_request`), then run
  SELF_CHECK with `--request-from <that fresh request>`. This guarantees the
  fingerprint comparison is against the task as it stands now.
- `--request-from <file>` derives the request verbatim from whatever
  prepare_handoff_request you point it at: same task_ref, snapshot, and the
  CALLER role as current role. **Never derive it from an older work round's
  request envelope** — a superseded snapshot is checked against the package
  built from that same superseded snapshot, which returns a meaningless
  READY/USE_EXISTING. When only an older envelope is at hand, rebuild the
  snapshot from the current issue first and check against that.
- Or pass `--request-file <self_check_request.json>` (with optional explicit
  `--task-ref` / `--role` overrides for caller-confirmed corrections).
- `--package-ref <package_id>` pins a specific package when the caller
  explicitly wants that one checked; otherwise the latest valid package for
  `task_ref + current role` is resolved by discovery.

## Step 2 — Discovery + check (deterministic)

```bash
python <skill>/scripts/handoff_pipeline.py selfcheck \
  --repo <root> --out-dir <scratch> \
  --issue <issue-id> --request-from <previous-request-envelope.json> \
  --findings-source-binding-file <binding.json> \
  --findings-trusted-map-file <trusted-map.json> \
  --findings-evidence-file <prior-observation.json>
```

Do not substitute `tools/context_cli.py self-check` or a direct T04 call
without the same Findings source binding. Those remain internal callables
and are unsupported for formal dispatch. The raw command, exit code, and
JSON result (including `task_ref`, `role`, `package_id`, source/package
digests) are the inspectable self-check evidence — a natural-language
"I already checked" statement is not.

(Offline variant: replace discovery with `--envelope-file <result.json>`;
`--store <dir>` scans a runtime package store.)

T06 discovery reads the issue's `/note` CONTEXT_HANDOFF records and resolves
the latest **valid** package for `task_ref + current role`. Its ordering and
trust authority is the server comment `created_at` and comment id — never an
embedded `prepared_at`. When discovery reports a newer invalid or conflicting
same-target candidate, it fails closed; you must **not** fall back to an
older caller-supplied package or an earlier artifact. T04 then validates
task/role/scope/status/fingerprint/revisions and, for Registry-verified
package scopes, reuses the T01 Finding Gate verbatim with the current-role
boundary. None of that logic is reimplemented here.

When the discovered package carries an accepted artifact dependency set, or
the caller supplies `--artifact-requirements-file`, also pass
`--artifact-store-file` with the exact envelope store. The pipeline
re-resolves the required identity/version set through `cartifact` and
compares it with `dependency_changed`. A digest mismatch, a newly stale or
superseded artifact, an ambiguous or missing exact version, or a now
ineligible status fails closed. The skill maps that failure onto the frozen
T04 reason `package_stale` and status `REFRESH_REQUIRED`. T04 BLOCKED is
not rewritten. Missing store for a declared or previously exported set is
also fail-closed — never guess `latest` / `current`. A bounded refresh must
run PREPARE_HANDOFF and build a fresh package before consequential work.

## Step 3 — Map the verdict (deterministic)

- **READY / USE_EXISTING** (`consequential_work: "allowed"`) → the discovered
  package may be used as-is; consequential work may continue. This path must
  not call, mention, or wake the Context Engineer or any other agent.
- **REFRESH_REQUIRED / REFRESH** (`consequential_work:
  "stopped_until_refreshed_ready"`) → run **PREPARE_HANDOFF** for the **same
  task and the current role** — the payload's `refresh.task_ref` and
  `refresh.target_role` name exactly that binding; do not retarget another
  task or role. Consequential work stays stopped until the refreshed
  handoff returns READY, and the refreshed package is then re-checked if the
  caller requires it.
- **BLOCKED / ESCALATE** (`consequential_work:
  "stopped_escalation_required"`) → stop consequential work. Surface the
  exact public `reasons` (frozen vocabulary only) plus the available
  discovery provenance (`provenance.comment`). Record the escalation need —
  but do not mention, assign, dispatch, or start the Context Engineer or any
  other agent; later workflow stages own that routing.

## Result contract

Return the pipeline JSON verdict: `status`, `action`, `reasons`,
`package_id`, `provenance`, `consequential_work`, and the artifacts dir
(`self-check-request.json`, `self-check-result.json`). On any bounded stop
(discovery fail-closed, malformed request), return the error envelope instead
and stop — never guess around it.


## Parent-container handoff within current project authorization

The task identity and publication container are distinct. In Lead's current parent issue, publish the finalized child-task envelope as the ONE final non-trigger comment of that run; use the real trigger comment as --parent when available. The envelope task_ref stays the child. Only after confirmed publication does Lead perform one native assignment. No second summary/parent mention and no unverified SAFE_DISPATCH.

For worker SELF_CHECK, build a fresh request from the CHILD issue and its role, but pass the declared PARENT issue to --issue for discovery. Carry exact task binding, trusted map and prior source observation. A parent package for another task is not eligible. Do not change fingerprints to the parent identity.

The producer task is done after its stated deliverable goal is actually met and its single child comment carries exact artifact and validation evidence. Independent review is a separate stage/task. Native stage completion wakes Lead; no additional normal-completion mention. A blocked task stays blocked and can mention Lead in its single child blocking comment. done does not mean merged or milestone accepted.

The single real authorization source can be reused only inside its declared scope. Generate exact per-task binding/map after authenticated project/parent-chain checks; no wildcard task lists or invented authors. New package generation inside unchanged authorization is Lead's responsibility, not another Human-start gate. Keep fail-closed identity/source/freshness checks.

For R1 non-code review, the artifact requirements document may carry explicit reviewed_artifact (artifact_type, artifact_id, exact version), also present as a required dependency. Without it, the legacy implementation requirement remains. Wrong owner, stale/superseded or unmatched subject fails. QA's full R2 baseline remains required. Keep this requirements document at finalize, publish and consumer selfcheck; omission fails closed for a non-code review.

