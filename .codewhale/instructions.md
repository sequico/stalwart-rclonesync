# Codewhale project instructions

> This file is a **self-contained mirror** of the owner-global operating rules
> (global user rule, active here). It exists so that sub-agents and sessions
> started in this repository inherit them even when the global memory is not
> loaded. It is **not** a second source of truth: change
> `~/.codewhale/memory/global/MEMORY.md` first, then mirror the change here.
> This repository has no other law file (`AGENTS.md` / authoring contract) —
> when one is added, it becomes the project law and this file mirrors only the
> global rules below.

## Budgets live in the machine, once

The hard bounds against stale turns, hung streams and runaway agents are set in
`~/.codewhale/config.toml` (`[tui]`, `[subagents]`) and apply to **every**
workspace. Never restate or shadow those numbers in a project config.

- Read the live values with `codewhale config list` (`subagents`, `tui`).
- When a cap fires, report **which** cap fired and what work already landed;
  never silently retry past it.

## Progress, not elapsed time

- Never wait passively: no blind "wait until all". Poll the worker's state.
- A worker that is alive and advancing (steps up, tool calls, files written) may
  run as long as it needs — elapsed time is not the kill criterion.
- Two consecutive polls with no progress = stale: `interrupt` it, then resume
  from its checkpoint (or `cancel` if the base has moved).
- Steps advancing while tokens explode with no artifact = send **converge-now**
  (finish with the evidence in hand) before killing anything.

## Shell commands must fail fast, never hang

- Always pass an explicit timeout; anything expected to outlast ~5–10 s is
  backgrounded and polled, not awaited in the foreground.
- The fail-fast non-interactive defaults (`GIT_TERMINAL_PROMPT=0`, `PAGER=cat`,
  `GIT_EDITOR=true`, `npm_config_yes=true`, …) are installed machine-wide in
  `~/.codewhale/shrc`. Interactive terminals are untouched.
- Never `find`/`grep -r` from a workspace root that is also a home directory, and
  never traverse a FUSE-synced mount (pCloud, Insync, gvfs): a `stat` inside a
  hung mount blocks uninterruptibly, where no timeout and no signal can help.
  Prefer `git grep` / `git ls-files` inside the repository.
- Working in a repository directory (not in a home directory) is what keeps
  those mounts outside the workspace.

## Bounded work per worker

- Hand each worker per-file diff slices or a small explicit file list — never a
  directory-level diff. A truncated output is narrowed with a read or a single
  grep, never re-run as the same wide command.
- Read-only roles (`reviewer`, `explore`) reject compound shell forms (`cd X &&`,
  pipes, `;`, `&&`, redirects). Open every such prompt with the allowed forms
  only and state that a rejection is a policy boundary, not a syntax error:
  do not retry variants.

## Commits and pushes

- Never commit or push unless the owner asks in that same turn, with the words
  "commit" or "push". An earlier order never carries over.

## Language

- Reply in the language the owner writes in. Agent-authored file content (code,
  comments, docs, config) is written in English unless this repository states a
  different rule.

## Worktrees

- A worktree of this repository lives in `.worktree/` — the canonical name, here
  and in every repository: `<repo>/.worktree/<slug>`. Not `.worktrees/`, not
  `<name>.worktree/`, no per-tool spelling.
- Create one with `git worktree add .worktree/<slug>`.
- A worktree is a checkout, not content. `/.worktree/` is ignored — in a
  repository whose `.gitignore` belongs to upstream, the pattern lives in
  `.git/info/exclude` instead — so `git add -A` can never stage one.
