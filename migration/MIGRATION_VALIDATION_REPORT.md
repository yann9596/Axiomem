# MIGRATION_VALIDATION_REPORT — YZT-43（V1.1 Migration Audit 重建与冻结 Replay Evidence）

状态：**READY_FOR_QA_RECHECK**（候选 commit 等待 YZT-44 独立 QA 复验；R10 未执行）
分支：`yzt-43-evidence`（基于 YZT-42 候选 `c7771b0`）；`main` 与 V1 runtime 未切换。
Owner：02 Context Engineer（本 Issue 只交付证据与 Canonical 判断，不改可执行实现）。

---

## 0. Commit 链（hash 顺序可验证）

| 顺序 | Commit | 内容 |
|---|---|---|
| 0 | `c7771b0` | YZT-42 基线（fail-closed 计算型 Gate；A/C 红等待证据） |
| 1 | `71b7e967448beb369c5b976140feda1d5794624e` | **expectation/evidence freeze**：legacy inventory、14-Rule authority audit、per-object signal mapping、重建的 replay expectations 与 metric-evidence 输入 |
| 2 | `0f86a2a` | **expectation lock**：12 个 expectation 侧文件 sha256 冻结，`committed_at_revision=71b7e96…` |
| 3 | `c08f79c` | lock 可复现性修复（`.gitattributes` 对 hash 相关路径 `-text`；inventory evidence 改为 blob-basis sha256）——Gate C clean-checkout 验锁曾因 core.autocrlf 行尾转换失败，此 commit 在不改 runtime 的前提下修复 |
| 4 | （本 commit） | **result commit**：clean checkout 实测 Gate A/B/C 机器件 + 本报告 |

Gate C 在运行时重算 12 个锁内文件 hash 并校验 `committed_at_revision`（tools/cmetrics.py `verify_expectation_lock`）；git 历史顺序证明 freeze < lock < results。

## 1. 29-object 对账（Gate A，由数据计算）

- 机器可读 inventory：`migration/legacy-inventory.yaml`（29 对象 = 10 units + 3 chains + 11 signals + 5 candidates；schema 校验通过）。
- 逐对象 source/sha256/disposition/target 证据：`migration/legacy-inventory-evidence.yaml`（sha256 = git blob content，checkout 无关，附复现命令）。
- 对账结果（`tools/caccount.py`，set reconcile）：inventory=29，accounted=**29/29=100%**，silent_drop=**0**，duplicates=0；map_only（known entry，非 silent）=`registry/projects.yaml`（V1 Registry，非 29 对象集成员，map registry 节有独立处置）。
- YZT-42 交接时的缺口（11 个 signals 仅有 group-level 处置）已按 YZT-40 权威报告补齐为逐对象 `legacy:` 条目（`migration-map.yaml` signals 节）。
- 4 个 snapshot 后候选（t23/t24/t25、web-imagegen-repo-local）原为 main 工作树 untracked；已**字节级原样复制**进本证据分支（原件未动；F-5 bundle 亦有副本），使 29-object 集合对账可从 clean checkout 复现。处置不变：finding_ingest → FIND-*-MIG-{2,3,4}/WIMG-1（forget/pointer），**R10 delta 迁移仍不执行**。

Gate A 结果：`migration/gate-results/gate-a.json`（valid=true，exit 0）。

## 2. 14-Rule Authority Audit（语义证据，非 URI 白名单）

机器可读：`migration/authority-evidence.yaml` —— 每条 (rule, authority_ref) 一条 claim：精确 locator/摘录、claim_relation（supports/qualifies）、authority_scope vs rule_scope 的 coverage 结论。共 31 claims，14/14 rules valid（Gate A `rule_without_valid_authority=0`）。

| Rule | Authority artifact（精确出处） | 关系/覆盖 |
|---|---|---|
| RULE-TEAM-000001 | YZT-39 描述 "Owner 模型：…"（用户撰写，逐字）+ `sources/project-context-2026-09-06.json`（owner 治理记录，reliability=authoritative） | supports；team ⊇ team |
| RULE-APP1-000001..000007 | 方案 v1.0@42f149a 逐节（P16/§4.6、P6/§5、§4.1-4.3、P11/P12/§4.5/§7、§4.4/§5.4、§9 十项、§4.6）+ YZT-20 Confirmed baseline/Risks | supports（doc）；supports/qualifies（YZT-20） |
| RULE-APP1-000008 | YZT-26 Complexity guard/Out of scope + YZT-36 "执行模式固定为 synthetic_only_fail_closed…不得成为产品默认 Policy" | supports |
| RULE-WIMG-000001/000002 | `project-context/web-imagegen/project.yaml` hard_constraints（逐字 7 条）+ YZT-40（qualifies：采纳执行记录） | supports；project ⊇ project |
| RULE-WIMG-000003 | anchor hard_constraints（no Neo4j/vectorization/auto-merge）+ YZT-40 R7（"Index 不得继续 Git 跟踪"、零重建）+ **YZT-22（"…禁止宣称启用"，本审计新增）** | supports |
| RULE-WIMG-000004 | adr://0002（accepted，逐字）+ adr://0003（proposed；README 实证）+ README@600225a L19/20/67/112 | supports |
| RULE-WIMG-000005 | adr://0001（accepted，逐字）+ adr://0003 + README@600225a L5/22/23/24/104 | supports |

**Authority corrections（Canonical 判断，记录差异，不制造 PASS）：**
1. RULE-TEAM-000001：原 `multica://project-context/multica-agent-team-v1` 不可解析（team 规则错用 project-context URI 族；"multica-agent-team-v1" 非注册项目）。claim 本身有真实 owner 授权 → authority_refs 改指 YZT-39（逐字 owner 模型）+ V1 治理 signal；claim 文本未改。属机器可决的指针纠正，非 Authority 缺口。
2. RULE-WIMG-000003：embedding-interface-only 子句在原 refs 中无 authority → 新增 `multica://issue/YZT-22`（逐字支持）。
3. 无 demote / 无 Human Decision 事项：所有 claim 均找到真实支持证据；未发现"旧措辞升 Rule"的伪 authority。

## 3. Replay expectations：先冻结、后运行

- 6 个 expectation manifest 从权威 Issue 重建（`migration/replay/replay-yzt-{9,10,22,26,36,39}.yaml`，文件头注明逐条来源），freeze 于 commit 1，lock 于 commit 2。
- metric-evidence 输入（`migration/replay-evidence/*.yaml`）：false_canonical_refs（越界 canonical 形式）/ must_retain_refs（forget 纪律）/ allowed_issue_refs（合法 blocker），与 expectation 一同冻结。
- **replay-yzt-22 修正一处 expectation**：expected_conflicts 0→1。依据：YZT-42 修复后 CE policy baseline 含 `team.checkpoint`，包内会呈现 team checkpoint 的唯一 conflicts 条目（cp-team-conflict-001，已裁决、非阻塞、schemas/README.md 记录在案）。呈现≠隐藏：hidden_unresolved_conflict=false，YZT-22 的停止条件（产品基线实质冲突）仍为 0。这是由（权威 Issue + 真实 role 语义 + canonical 现状）推导的预期，不是跑完改预期。
- 其余 5 个 expectation 与 YZT-40 版本语义一致，逐条经权威 Issue 复核。

## 4. Gate A / B / C（clean checkout 实测）

命令（clone `yzt-43-evidence` 后，仓库根目录）：

```
python -m unittest discover -s tools/tests -v     # 35 项，33 过 / 2 失败（见 §6）
python tools/validate_canonical.py                # Gate A0+A → gate-a.json, exit 0
python -c "import sys; sys.path.insert(0,'tools'); from cgates import gate_b; gate_b()"   # Gate B → gate-b.json, exit 0
python tools/creplay.py                           # Gate C → gate-c-replay.json, exit 0
```

实测（clean clone of `c08f79c`）：

| Gate | 结果 | 关键计算值 |
|---|---|---|
| A0+A | **PASS**（exit 0） | schema 全过；accounted=29/29=100%；silent_drop=0；unresolved_scope=0；rule_without_valid_authority=0（31 claims 全 computed，无 missing evidence）；map_only=1（registry，known） |
| B | **PASS 10/10**（exit 0） | 零重建 22 objects；index 非Canonical/不 git 跟踪；D-02 scope 先于检索；双向零泄漏（YZT-22 blocker probe）；隐式 cross_project 拒绝；role profile 内容级生效（内容指纹，非回显）；slice 干净；普通任务 0 Case 且不搜索 |
| C | **PASS**（exit 0） | provenance ok（lock=71b7e96，12/12 hash 匹配）；6 replays 全部 `metric_status=computed`（无 0 默认）：false_canonical=0、false_forget=0、issue_noise=0、scope_pollution=0、false_activation=0、missing_context=0、unexpected=0；case_search/conflicts 逐 replay 符合预期 |

原始输出：`gate-a-clean.txt` / `gate-b-clean.txt` / `gate-c-clean.txt`（随评论附上）；机器件：`migration/gate-results/gate-{a,b,c-replay}.json`（本 commit）。

## 5. 结论

**READY_FOR_QA_RECHECK** —— 候选 tip = 本 result commit（父序：c7771b0 → 71b7e96 → 0f86a2a → c08f79c → results）。所有硬指标由 inventory / mapping / authority / expectation / actual / evidence 计算得出；expectation 先于结果冻结且 hash 可验证；fail-closed 行为经负向实测（freeze 前 Gate C FAIL：missing lock；锁被篡改/文件缺失即 mismatch）。

## 6. Known Limitations / Findings（不隐藏）

1. **SE 测试套件 2 项失败（预期状态迁移，非缺陷）**：`test_real_map_does_not_silently_claim_100` 与 `test_real_repo_metrics_are_missing_evidence_not_zero` 断言的是 YZT-42 交接时"证据缺失"的仓库状态（silent_drop>0 / metrics=missing）。本 Issue 恰恰要求消除该状态，两测随之翻转。fail-closed 逻辑本身仍由其余 fixture 负向测试覆盖（35 项中 33 过）。按 Owner 边界未改 YZT-42 测试；建议 Lead 路由 SE 将这两个 real-repo-state 探针改为 fixture 化（或在 QA 前明确豁免）。
2. **hash 锁的平台敏感性（已修复，留档）**：`core.autocrlf` 会改变 fresh checkout 的磁盘字节，曾致 Gate C 验锁失败。已在证据侧修复（`.gitattributes` -text + blob-basis sha256），不改 runtime；QA 在极端配置（如 `core.eol=crlf` + 无 attributes 支持）下仍应知悉此机制。
3. YZT-40 报告中的 Gate 结果（`5115233`）为历史证据，已被本轮计算型结果取代，不得再作 Cutover 依据。
4. Instructions/Skills 平台侧更新、repo-local 落仓、delta（R10）等 Cutover 窗口事项维持原状（不在本 Issue 范围）。
5. ADR-0003 状态为 proposed（实现现实由 README@600225a 实证）；evidence 中已如实标注。
6. context_density 均值 0.54 / context_size 均值 9.5：样本为 6 个历史回放，指标校准仍待首批真实运行（同 YZT-40 报告 §7.7）。
