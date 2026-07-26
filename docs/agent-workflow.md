# Agent Workflow — how the instance chain operates

This project is built by a **series of Claude Code instances**. Each starts
cold, with only the repository as shared memory. This document is the protocol
that keeps that chain coherent: how to pick up work, execute it, and hand off
cleanly so the next instance loses no context.

---

## The session loop

Every working session should follow this shape.

### 1. Orient (read before you act)
- Read [`CLAUDE.md`](../CLAUDE.md) — rules and repo map.
- Read [`STATUS.md`](../STATUS.md) — current state and suggested next actions.
- Skim [`roadmap.md`](roadmap.md) for the open task you'll take, and
  [`tech-spec.md`](tech-spec.md) for the contract around it.
- If touching a settled area, check [`decisions.md`](decisions.md) so you don't
  reverse a deliberate choice by accident.

### 2. Scope
- Pick **one** coherent task, preferring `STATUS.md`'s "Suggested next actions".
- Confirm it doesn't conflict with an in-progress (`[~]`) item.
- If the task is ambiguous or would break an invariant/ADR, **ask the user**
  rather than guessing.

### 3. Execute
- Mark the roadmap task `[~]`.
- Make the change, honouring the invariants in `tech-spec.md` §1 and the working
  rules in `CLAUDE.md`.
- **Verify it.** Prefer running the thing over asserting it works. Run `pytest`
  in `backend/` (CI runs it too, browserless). Chromium changes should be checked
  via Docker (see `CLAUDE.md`). **Phase 6 (docling)** client/engine changes are
  verifiable against a **mocked** docling-serve transport — you do **not** need a
  live docling Space to build and test the core, only to verify the end-to-end
  deploy.

### 4. Record (the handoff — never skip this)
Before ending the session, leave the repo self-explanatory for the next instance:
- **`STATUS.md`** — update the at-a-glance table, gaps, next actions, and add a
  dated session-log entry (what changed, what's next).
- **`roadmap.md`** — flip checkboxes; `[x]` only if verified, else `[~]` + note.
- **`decisions.md`** — add an ADR for any non-trivial decision.
- **`tech-spec.md`** — update it in the *same commit* as any contract change, and
  bump the API `version` if the contract changed.
- **Commit** in coherent units with descriptive messages.

---

## Handoff checklist (copy into your final message or commit)

```
[ ] STATUS.md updated (table + gaps + next actions + dated log entry)
[ ] roadmap.md checkboxes reflect reality ([x] only if verified)
[ ] decisions.md has an ADR for any non-trivial choice
[ ] tech-spec.md matches behaviour; API version bumped if contract changed
[ ] changes verified (how? note it), or explicitly marked unverified
[ ] committed with a clear message
```

---

## Rules of thumb for a good handoff

- **Honesty over green.** "Written but not run" is `[~]`, not `[x]`. A false
  checkmark costs the next instance more than an honest gap.
- **Say what you *didn't* do.** Half-finished work is fine; silent half-finished
  work is a trap. Note it in `STATUS.md`.
- **One task, finished, beats three, dangling.** Prefer a small verified change
  with clean docs over broad unverified churn.
- **Don't relitigate ADRs silently.** If a past decision looks wrong, write a new
  ADR that supersedes it and explain why — don't just quietly do it differently.
- **Keep the layers and the contract intact** unless the task is explicitly to
  change them (then update `tech-spec.md` + `decisions.md`).
- **Two services now, not one.** The repo deploys a backend Space (FastAPI +
  Chrome) and, from Phase 6, a docling-serve Space. Keep them decoupled over HTTP
  and never make the backend hard-depend on docling — fall back (ADR-014).

---

## When to stop and ask the user

Use `AskUserQuestion` rather than guessing when:
- A task would break an invariant (`tech-spec.md` §1) or overturn an ADR.
- The contract must change in a breaking way.
- Scope is genuinely ambiguous and the choice materially changes the work.
- An external action is involved that's hard to reverse (deploying, deleting,
  publishing) and you weren't explicitly asked to do it.
