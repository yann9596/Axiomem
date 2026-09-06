# HANDOFF: 上下文记忆系统建设 + Web-ImageGen 试点(2026-09-06)

给下一个 Context Engineer run:本文件记录被 pwsh 运行时故障打断后的现状与剩余步骤。
**完成后请删除本文件**(或并入正式 issue 评论后删除)。

## 环境修复(已做,待你验证)

- 根因: Store PowerShell 7.6.4→7.6.5 升级击穿 `C:\Users\Administrator\bin\pwsh.exe` 旧包装器,
  所有 bash 调用报 `pwsh wrapper error: real pwsh.exe not found`。详见
  `D:\AI\Agent\engineering-growth\cases\2026-09-06_Store升级7.6.5-击穿pwsh包装器-全Agent瘫.md`。
- 已在 `D:\opencode\config\opencode.json` 和 `opencode.jsonc` 设
  `"shell": "C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.5.0_x64__8wekyb3d8bbwe\pwsh.exe"`。
- 开工先验证: `python --version` 与 `multica workspace get --output json` 可执行即修复成功。
- 建议顺手治本(可选): 运行 `& "D:\opencode\scripts\fix-pwsh-wrapper.ps1"` 重编译 PATH 上的
  动态解析包装器(csc.exe 方案,源码 `D:\opencode\scripts\PwshWrapper.cs`)。

## 记忆系统现状(D:\AI\multica-memory)

- 已建: memory/ chains/ sources/ schemas/ index/memory.db tools/(ingest|retrieve|verify|rebuild-index
  + .cmd 包装),Git 初始提交 initial-team-memory-v1(main)。
- 已有 canonical: memory/governance-owner-boundaries.json;chain: team-governance。
- 本 run 新增(文件直写,绕过 CLI,但字段与 tools/memory_cli.py 校验一致):
  - sources/web-imagegen-project-context-2026-09-06.json(external_signal, authoritative)
  - memory/web-imagegen-project-context.json(memory_unit, canonical, importance 5)
  - chains/web-imagegen-pilot.json(memory_chain)
- 方案对照材料(本地已存,不必再依赖附件):
  - `C:\Users\Administrator\multica_workspaces_desktop-api.multica.ai\ygy-93b2a5fe8b6b\task-d67fa2df4d7e\workdir\issue-05-memory-rag.md`
  - 同目录 `multica_agent_team_v1.md` 第 15-18 章
  - 附件 `multica_context_memory_system_v1.md`(id 01a075d9-1e38-7e99-8427-7d187f3dd02f)仍需下载比对。

## 剩余步骤(按序)

1. 验证 shell 恢复(见上)。
2. `multica attachment download 01a075d9-1e38-7e99-8427-7d187f3dd02f --output ./multica_context_memory_system_v1.md`,
   与 issue-05 + 现有仓库逐项 diff,补缺口(注意: 方案中 memory/ 子目录分类被 V1 简化为 scope 字段,
   这是已记录的有意取舍)。
3. `python tools/memory_cli.py verify && python tools/memory_cli.py rebuild-index`,再跑
   `retrieve "owner boundary" --limit 5` 冒烟测试。
4. 把新增的 3 条记录用 `ingest --dry-run` 校验通过后,git 提交(仅 memory 仓,不碰产品仓)。
5. **先查重再通知**: `multica issue list --output json` + 评论检索,确认 2026-09-06 13:2x 的
   前序 run 是否已发过团队通知(当时疑似已在创建 issue/提及成员,被 cmd 编码问题打断)。
   未发则按角色发通知: Engineering Lead、Solution Architect、Software Engineer、Feature Reviewer、QA,
   内容=记忆系统入口 D:\AI\multica-memory、ingest/retrieve 用法、Embedding 未启用、试点链 web-imagegen-pilot。
6. 试点已预置: Web-ImageGen 项目 canonical 单元 + pilot 链。若 diff 后方案要求更多试点动作
   (如首个 Context Package 实例),按方案补做并走 ingest。
7. 向用户回包: 建成情况、试点状态、通知状态、索引与冒烟测试结果、置信度与缺失清单。
