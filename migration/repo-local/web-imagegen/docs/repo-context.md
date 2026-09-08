# Web-ImageGen repo context (V1.1, staged — do not commit to the product repo from here)

Landing this file plus `.ai/context.yaml` (from `migration/repo-local/web-imagegen/`)
into `D:\AI\projects\opencode-web-imagegen` is a **Cutover-window Software
Engineer write** (product repo writes are SE-owned; final merge is human).
Contents reflect main@600225a reality (verified 2026-09-08), replacing the V1
branch version at a0a049b.

## Repo role

Provider-neutral Codex image generation: local CLI + globally installed
`Web ImageGen` Skill; provider values `default | grok | gpt`; grok implemented,
gpt offline-published (real browser smoke pending Phase H authorization).

## Module map (main@600225a)

- `src/` — provider-neutral core: jobs, attempts, artifact validation, atomic
  files, download snapshot, provider config/select/runtime.
- `skill/web-imagegen/` — installed Skill: agents/openai.yaml, references/
  (ai-led, user-led, runtime, providers/gpt, providers/grok), scripts/
  (cli.mjs, downloads.mjs, provider.mjs).
- `docs/adr/` — 0001 Codex Chrome browser boundary; 0002 explicit global
  provider switch; 0003 one-skill routing for browser providers (proposed,
  implemented in code).
- `tests/` — offline npm tests (`npm test`); real-browser smoke separate.

## Entry points

- `npm run skill:install` — install the global Skill.
- `npm run provider:default|provider:grok|provider:gpt` — explicit global
  provider switch (restart Codex after switching; `provider:openai` retired).
- `npm run provider:status` — current provider state.
- `npm test` — offline only; browser smoke requires explicit user approval.

## Hard constraints (canonical: RULE-WIMG-000001..000005, adr://0001..0003)

- Never clean/overwrite/commit/discard uncommitted or untracked content;
  product tasks run in an independent worktree.
- Exactly one active Generation Provider at a time; no implicit routing,
  prompt-guessing, or failure fallback; failures stay visible.
- Browser interaction only via Codex Chrome on the user-supplied, logged-in
  tab; never search/open/replace tabs; never read/write cookies, browser
  storage, passwords or profiles; no MCP/Playwright/CDP/self-managed
  Chromium/independent profile/HTTP daemon.
- Output: `<workspace>/imagine/<日期_任务>/<生成目标>/` (job.json,
  1.<orig>, 2.<orig>, chosen.jpg); redraws use `-V2` folders; debug logs keep
  sanitized status/size/MIME only.

## Context pointers

Canonical project context: `D:\AI\multica-memory\project-context\web-imagegen`
(project.yaml, checkpoint.yaml, rules/, facts/). Team context:
`D:\AI\multica-memory\team-context`. This file stays pointer-only — no
Rule/Fact bodies are copied here (Spec §3.3).
