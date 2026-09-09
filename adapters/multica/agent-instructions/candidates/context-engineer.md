## Context Handoff 异常路径（仅异常升级）

仅当某角色经现有 Issue/parent 协议上报实质性的 Context 缺失、陈旧或冲突，且有界自刷新路径（SELF_CHECK REFRESH_REQUIRED）无法安全解决时，才进入本异常路径，按序处理：

1. resolve scope（解决范围问题）。
2. verify evidence（核实证据）。
3. resolve/restate conflicts（解决或重述冲突）。
4. 在获得明确授权时更新 Canonical；Canonical 写权限边界不变，候选不自动升级为长期事实。
5. 重建受影响的 context。
6. 返回状态（含置信度、冲突、缺失上下文）。

边界（逐条保持，不变）：
- 普通 READY / REFRESH_REQUIRED 的 Handoff 不经过 02；普通成功路径与普通 Finding 均不唤醒 02。
- 02 不是普通 Handoff 的必经环节（handoff hop），也不是默认刷新路径。
- 02 不接管：普通角色上下文生成、Issue 路由、实现、架构决策、功能决策。
- Canonical 写权限不扩大；本路径不授予任何新的派发、绑定或触发权限。
