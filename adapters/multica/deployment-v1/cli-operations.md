# Exact supported CLI disable / remove / rollback (deployed `multica`)

Inventory taken from live `--help` during correction-1. D1 does **not** execute these. Unknown or missing actions are reported; they are **not** implemented through private APIs.

## Supported (use these)

| Intent | Command | Notes |
| --- | --- | --- |
| Create new 05 | `multica agent create --name "05 Delivery Reviewer" --runtime-id <id> --instructions <text>` | Lead/Human only. New UUID. |
| Apply instructions | `multica agent update <id> --instructions <text>` or pipe via a file the CLI accepts | Save before-text first for rollback. |
| Import skill from archive | `multica skill import --file <path.zip\|path.skill> --on-conflict fail` | Local path must be `.zip` / `.skill`, not a raw directory. |
| Create skill from SKILL.md body | `multica skill create --name <name> --content-file <SKILL.md>` | Alternative to import. |
| Update skill body | `multica skill update <id> --content-file <SKILL.md>` | Rollback = update back to previous body. |
| Delete workspace skill | `multica skill delete <id> --yes` | Drops the skill object. Confirm no remaining required bindings first. |
| Bind skill (additive) | `multica agent skills add <agent-id> --skill-ids <id>[,<id>]` | Does not replace existing assignments. |
| List bindings | `multica agent skills list <agent-id> --output json` | Read-back after add. |
| Archive agent | `multica agent archive <id>` | Rollback for a newly created 05. |
| Restore archived agent | `multica agent restore <id>` | Inverse of archive. |
| Squad instructions | `multica squad update <squad-id> --instructions <text>` | Save before-text. |
| Add squad member | `multica squad member add <squad-id> --member-id <agent-id> --type agent --role member` | D3: new 05. |
| Remove squad member | `multica squad member remove <squad-id> --member-id <agent-id> --type agent` | D3: old 05 leave normal routing. |

## Unavailable / forbidden (report, do not private-API)

| Intent | CLI fact | What D3 must do |
| --- | --- | --- |
| Unbind one agent skill | **`multica agent skills remove` does not exist** (`agent skills` has only `add` / `list` / `set`) | Leave `milestone-quality-gate` bound until a supported unbind exists, **or** Human/Lead explicitly accepts a later mechanism. Do **not** call `agent skills set` to drop one id: `set` **replaces all** current assignments. |
| Replace-all bind | `multica agent skills set <id> --skill-ids ...` exists | **Forbidden** this batch (`never_replace_all_set`). |
| `multica skills remove` | unknown command; the noun is `skill` | Use `skill delete` only when deleting the workspace skill itself is intended. |
| SAFE_DISPATCH / auto assignment as default start | not a CLI; U06–U08 code retained disabled | Do not invoke from Instructions. |
| T06 full discover / production ledger | out of this batch | Do not run. |
| Source-data Finding drain | not a disable CLI | Do not empty `D:\AI\multica-memory\runtime\v1.1\findings`. |

## Rollback map (supported inverses)

| Forward | Rollback |
| --- | --- |
| `agent create` (new 05) | `agent archive <NEW_05>` (keep old 05 untouched) |
| `agent update --instructions` | `agent update --instructions` with saved `live-before` text |
| `skill create` / `skill import` | `skill delete <new-id> --yes` if unused |
| `skill update --content-file` | `skill update --content-file` with previous body |
| `agent skills add` | **no additive unbind**. Report gap. Do not `set`. |
| `squad update --instructions` | `squad update --instructions` with saved before-text |
| `squad member add` new 05 | `squad member remove ... --member-id <NEW_05>` |
| `squad member remove` old 05 | `squad member add ... --member-id b6335f8e-...` (history restore) |

## Pre-D2 vs D3

Execute only the `ops_pre_d2` list in `binding-plan.json` before D2. `ops_d3` stay blocked. 04 does not run either list in this attempt.
