# 旧 app1 记忆归档/停用记录（YZT-98）

- 决定者：Yann（Human），于 YZT-97 评论裁定「TeachersApp1 是新的独立项目，清理原 app1 记忆以防冲突」。
- 执行者：02 Context Engineer（Canonical Memory 正常写入 Owner）。
- 开工授权：Human 于 YZT-97 评论 `01a0a855-d937-727f-a03b-316e0ae8e10f`「批准开工例外」，对应 01 的请求「仅 YZT-98 的人工 Bootstrap 开工例外」，允许 02 在正式包/Findings 绑定缺失时先完成本 Issue 列明的归档/停用与新项目独立身份、可信来源和绑定修复。
- 任务：YZT-98（parent YZT-97）。旧 Project UUID `c671779f-059a-41b7-af11-95636de809ec`；新 Project UUID `7a2195b5-6628-4b02-9fb2-bc3ce161de85`。
- 基线：`251034892d3e6b62d17b64b412e0f4a76ad7a555`（本次变更前的 Memory HEAD）。
- 目标：不继承、不混入、可追溯、可恢复；不做全库删除，不清空真实 Findings，不改其他项目、公共团队配置、Memory Core 或冻结 Handoff 契约。

## 处置原则

1. **停用而不是删除**：旧 app1 的所有对象仍在原路径、仍可读、仍在 Gate A 审计范围内。
2. **权威入口只在一处收口**：Project Registry 的 phase 是唯一 phase 权威（F-6），把 `app1` 置为 `archived` 即让旧身份在协议层 fail closed，而不是靠文档提醒。
3. **绝不改名复用**：新项目用新 id `teachers-app1`、新 subtree、新 multica_project_id、新产品仓 id `teachers-app-one`；同名不构成继承。
4. **旧内容不搬迁、不改写**：`project-context/app1/**`、`memory/app1-*.json`、`sources/app1-*.json`、`chains/app1-mvp.json`、`migration/**` 全部保持原样，避免制造第二份「看起来更新」的旧事实。

## 旧 app1 入口清点与处置

| 层 | 入口 / 对象 | 数量 | 处置 |
|---|---|---|---|
| V1.1 权威 | `team-context/registry/projects.yaml` → `app1` | 1 | phase `incubation` → **`archived`**（保留条目、UUID、subtree、产品仓指针用于追溯） |
| V1.1 权威 | `project-context/app1/project.yaml`、`checkpoint.yaml`、`facts/`、`rules/`、`cases/` | 15 | **保留原路径、原内容**；因 scope=app1 且 app1 已归档，不再进入任何新项目 Context 包；Gate A 继续校验其 schema |
| V1.1 派生 | `index/v1.1/memory.db` | 1 | 从 canonical 重建；app1 对象仍在索引中但带 `project_id=app1`，scope 过滤下不可达 |
| V1.1 团队视图 | `team-context/checkpoint.yaml` → `projects[]` 的 `app1` 行 | 1 | 派生展示同步为 `phase: archived`（phase 权威仍在 Registry） |
| V1 legacy（只读窗口） | `registry/projects.yaml` 的 `app1` 条目 | 1 | **保留**，加注记指向本记录；V1 已非写入模型、`index/memory.db` 不再重建 |
| V1 legacy | `memory/app1-*.json` Memory Units | 7 | 保留（Gate A legacy accounting 依赖其存在：29/29） |
| V1 legacy | `sources/app1-*.json`、`sources/candidates/app1-*.json` | 11 | 保留（同上） |
| V1 legacy | `chains/app1-mvp.json` | 1 | 保留。该 chain 仍映射到 **旧** `app1` scope（`tools/cutil.py` `CHAIN_SCOPE`），不会被重定向到新项目；旧项目已 archived，handoff 层对 app1 一律拒绝 |
| V1 legacy | `index/memory.db` | 1 | 保留（不再重建，仅作回滚证据） |
| 适配器 | `adapters/multica/project-map.json` | 1 行 | **保留** 旧 UUID → `app1` 映射用于追溯；它现在解析到 archived 项目，因此旧 UUID 请求在 PLAN 阶段 fail closed |
| 迁移证据 | `migration/**`（inventory、map、authority-evidence、cutover、replay） | — | **不改**；历史证据按 `repo://teachers-app1@42f149a` 指向旧仓，仍是旧事务的准确记录 |

新增（本项目身份与新来源边界）：

| 对象 | 路径 | 说明 |
|---|---|---|
| 新项目注册 | `team-context/registry/projects.yaml` → `teachers-app1` | phase=incubation，multica `7a2195b5-…`，产品仓 `teachers-app-one` |
| 新项目锚点 | `project-context/teachers-app1/project.yaml` | 只依据新仓 `0ad4bd9` 的有效文档索引 |
| 新项目 Checkpoint | `project-context/teachers-app1/checkpoint.yaml` | 以 YZT-98 身份决定为唯一 inherited premise |
| 新项目事实 | `project-context/teachers-app1/facts/FACT-TAPP1-000001.yaml` | 仓库/修订/来源边界实态，verified against `repo://teachers-app-one@0ad4bd9` |
| 团队视图 | `team-context/checkpoint.yaml` → `teachers-app1` 行 + `cp-confirmed-005` | 记录人类决定与不继承边界 |
| 项目映射 | `adapters/multica/project-map.json` | `7a2195b5-…` → `teachers-app1` |
| 来源边界 | `adapters/multica/project-bindings/teachers-app1/` | trusted source map（已备）+ 生产 binding（待 Lead 授权 comment 激活） |
| 团队记忆注记 | `team-context/checkpoint.yaml` `cp-confirmed-002` | 生命周期枚举改为指向 Registry，并注明已被 `cp-confirmed-005` 取代 |

代码侧最小改动（非冻结契约，均不改变其他项目语义）：

- `tools/validate_canonical.py`：项目 id / phase / anchor 列表改为**数据驱动**（从 Registry 读取），并保留「项目集合漂移」硬告警（`EXPECTED_PROJECT_IDS`、`EXPECTED_ARCHIVED_PROJECT_IDS`），使归档项目继续被校验而不是被静默丢弃。原实现把 `("app1", "web-imagegen")` 与 `{incubation, active_development}` 写死在 Gate A 中，新增/归档项目会直接报错。
- `tools/cauthority.py`：`PRODUCT_REPOS` 增加 `teachers-app-one` → `D:\AI\projects\TeachersApp1`；旧的 `teachers-app1` / `app1` 解析保留，使归档 scope 的历史 authority 链仍可审计。

## 未改动（边界声明）

- 未改 `schemas/**`（含 `context-handoff/**` 冻结契约）、未改 `team-context/roles/**`（Role Profile revision 未变）、`team-context/policies/**`、`team-context/rules/**`。
- 未改 `runtime/v1.1/findings/FIND-WIMG-HO00-000001.json`（web-imagegen 真实 Finding 未被清空/关闭/重关联）。
- 未改 `migration/**` 任何既有证据、未改 web-imagegen 任何对象、未改产品仓任何文件。
- 未引入新的存储架构；新项目 Findings 根沿用既有 `flat-finding-json-v1` 布局，只是改为项目级物理根（`runtime/v1.1/projects/<project>/findings`）。

## 回滚

变更集中在单个 commit（见 Issue 交付说明的 exact revision）。

```sh
# 1) 撤销本次 Memory 变更（保留历史，不重写）
git -C D:\AI\multica-memory revert --no-edit <commit>
# 2) 重建派生索引（index/v1.1 是 gitignored 的派生状态）
python tools/context_cli.py rebuild-index
# 3) 校验
python tools/context_cli.py validate-canonical && python tools/chandoff.py scan
```

回滚后 `app1` 回到 `incubation`、`teachers-app1` 从 Registry 消失、Gate A 的 `EXPECTED_PROJECT_IDS` 也随之回退；旧 app1 的对象**从未被移动或删除**，因此不依赖任何数据恢复操作。若只需临时停用新项目而保留本项目身份，把 `teachers-app1` 的 phase 改为 `archived` 即可（同样 fail closed）。

## 验证（本机实测，命令 + 结果）

| 检查 | 命令 | 结果 |
|---|---|---|
| Gate A（schema + authority + legacy 记账） | `python tools/context_cli.py validate-canonical` | exit 0，`valid: true`，anchors 3 / facts 6 / checkpoints 4，`rule_without_valid_authority: 0`，legacy 29/29、`silent_drop: 0` |
| Gate B（硬验收） | `python tools/context_cli.py gate-b` | exit 0 |
| Gate C（历史重放 + 期望锁） | `python tools/context_cli.py migrate-replay` | exit 0，`passed: true`，`issue_noise: 0`，`provenance.ok: true`、`mismatches: []` |
| 派生索引可重建 | `python tools/context_cli.py rebuild-index` | objects 24 / checkpoints 4 / role_profiles 6，`embedding_provider: disabled` |
| 框架中立边界扫描 | `python tools/chandoff.py scan` | `clean: true` |
| 隔离探针（18 项） | `python adapters/multica/project-bindings/teachers-app1/verify_isolation.py --repo D:\AI\multica-memory` | exit 0，18 passed / 0 failed |

修订值（本次变更后）：`memory_revision sha256:45cad0a1fb4c91f86bfb402c57e423137ba6f681bd9d6b594f3d0ba68a451693`、`registry_revision sha256:ec7a825740060dbd7dc7aa857128aa2ad587a4a0c4fd4f034b1aa5d594d14401`、`role_profile_revision sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f`（**未变** → 未触碰角色配置）。变更前为 `30b51dea…` / `a08e20eb…`。

### fail-closed 反例（正式 pipeline，实测）

| 反例 | 命令要点 | 结果 |
|---|---|---|
| 旧身份（已退役 Multica UUID + project-map） | `handoff_pipeline.py prepare` + 离线 issue fixture（project_id=c671779f-…） | exit 3，`status: BLOCKED`，`escalation.reason: "archived project 'app1' excluded"` |
| 同一旧身份在 T01 PLAN | `context_cli.py prepare-handoff-plan --request-file <app1>` | exit 2，`status: BLOCKED`，`escalation.reason` 同上 |
| 错误绑定（binding.project_id 与 trusted map 不一致） | `handoff_pipeline.py selfcheck` | exit 2，`findings_source_unbound: binding project does not match the trusted source map project` |
| 缺绑定（试图绕过 Findings 门禁） | `handoff_pipeline.py selfcheck` 不传 binding | exit 2，`findings_source_unbound: selfcheck requires --findings-source-binding-file` |
| stale 包（沿用变更前 revision） | 隔离探针 `selfcheck_stale_package_*` | `REFRESH_REQUIRED`，原因 `memory_revision_changed` + `registry_revision_changed`，且无 `role_profile_revision_changed` |
| 角色/任务不匹配、显式包 ref | 隔离探针 `selfcheck_role_mismatch` / `selfcheck_task_changed` | `REFRESH_REQUIRED`，原因分别为 `role_mismatch` / `task_changed` |

### 其他项目读取未受影响（逐字节比对）

在 `2510348` 的干净 worktree 与变更后工作树上对同一输入各构建一次包并逐节比对：

- `web-imagegen` / `software-engineer`：除 `assembly_trace.excluded_counts.other_project`（14 → 16，新增项目对象被正确排除）外**全部节逐字节相同**。
- `web-imagegen` / `context-engineer`：`anchor_digest`、`rules`、`current_facts`、`cases`、`project_state_slice`、`open_conflicts`、`blocked_by`、`source_refs`、`repo_local_pointers`、`project_phase` 全部相同；仅 `assembly_trace`（同上）与 `team_state_slice`（团队级派生 posture：app1 显示 archived、新增 teachers-app1 行与 `cp-confirmed-005`）变化——这正是本次有意变更的团队级派生展示。

### 测试基线对比（`python -m unittest discover -s tools/tests`）

- 变更前（干净 worktree `2510348`）：1356 tests，75 failures + 8 errors。
- 变更后：1357 tests（+1 为新增的 `test_canonical_registry_archives_app1`），78 failures + 8 errors。
- 逐名比对：新增失败恰好 3 项，且全部属于同一类——**硬钉住变更前 canonical revision/digest 的证据类测试**：`test_handoff_artifact_readiness.PreconditionsTests.test_memory_registry_role_revisions_unchanged`（钉 `30b51dea…`/`a08e20eb…`）、`test_handoff_finding.RepoGuardTests.test_committed_evidence_bundle_matches_generator`（U09 已提交证据包内嵌旧 revision）、`test_joint_replay.ReportTests.test_final_gate_matrix_digest_recorded_in_report`（U11 报告内嵌旧矩阵 digest）。**没有任何原本失败的测试被掩盖或改写**（0 项从失败变为通过）。
- 这 3 项**有意不在此修复**：它们的正确处置是各自 lineage 的所有者重新锚定，而不是把新 revision 写进 U09/U11 的已交付证据——那会把「该证据产生于旧 revision」这一真实出处改成假的。可重放命令：`python tools/chandoff_finding.py evidence`、`python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay`。
- 因 `app1` 归档而失效的 T01/T02/T03 fixture（14 项）已按既有 `registry=` 注入缝对齐，并新增 `test_canonical_registry_archives_app1` 断言 canonical Registry 的真实归档状态。

### 明确 NOT_RUN

- 本项目正式 `PREPARE_HANDOFF` / `SELF_CHECK` **正例**（缺 Lead authority comment，见下）。
- 产品仓构建、真机/模拟器验证、CI：未执行（本任务不涉及）。
- 旧 app1 的 findings、checkpoint 语义未重新评估（不在本任务范围）。

## 残留阻断（未扩大例外）

- 本项目**正式（production）Findings source binding 尚未激活**：冻结协议要求 authority anchor 是一条**平台上的授权 comment**（`AuthenticatedCommentResolver` 会经认证 CLI 重读该 comment 并核对 digest），02 不能自授权，也不能拿一条内容不相关的现存 comment 去凑 digest（那等于伪造 authority）。trusted source map 已备好并通过校验，差 01 一条 comment：`adapters/multica/project-bindings/teachers-app1/findings-source-plan.md` 给出可直接粘贴的 comment 文本与一条激活命令。
- 因此本项目的 `PREPARE_HANDOFF` / `SELF_CHECK` 正例为 **NOT_RUN**；已执行的只有 fail-closed 反例与只读探针（见上表）。不得据此宣称 READY，也不得启动后续 specialist。
- 本机旧/新产品仓目录仅大小写不同（`D:\AI\projects\teachers-app1` 与 `D:\AI\projects\TeachersApp1`），实测为两个不同目录（File ID 不同），但 `D:\AI\projects` 报告为不区分大小写——该区分**不可假定可移植**；换机或新建目录时须按精确路径核对，避免旧仓被当作新仓。
