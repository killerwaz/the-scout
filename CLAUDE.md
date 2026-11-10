# The Scout: project rules

## PLAN.md is the only plan

`PLAN.md` is the single source of truth for what we're building, the decisions, the phases and progress. These rules override the Superpowers plugin's defaults:

- **Never create `docs/superpowers/plans/` or `docs/superpowers/specs/`**, or any other plan, spec or design file.
- **brainstorming**: when the design is settled, write it into the relevant section of `PLAN.md` (§1 What we're building, §2 Decisions, §4 Architecture, or the phase in §6) instead of writing a separate design doc. Show me the diff before saving.
- **writing-plans**: put the task breakdown under the current phase in `PLAN.md` §6, as a checklist. Keep it short: files, steps, and how to verify each one.
- **executing-plans / subagent-driven-development**: work from the phase checklist in `PLAN.md`. Tick items off and add a line to §8 Progress log as you go.
- **Locked decisions in §2 stay locked.** If brainstorming points at changing one, stop and ask me; don't edit it quietly.
- New open questions go in §7.

## Git

This folder is a git repository (since 2026-11-10), pushed to GitHub as the public repo `the-scout`. Commit only when I ask. Never push or add a remote unless I ask. Skip the Superpowers steps that create worktrees or finish branches, unless I ask.
