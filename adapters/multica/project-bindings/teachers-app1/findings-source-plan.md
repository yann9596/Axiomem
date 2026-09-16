# TeachersApp1 — Findings source boundary（YZT-98）

本目录定义 `teachers-app1` 项目的 **Findings 来源边界**：项目的真实 Finding 只允许
落在本项目自己的物理根，绝不与其他项目共用。

## 边界

| 项 | 值 |
|---|---|
| project_id | `teachers-app1` |
| source_id | `teachers-app1-runtime-findings` |
| layout | `flat-finding-json-v1` |
| root | `D:\AI\multica-memory\runtime\v1.1\projects\teachers-app1\findings` |
| 当前记录数 | 0（空 store，不是「缺失」：目录已存在且可枚举） |
| 已授权 task_ref | `multica://issue/YZT-97`、`multica://issue/YZT-98` |
| 已授权 role | 六个逻辑角色（engineering-lead / context-engineer / solution-architect / software-engineer / delivery-reviewer / qa） |

为什么另起项目级根，而不是继续用 `runtime/v1.1/findings`：后者当前存放
web-imagegen 的真实 Finding `FIND-WIMG-HO00-000001.json`。把两个项目的 Finding
放进同一个物理根，任何一次绑定或枚举失误都会把另一个项目的 Finding 读进本项目
的 Finding Gate。项目级物理根让「混入」在文件系统层面就不可能发生，而不是靠
scope 过滤事后补救。布局本身仍是既有的 `flat-finding-json-v1`，没有引入新的存储架构。

## 已备好的产物

- `findings-trusted-source-map.json` — `findings-trusted-source-map/1`，钉住上面的
  物理根与授权范围。已通过 `tools/chandoff_findings_source.py:load_trusted_source_map` 校验。
- `build_binding.py` — 操作辅助脚本：读一条**平台上的**授权 comment，算出它的
  sha256，写出并校验 `findings-source-binding/1`。只读 CLI，不发布、不 mention。

## 尚缺的唯一一项：authority comment

冻结协议（`tools/chandoff_findings_source.py`）要求 binding 的 authority 必须是
**平台上真实存在、可被认证 CLI 重读**的一条 comment：每次 boundary 都会重读它并
核对 `sha256(content)`。因此这条 comment 必须由 Lead（01）发出，02 不能自授权，
也不能拿一条内容不相关但存在的 comment 去凑 digest —— 那属于伪造 authority。

**请 01 在 YZT-97（或 YZT-98）发布下面这条 comment（内容即权威记录，请勿改写）：**

```text
FINDINGS_SOURCE_BINDING teachers-app1
source_id: teachers-app1-runtime-findings
project_id: teachers-app1
root: D:/AI/multica-memory/runtime/v1.1/projects/teachers-app1/findings
layout: flat-finding-json-v1
task_refs: multica://issue/YZT-97, multica://issue/YZT-98
roles: engineering-lead, context-engineer, solution-architect, software-engineer, delivery-reviewer, qa
authorized_by: 01 Engineering Lead (YZT-98 bootstrap repair, Human-approved exception)
note: 项目级物理根；不与 web-imagegen 共用；不得据此清空或转移既有 Finding
```

## 激活（一条命令 + 一次校验）

```powershell
# 1) 用 Lead 的 comment 生成并校验 production binding
python D:\AI\multica-memory\adapters\multica\project-bindings\teachers-app1\build_binding.py `
  --repo D:\AI\multica-memory `
  --issue <发布该 comment 的 issue-id> `
  --comment-id <该 comment 的 comment-id> `
  --trusted-map D:\AI\multica-memory\adapters\multica\project-bindings\teachers-app1\findings-trusted-source-map.json `
  --out D:\AI\multica-memory\adapters\multica\project-bindings\teachers-app1\findings-source-binding.json
```

`build_binding.py` 会打印 `authority_digest` / `runtime_commit` /
`runtime_adapter_digest`，这些是后续 PREPARE / FINALIZE / SELF_CHECK / PUBLISH
每个 boundary 都要重验的 pin。激活后即可按共享技能跑正式门禁：

```powershell
python D:\AI\multica-memory\skills\multica-context-handoff\scripts\handoff_pipeline.py prepare `
  --repo D:\AI\multica-memory --out-dir <scratch> `
  --issue <issue-id> --target-role <role> --caller-role <role> --purpose <purpose> `
  --findings-source-binding-file ...\findings-source-binding.json `
  --findings-trusted-map-file ...\findings-trusted-source-map.json
```

## 边界声明

- 未创建、未清空、未迁移任何 Finding；web-imagegen 的 `FIND-WIMG-HO00-000001`
  保持可见且未被重关联。
- 未修改冻结契约（`schemas/context-handoff/**`）、未修改冻结 Handoff、未修改
  Memory Core。
- 在本 binding 激活之前，本项目的 `PREPARE_HANDOFF` / `SELF_CHECK` **正例**
  一律是 NOT_RUN；此时任何「READY」声明都是伪造。
