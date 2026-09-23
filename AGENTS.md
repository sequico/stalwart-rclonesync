# Project instructions

> Self-contained mirror of the owner-global agent rules (global user rule,
> active here), so sessions and sub-agents started in this repository inherit
> them even when the global `AGENTS.md` is not loaded. This is not a second
> source of truth: the global file is canonical, change it first and mirror the
> change here. This repository has no other law file.

## Language

- Reply in the language the owner writes in; agent-authored file content (code,
  comments, docs, config) is written in English unless this repository states a
  different rule.

## Provider privacy (priority directive)

- Every request to the LLM provider must be zero-retention, with training and
  service-improvement use disabled, and opt-out headers (e.g.
  `X-Data-Opt-Out: true`) sent on every request.

## Commits and pushes

- Never commit or push unless the owner asks in that same turn with the words
  "commit" or "push". An earlier order never carries over.

## Single source of truth

- Every concept, constant, schema and helper has exactly one canonical
  definition; everything else imports or derives from it. Search for the
  existing definition before writing a new one.

## Skills

- The skill that governs a piece of work is loaded before the first edit of it.

## Verification

- Before claiming a task done: re-read the modified files, then run the real
  check (tests, lint, type-check, output), not a stand-in.
- A gate that reports anything standing has not passed. "Pre-existing" and
  "not mine" are not categories.

## Working-directory hazard

- On a machine whose home directory contains FUSE network mounts (pCloud,
  Insync, gvfs), a `stat()` inside a hung mount blocks uninterruptibly. Work in
  the repository directory; prefer `git ls-files` / `git grep` over filesystem
  walks, and never traverse a mount.

## Worktrees

- A worktree of this repository lives in `<repo>/.worktree/<slug>` — the
  canonical name, here and in every repository: not `.worktrees/`, not
  `<name>.worktree/`.
- Create one with `git worktree add .worktree/<slug>`.
- `/.worktree/` is ignored (in a repository whose `.gitignore` belongs to
  upstream, the pattern lives in `.git/info/exclude` instead), so `git add -A`
  can never stage one.
