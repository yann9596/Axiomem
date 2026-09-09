## Context Handoff 派发门

向下游 agent 或 squad 成员派发任何工作之前，必须逐条满足：

1. 目标 Issue 已存在：不为没有具体 Issue 的工作派发或触发。
2. 已确定 target role（目标角色）。
3. 已通过共享 Skill `multica-context-handoff` 执行 PREPARE_HANDOFF，并按其确定性返回状态行动。
4. Handoff 状态为 BLOCKED 时，禁止触发下游工作：停止派发，记录升级需求，并按现有 Issue/parent 协议升级。
5. Handoff 状态为 READY（或策略明确允许 PARTIAL）时才可派发，且对目标 agent 使用恰好一次 Multica 触发。
6. assignment 与 agent mention 永远不得组合为重复触发；每次 Handoff 只允许一个触发（二选一）。

创建子 Issue 的安全顺序：create 时不启动目标 agent → prepare/publish/confirm handoff → 恰好一次触发。不要在创建 Issue 时传入 assignee 而顺带触发目标 Run。

本门只新增派发前置检查，不改变任何 Owner 边界：Scope/Priority/任务拆分/路由仍归 01；架构仍归 03；实现仍归 04；功能正确性仍归 05；QA 结论仍归 06；Canonical 写入仍归 02；最终 Merge 仍归 Human。
