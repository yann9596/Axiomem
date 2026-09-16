# F-04 / F-05 proposal v1 — exact pin and whitelist change list

- kind: `yzt111_f04_f05_proposal`
- revision: **v1**
- task: `multica://issue/YZT-111`
- based_on implementation: this commit on top of `a243c6ff440aa0ef8a1a01c224f5aebf79009927`
- status: **proposal only**. No pin, whitelist, or committed-bundle refresh was executed.

F-04 = pin baseline approval. F-05 = immutability / executing-file pin refresh after an approved generator change. Historical frozen evidence must be retained; a new baseline may be **appended** after Lead approval.

## A. Implemented this wave (not a pin refresh)

| file | old LF digest (HEAD `a243c6ff` blob) | new LF digest (this implementation working tree, LF-normalised) | source version | lineage | reason | failures covered | historical vs new |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `tools/chandoff_joint.py` | `sha256:0fc9190f68fba84a6aebcf7c7335c5dd37978e1fceb1456ca00b5de130019ed4` | `sha256:6e28025cae9cc6e33fff24bc93bfe5d591d7e283771389c267a110da8aee8d1d` | U11 generator at `6439bd429af0aa9ec4b95d838643bd8b633ed053`, then F-03 `83386809`, then F-07 `a243c6ff` | YZT-80 → YZT-105 F-03 → YZT-109 F-07 → YZT-111 | two `store-chain.json` hashes must use `normalize_lf=True` | F-07-NEW-001; `StoreChainNormalizeLfTests` | new executing baseline (code) |
| `tools/tests/test_joint_replay.py` | `sha256:c008dccae6cec4806717a67daad441e3aa3b7f339497b13aebc23b72ffd1654d` | `sha256:41bff44ba42df6bd4ec31468f9991134676837bbebfe73fac4dd976de73f4cbb` | U11 focused tests | YZT-80 / YZT-105 GateA tests as pattern | directed regression for the two call sites | the three new tests | new executing baseline (tests) |

Exact generator diff (already applied):

```diff
--- a/tools/chandoff_joint.py
+++ b/tools/chandoff_joint.py
@@ artifact_set_manifest (line 211 at a243c6ff)
-        "source_digest": file_digest(U10_FIXTURES / "store-chain.json"),
+        "source_digest": file_digest(U10_FIXTURES / "store-chain.json", normalize_lf=True),
@@ compatibility_manifest (line 2446 at a243c6ff)
-        "u10_store_chain_digest": file_digest(U10_FIXTURES / "store-chain.json"),
+        "u10_store_chain_digest": file_digest(U10_FIXTURES / "store-chain.json", normalize_lf=True),
```

## B. Allowed new baseline — regenerate committed U11 derived files (do **not** execute now)

These two files are the only generated artifacts whose bytes move because of F-07-NEW-001. `generated_at` stays `2026-09-11T00:00:00Z`. Suggested command after approval: `python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay` then keep only the two files that differ.

| file | old committed digest (`a243c6ff`) | new scratch digest (LF=CRLF) | original source | lineage | reason | failures covered | class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `adapters/multica/joint-replay/artifact-set.json` | `sha256:373ffbe86a05526160c8891b56e36dbda7ac08c5282af704de1b2628264c6462` (embeds store-chain raw `sha256:9c754a93…`) | `sha256:6090ef9bb7b9821504b83679d53c01f1e0022ea9887fa4a624bac85b5f420377` (embeds LF `sha256:d9eb3121…`) | U11 evidence bundle | YZT-80 / YZT-109 F-07 | committed bytes still use the CRLF-mode digest | `BundleTests.test_committed_bundle_regenerates_byte_for_byte` | **allowed new baseline** (append regen). Do not rewrite YZT-109 reports. |
| `adapters/multica/joint-replay/compatibility-pin-manifest.json` | `sha256:2db0869edd13ec0902b2eb53e9ecc6e9ff62531691fb0056e13e781ff4e3bf18` | `sha256:77f92fad28f8e7a326a2798177895c7f0f210391a4402c19e78a3fb143b17224` | U11 compatibility manifest | YZT-80 / YZT-109 | same store-chain digest field `u10_store_chain_digest` | same byte-for-byte test | **allowed new baseline** |

17-file bundle digest (`bundle_digest`, capture excluded):

| | digest |
| --- | --- |
| committed at `a243c6ff` | `sha256:50e702dac5aa3f6ed3cb0d47538488939e0eec249bf31ea22809a5b174113b5e` |
| scratch after this generator | `sha256:c1c51f6ef07801f0dbb5010a941346adde112c54048d19ec4f04206192e07ec9` |
| committed 18-file incl. capture (unchanged here) | `sha256:2047cc98737ea5fd82e7b7b3cb0199afe2480ef41c9bf0eca54a21fa6e1228c9` |

The other 15 generated files, including `final-gate-matrix.json` `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868`, are byte-identical and must not be rewritten.

## C. F-05 executing-file pin — `chandoff_joint.py`

| file | old pin | proposed new pin | original source version | lineage | reason | failures covered | class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `tools/u12_preflight.py` `PIN_FILES_LF["tools/chandoff_joint.py"]` | `sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208` (U11 `6439bd4`) | `sha256:6e28025cae9cc6e33fff24bc93bfe5d591d7e283771389c267a110da8aee8d1d` (this implementation LF) | U12-P0 pin table | YZT-82 / YZT-84 / YZT-105 F-03 already drifted this pin to `0fc9190f…` without refresh | executing LF digest of joint.py after F-07-NEW-001 | `PinVerificationTests.test_real_pins_reproduce_and_discrepancy_recorded` | **allowed new baseline** of the pin table, not of historical pin-verification JSON |

Suggested diff (do not apply now):

```diff
--- a/tools/u12_preflight.py
+++ b/tools/u12_preflight.py
     "tools/chandoff_joint.py":
-        "sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208",
+        "sha256:6e28025cae9cc6e33fff24bc93bfe5d591d7e283771389c267a110da8aee8d1d",
```

Intermediate F-03 digest `sha256:0fc9190f…` was never pinned; skip it. Pin the digest that matches the approved generator revision.

`tools/u12_r0_binding.py` pins `tools/chandoff_joint.py` by **commit** `6439bd429af0aa9ec4b95d838643bd8b633ed053` plus digest method LF. That commit blob stays `2afc2293…`. Updating the binding to a later commit is a separate F-05 identity change and must name the new exact commit after this implementation is accepted. Not guessed here.

## D. F-04 predecessor pins — assignment / mention (verified source)

U11 `compatibility_manifest()` and `PredecessorGuardTests` still pin U11-era bytes. Actual LF digests at `a243c6ff` / this wave (unchanged by this patch):

| file | U11 pin (`49c48a9` era) | actual LF now | source of drift | class |
| --- | --- | --- | --- | --- |
| `tools/chandoff_assignment.py` | `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129` | `sha256:acc67d823b7ea4f646f342723e463704196ac815d820ae518ce9c8e119cbcb1c` | post-U11 yzt-88: `261df9a`, `d2b6299`, `2efb52f`, `58ba5d8` | **allowed new pin baseline** after Lead approval |
| `tools/chandoff_mention.py` | `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1` | `sha256:6b95dc8a61fc983bc0f9bd716da4bf3dd170f05ecb0639d75c64242cf90f6f19` | same four commits | **allowed new pin baseline** |

Lineage is **verified** from `git log 49c48a9..HEAD -- tools/chandoff_assignment.py` and the mention path. Not UNRESOLVED.

Unchanged matching pins (do not retouch):

| file | pin / actual LF |
| --- | --- |
| `tools/chandoff_finding.py` | `sha256:efa29010b5a0b07aa76a329c107b903a54f342e8fa88cc3e99a384a121273848` |
| `tools/chandoff_dispatch.py` | `sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa` |
| `tools/chandoff_fallback.py` | `sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b` |

Suggested `compatibility_manifest` pin table diff (do not apply now):

```diff
-        "tools/chandoff_assignment.py": "sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129",
-        "tools/chandoff_mention.py": "sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1",
+        "tools/chandoff_assignment.py": "sha256:acc67d823b7ea4f646f342723e463704196ac815d820ae518ce9c8e119cbcb1c",
+        "tools/chandoff_mention.py": "sha256:6b95dc8a61fc983bc0f9bd716da4bf3dd170f05ecb0639d75c64242cf90f6f19",
```

Mirror the same two strings in `PredecessorGuardTests.test_predecessor_pins_reproduce`. Covered failures: `FinalGateTests.test_compatibility_pins_reproduce`, `PredecessorGuardTests.test_predecessor_pins_reproduce`. After those two pins match, `all_reproduce` still depends on o2/u09 bundle pins (`sha256:3c207e85…` / `sha256:d598d2c5…`), which currently match.

## E. Historical frozen evidence — do **not** rewrite

| artifact | why frozen | note |
| --- | --- | --- |
| `adapters/multica/u11-f07/lineage-report-v1.md` and `evidence-manifest-v1.json` | YZT-109 F-07 delivery | records F-07-NEW-001 as unrepaired; keep |
| `adapters/multica/U11_JOINT_END_TO_END_REPLAY_REPORT.md` revision 2 | F-07 report | needle `14b892c2…` stays valid |
| `adapters/multica/u12-p0/u11-o2-pin-verification.json` | U12-P0 contemporaneous pin readback | `file:tools/chandoff_joint.py` match at `2afc2293…` |
| `adapters/multica/u12-p0r/pin-verification.json` | U12-P0R contemporaneous pin readback | same |
| `adapters/multica/u12-r0bi/implementation-manifest.json` | R0B implementation record at `6439bd4` | digest `2afc2293…` is the blob of that commit |
| `adapters/multica/u12-r0b-forward/implementation-manifest.json` | forward record | same class |
| `tools/u12_preflight.py` `PIN_BUNDLES["adapters/multica/joint-replay"]` `sha256:04e0da84…` and `PIN_MATRIX_FILE` `sha256:a9ecc24c…` | U11-original bundle/matrix | already stale after YZT-109 (`2047cc98…` / `d8f7c84c…`); refresh only as a **new** pin row after an approved regen, never by editing old verification JSON |
| `migration/gate-results/*` | Gate A/B/C historical | out of scope |

## F. Failures this proposal would close vs leave

After Lead-approved application of B+C+D (still not done):

- `BundleTests.test_committed_bundle_regenerates_byte_for_byte` — would pass if B is applied
- `PredecessorGuardTests.test_predecessor_pins_reproduce` — would pass if D is applied
- `FinalGateTests.test_compatibility_pins_reproduce` — would pass if D is applied and o2/u09 pins remain matching
- `PinVerificationTests.test_real_pins_reproduce_and_discrepancy_recorded` — would still fail until C **and** the U11-original joint-replay/matrix pins in `u12_preflight.py` are moved to the then-approved bundle/matrix (YZT-109 leftovers). Treat as a later F-05 row, not silently batched.

Do not batch-accept "whatever the working tree currently hashes to". Each row above is an exact old → new pair with lineage.
