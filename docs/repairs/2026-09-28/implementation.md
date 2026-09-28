# Memory lifecycle audit repair — implementation and boundaries

Date: 2026-09-28. Baseline: `9213a0e1a8e95e6fc3684e878fe8170846aba76c`. The preliminary source branch at `c2bdbfeef052b85ec51b3f40b04d8166c22dcc7e` added only isolated CI and the bounded authorization. The actual repair is the subsequent source delta in PR #2. **The project remains paused. A source commit is not deployment, independent approval or permission to resume.**

## Implemented source changes

| Area | Implementation | Deliberately NOT claimed |
|---|---|---|
| Canonical lifecycle | Supersede long Facts 000003/4/5/6; retain their statement bytes; seven topic-scoped successors 000007–13; Checkpoint 16/5/0/45 becomes 4/6/0/4 (confirmed/open/conflicts/next). | No new platform truth, product decision, completed pending task, or deletion of historical evidence. |
| Reference validation | Fact schemas are now actually checked by validate_canonical. Five pre-existing invalid reference encodings are explicitly normalized to immutable public source pointers, with before/after mapping. | Authority failures are not converted to approvals. Original source bytes remain at the exact baseline commit. |
| Index | Canonical status and path projection; same-byte snapshot revision; exclusive rebuild lock; temp build/check/fsync/atomic DB replacement; failure retains old DB; read-only check-index. | No full-table semantic theorem or fact truth verification. Formal Handoff reads YAML, not SQLite. Legacy V1 index/CLI stays untouched. |
| Context quality | Trusted UTF-8 byte policy; full admitted PLAN source checks; FINALIZE; post-artifact/pre-seal envelope; actual transport bytes; consumer/SELF_CHECK; direct compatibility builder. | Bytes are not tokens. No truncation, request-level budget override, new public status/schema, or false READY. |
| Discovery | Bounded content+parser/schema-version parse cache; copies prevent poisoned cache mutation; every discovery still enumerates the complete live thread. | **Zero network reads saved.** No assumed backend watermark, disk locator authority, newest-invalid fallback, or bypass of package-ID conflict. |
| Dependencies | Scoped shadow manifest includes declared-scope candidate directory, new/deleted/revoked objects, role, policies, authority evidence, registry and explicit artifacts. | **Global authoritative freshness is unchanged.** A shadow unchanged result cannot authorize READY. |
| Governance | Read-only memory-health and repository/path source-change proposals, owned by Lead/02 through existing Memory Disposition. | No auto-promotion, auto-trigger, second governance ledger, or semantic inference from age. Unbound/indirect dependencies still need evidence review. |
| Binding | A new exact source Skill bundle pin versions the current code forward; previous U04/U05/forward constants and historical evidence remain. | Source acceptance does not prove the Windows/shared Skill database has been updated. Old installed bytes must fail, not silently pass. |

The source consumer search found no V1.1 SQLite query consumer other than index tooling/checks; formal PLAN and compatibility build read Canonical YAML. `check-index` is the explicit deployment/administrative acceptance entry, not an invented runtime SQLite dependency.

## Migration and safety review

Read `canonical-migration.json` alongside all seven successor Facts, the original four Facts at the baseline and the new Checkpoint. It records immutable statement hashes, per-paragraph source mappings, current topic destinations, every old Checkpoint entry, exit/revalidation conditions and five reference normalizations. This is an audit deliverable, not a new Memory Core type. A paragraph map is not a claim of live source revalidation.

The current snapshot preserves: independent project identity, exact source/version and approved narrow G01/G02/G06 choices; unresolved Q1–Q6 branches; old negative verdicts; the 0.2.3 live AMBIGUOUS receipt versus 0.2.4 offline replay; YZT-202 generation-specific signed custody; NOT_READY/NOT_RUN and the paused-project condition. It neither installs anything nor grants cleanup, product-main merge or release authorization. No raw audit/chat/runtime/signing input is published.

## Budgets and calibration

`team-context/policies/context-quality.json` is trusted repository policy, not a model-controlled request option. Initial enforced limits are: PLAN 98,304 B; package 65,536 B; envelope 69,632 B; actual publication 98,304 B; Fact 8,192 B; Rule 16,384 B; individual Checkpoint 4,096 B; at most 24 Checkpoint entries; at most 128 top-level refs/16,384 B; total facts 32,768 B; Checkpoint slices 24,576 B; admitted source object 16,384 B. Detailed diagnostics name the object and limit. Original count-budget error codes remain compatible.

`offline-quality-calibration.json` compares 12 **synthetic fixed requests**, identical request hashes, six roles and two topics with the deterministic subset selector. It is not the actual YZT-221 request and cannot be equated to the original audit's 190,404 B example. Original live request/envelope bytes were not included in the supplied audit. Final envelope/transport measurements include frozen metadata but no live required-artifact enrichment; adversarial synthetic enrichment has separate regression tests. 01 must replay the real workload and required artifacts before lifting the pause. A necessary oversized package is routed for curation or task narrowing; it is not cut or granted a model-controlled exemption.

## Validation and CI interpretation

Run the two new offline regression files, then inspect `validation.json` for exact results and the full-suite differential. The old full suite is already red in a clean source archive: authority/product source dependencies, an absent live Multica CLI and historical fixtures are not available here. No historical expected verdict or evidence pin is rewritten to claim a pass. The import allowlist adds only the two explicitly read-only helper modules; the new tests check their side-effect boundary.

CI separates new regression tests, current Fact schema/health/index checks and a full baseline differential in temporary archives. A differential pass means **no newly failing test identities**, not that the complete suite passed or all failure causes are equivalent. Both raw suite exit codes and failure identities are exported. The authority gate is reported without waiving or approving it. No CI run publishes a Context or invokes a production task.

```text
python -m unittest discover -s tools/tests -p test_index_lifecycle_audit.py -v
python -m unittest discover -s tools/tests -p test_memory_audit_repair.py -v
python tools/context_cli.py validate-canonical
python tools/context_cli.py rebuild-index
python tools/context_cli.py check-index
python tools/memory_health.py audit --project teachers-app1
python tools/memory_health.py source-change --repo teachers-app-one --path entry/src/main/ets
python tools/replay_memory_quality.py --repo <isolated-source-snapshot>
```

`validate-canonical` writes its existing gate-results artifact; run tests and validation in an isolated checkout. Never drain or reset real Findings/ledger to reproduce synthetic tests. A lock/SQLite sidecar or changing Canonical blocks replacement; operators must reconcile the writer, not force-delete the evidence.

## Delivery and rollback

01 applies `01-external-issues.md` after exact-commit independent review. Do not recreate implementation tasks for already delivered code, but resolve any genuine review defect. Cross-run files use authorized durable storage and exact digests. The user's pause remains a separate release condition.

Rollback uses a reviewed forward revert, never force-push. An old index is not fresh merely because it exists. Canonical correction must retain real current constraints; blindly restoring historical active truths is not a safe rollback. Scope isolation, source/authority binding, latest-bad-record blocking and exact artifact checks remain mandatory.
