# 给 01：应用 PR #2 与仓外验收任务

用户已暂停当前项目。保持暂停；本文件不是恢复指令，也不是已经创建的 Multica Issue。仓库内实现见同目录 implementation.md、canonical-migration.json、offline-quality-calibration.json、validation.json。按远端 PR #2 的实际 head SHA 读取，不从本文猜测版本。不得把 PR 已存在/已合并/源码预算通过当作线上完成。

## 先决条件

核对最新 main、PR head、现有未提交内容、Windows 部署版本、共享 Skill 实际绑定、当前 parent/child 与角色。既有工作树不重置。对 PR 精确提交安排独立 05 审查：代码安全、迁移逐项映射、未批准/NOT_RUN 边界、质量门接线、冻结合同不变、历史保留与新 source pin。你负责接受和受权集成，不虚构审查或替代专业责任。本次修复无权合并 TeachersApp1 产品 main、运行设备、清理数据、签名或发布。

实际需要创建的任务由你在 Multica 建立一次并填回真实编号；下列 E1–E4 只是拆分模板。去重后复用现有任务。遵守一条最终评论、一次触发、现行专业槽位及有界纠正，不以本交接自动唤醒任何人。

| 模板 | Owner / 依赖 | 必须完成的仓外工作 | 验收和停止条件 |
|---|---|---|---|
| E1 精确版本部署与治理接受 | 01 统筹；04 部署；02 核验 Canonical；05 独立审查。依赖 PR 精确版本通过审查/集成 | 在安全且稳定的 Windows 工作树应用；更新共享 Skill 安装与实际绑定，核对源码新的 AUDIT_REPAIR_SKILL_BUNDLE；重建 **V1.1** 索引并只读 check；抽查 status/path/statement/relations。不得误用 memory_cli V1。 | 远端/本地/source Skill/installed Skill/Canonical/index 各自 exact revision 回执；锁/sidecar/并发变化停止；旧部署摘要失败必须修复绑定，不改旧收据让其通过。 |
| E2 权威来源与真实工作负载验收 | 02 源核验，05/06 独立质量验收，01 接受。依赖 E1 | 完整读取真实 parent/child、源授权、Artifact exact version、真实 Findings binding/trusted map/prior observation；核对继承的13个隔离环境 authority errors，其中7个旧 app1本地来源、6个 web-imagegen外部来源，不伪造批准。对真实同任务/角色做 PLAN→FINALIZE→post-artifact→publish/readback→consumer fresh SELF_CHECK；测量包含实际依赖的完整字节。 | 源存在/权威/Scope/schema/NOT_RUN 均保留；明确哪些旧归档项目问题与当前消费者有关。必要长信息超限交02整理或01缩小任务；阈值调整需版本化证据，不关闭硬门绕过。不得清空真 Findings。所有有效消费者必须新鲜且部署一致。 |
| E3 平台发现契约与影子失效验收 | 04 平台契约实测，05 Review，01决策。可与E1独立准备，不阻塞已安全部署的修复 | 只读验证评论分页/完整性/精确ID读取、编辑删除是否有可信水位、乱序与同时间戳、父容器与子task/role/工作区隔离；采集影子manifest对照，覆盖新增适用Rule/Conflict/撤销与无关项目变化。 | 无可信完整性证明继续完整live扫描；本次解析缓存节省网络读取数=0。影子 unchanged 永不覆盖现有全局 stale。只有独立验收证明不会漏约束，才另行启用更窄权威失效或网络增量优化；不将此可选优化无限阻塞E1/E2。 |
| E4 受控恢复评审与结案 | 01；依赖 E1/E2，E3的保守回退已明确 | 汇总 exact commit、独立审查、实际部署/索引/Skill绑定、真实包统计、fresh SELF_CHECK 和所有剩余受影响问题。沿既有 Memory Disposition 回填已治理/无需治理理由/阻断Owner与退出条件。 | 用户主动暂停仍是独立约束；不得由PR、源码测试或本提示词推断暂停已解除。只有暂停解除且受影响运行门满足，才走正常一次派发。旧设备/产品/签名门照常独立存在。 |

## 具体执行要求

先按现有共享合同完成真实身份/来源绑定的协作 SELF_CHECK；只读诊断允许，禁止伪造 READY。若旧 Context 因本次源码/Canonical变化而 stale，在未变授权内有界刷新。原公开仓库没有私有原始审计、实时评论和本机收据；这些从已授权平台读取，不能在公开PR里粘贴全文。

测试边界须明确：新增回归通过 ≠ 全仓通过；差异门无新增失败ID ≠ 原有失败消失；离线合成包下降 ≠ 线上 token/费用下降；实现源码合并 ≠ 01已接受或共享Skill已部署。无须再让用户选择既有授权内的常规技术参数，只有新的产品Norm、权限、数据破坏或范围扩展才提出完整决策表。

## 一条最终回执

返回真实 E1–E4 task IDs/状态；PR/commit与远端回读；Review artifact exact version；Windows和共享Skill部署摘要；Canonical/index检查；真实12类或等效代表工作负载的字节和安全差异；fresh消费者SELF_CHECK；未完项Owner/期限/停止条件；用户暂停是否明确解除。未取得的证据写 NOT_RUN/BLOCKED，不以排队任务作为完成。
