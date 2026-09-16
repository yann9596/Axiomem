# YZT-113 pin-refresh-manifest v1 — D→B→C exact old → new digests

- kind: `yzt113_pin_refresh_manifest`
- revision: **v1**
- task: `multica://issue/YZT-113`
- based_on: `403e7758d9908410e636cc3b923c879cfd620340` (tree `899d12859f322f7e29ff358cc9713dc16306da5d`; parent `a243c6ff440aa0ef8a1a01c224f5aebf79009927`)
- algorithm: SHA-256 of file bytes; `PIN_FILES_LF` and predecessor pins use LF-normalised bytes (`\r\n` → `\n`); bundle pins use canonical sorted `[rel, sha256(raw bytes)]` via `bundle_digest`; matrix pin uses raw file bytes
- CLOCK: `2026-09-11T00:00:00Z`
- input commit for all rows: `403e7758d9908410e636cc3b923c879cfd620340` unless noted

Proposal B/C candidates `6e28025c…` / `77f92fad…` / 17-file `c1c51f6e…` are **pre-D scratch values** and were **not** copied.

## D — predecessor pins (assignment / mention)

Verified before edit: `git log 49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9..HEAD -- tools/chandoff_assignment.py` and the mention path list exactly four yzt-88 commits. `58ba5d8` is an ancestor of HEAD. HEAD blobs equal `58ba5d8` blobs. The two pinned source files were **not** modified.

| target | algorithm | old | new | reason | verification |
| --- | --- | --- | --- | --- | --- |
| `tools/chandoff_joint.py` `compatibility_manifest` pin `tools/chandoff_assignment.py` | SHA-256 LF | `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129` | `sha256:acc67d823b7ea4f646f342723e463704196ac815d820ae518ce9c8e119cbcb1c` | post-U11 yzt-88 executing LF | `file_digest(..., normalize_lf=True)` at HEAD equals new pin; blob `46e64197ae66fdc42d65d65d4e717fbae3122c6e` = `58ba5d8` |
| `tools/chandoff_joint.py` `compatibility_manifest` pin `tools/chandoff_mention.py` | SHA-256 LF | `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1` | `sha256:6b95dc8a61fc983bc0f9bd716da4bf3dd170f05ecb0639d75c64242cf90f6f19` | same four commits | same measurement; blob `0ec3bdf97d41f9cbe1f767477619ab7b3a85b838` = `58ba5d8` |
| `tools/tests/test_joint_replay.py` `PredecessorGuardTests` assignment pin | SHA-256 LF | `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129` | `sha256:acc67d823b7ea4f646f342723e463704196ac815d820ae518ce9c8e119cbcb1c` | mirror D | same |
| `tools/tests/test_joint_replay.py` `PredecessorGuardTests` mention pin | SHA-256 LF | `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1` | `sha256:6b95dc8a61fc983bc0f9bd716da4bf3dd170f05ecb0639d75c64242cf90f6f19` | mirror D | same |

Unchanged matching pins (not retouched): finding `sha256:efa29010…`, dispatch `sha256:62dbd081…`, fallback `sha256:7884cfb6…`.

yzt-88 lineage (only commits after `49c48a9` that touch those two files):

- `261df9a2cd0c036def29572f2cbf273bdf33cb70`
- `d2b6299abd924aa55f97756488a04209a04d0158`
- `2efb52fc429fbcf05777064efc080b91625697d8`
- `58ba5d8d92fa82b07330e5aacd4e8b9fe1a9bbc8`

Post-D executing LF of `tools/chandoff_joint.py` (measured; used by C, not a D pin): `sha256:06373790cbf82bb31d94f3860fbcec2f33a58e28962fabcf4418dbf297a8f535`. Pre-D LF was `sha256:6e28025cae9cc6e33fff24bc93bfe5d591d7e283771389c267a110da8aee8d1d`.

## B — committed U11 derived bundle

Command: `python tools/chandoff_joint.py bundle --out-dir <scratch>/post-d --generated-at 2026-09-11T00:00:00Z`. Compared to committed `adapters/multica/joint-replay`: **15 SAME generated files**, **2 DIFF**, capture unchanged. Only the two DIFF files were copied into the committed directory.

| target | algorithm | old (`403e7758` / `a243c6ff` committed) | new (post-D generation) | reason | verification |
| --- | --- | --- | --- | --- | --- |
| `adapters/multica/joint-replay/artifact-set.json` | SHA-256 raw | `sha256:373ffbe86a05526160c8891b56e36dbda7ac08c5282af704de1b2628264c6462` | `sha256:6090ef9bb7b9821504b83679d53c01f1e0022ea9887fa4a624bac85b5f420377` | embeds LF store-chain `sha256:d9eb3121…` instead of CRLF raw `sha256:9c754a93…` | byte compare vs scratch; 15 other generated files identical |
| `adapters/multica/joint-replay/compatibility-pin-manifest.json` | SHA-256 raw | `sha256:2db0869edd13ec0902b2eb53e9ecc6e9ff62531691fb0056e13e781ff4e3bf18` | `sha256:b3a4b5fc4833fd0b1f77f1dbfa5ad84c5cce11e1349cf8982294311e248e5938` | D predecessor pins + LF store-chain field; `all_reproduce` now `true` | same; o2/u09 bundle pins still match |
| 17-file `bundle_digest` (exclude `capture/`) | canonical entry digest | `sha256:50e702dac5aa3f6ed3cb0d47538488939e0eec249bf31ea22809a5b174113b5e` | `sha256:8ed9608d8f8298d4508baa651aa76ce9d81921fcedc6222f8523b392c0d85187` | two generated files moved | `bundle_digest(..., exclude=("capture/",))` |
| 18-file `bundle_digest` (incl. capture) | canonical entry digest | `sha256:2047cc98737ea5fd82e7b7b3cb0199afe2480ef41c9bf0eca54a21fa6e1228c9` | `sha256:641b756fc49d1f95cf52751b939e36fbe6a40b5e1eb5c7bec5c306fb98e69798` | same + unchanged capture `sha256:b00f5664581592634a8b37899aec23b6d2e7ac41726919a320bef9b842cb24bd` | `bundle_digest` with no exclude; preflight `bundle_digest` agrees |

`final-gate-matrix.json` bytes stayed `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868` (not rewritten). Capture not regenerated.

Pre-D scratch 17-file `sha256:c1c51f6e…` and compatibility `sha256:77f92fad…` are **not** the post-D values.

## C — preflight executing pins

Measured after D and B, then written. `tools/u12_r0_binding.py` commit identity pin (`6439bd429af0aa9ec4b95d838643bd8b633ed053`) was **not** updated.

| target | algorithm | old | new | reason | verification |
| --- | --- | --- | --- | --- | --- |
| `PIN_FILES_LF["tools/chandoff_joint.py"]` | SHA-256 LF | `sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208` | `sha256:06373790cbf82bb31d94f3860fbcec2f33a58e28962fabcf4418dbf297a8f535` | post-D executing LF | `u12_preflight.digest_file(..., normalize_lf=True)` |
| `PIN_BUNDLES["adapters/multica/joint-replay"]` | 18-file `bundle_digest` | `sha256:04e0da8412ba8b024fc18fd06608b59ebe39de76692f7ed85f9c4663d28a29b9` | `sha256:641b756fc49d1f95cf52751b939e36fbe6a40b5e1eb5c7bec5c306fb98e69798` | post-B committed 18-file | `u12_preflight.bundle_digest` == joint `bundle_digest` |
| `PIN_MATRIX_FILE` | SHA-256 raw | `sha256:a9ecc24c0ee1ff8965b534ba3b317b5e1c31e541458bb9fb06d88d59eefe946d` | `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868` | YZT-109 reviewed matrix bytes; not regenerated | committed file bytes already equal new digest before C; `digest_file` raw |

Did **not** copy pre-D `6e28025c…` into `PIN_FILES_LF`.

## Frozen (not rewritten)

`adapters/multica/u11-f07/**`, `U11_JOINT_END_TO_END_REPLAY_REPORT.md` rev2, `u12-p0/**`, `u12-p0r/**`, `u12-r0bi/**`, `u12-r0b-forward/**`, `migration/gate-results/**`, `tools/u12_r0_binding.py`.
