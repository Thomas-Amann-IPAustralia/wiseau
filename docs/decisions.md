# Architecture Decision Log (ADRs)

A running record of **non-trivial decisions and their rationale**, so a later
Claude Code instance doesn't relitigate a settled choice — or, if it should be
revisited, can see exactly what was weighed.

Add a new entry at the top when you make a decision that future work would
otherwise have to reverse-engineer. Keep entries short. When a decision is
overturned, don't delete it — add a new entry that supersedes it and mark the old
one `Superseded`.

**Entry template:**

```
## ADR-NNN — <short title>
**Date:** YYYY-MM-DD · **Status:** Accepted | Superseded by ADR-XXX | Proposed
**Context:** what forced a choice.
**Decision:** what we chose.
**Consequences:** what this makes easy/hard; what to watch for.
```

---

## ADR-000 — Establish the documentation foundation for an instance chain
**Date:** 2026-07-20 · **Status:** Accepted
**Context:** The project is executed by a series of Claude Code instances with no
shared memory beyond the repo. Without durable, role-separated docs, each
instance re-derives context and drifts off-spec.
**Decision:** Maintain six documents with distinct roles — `CLAUDE.md` (entry
point/rules), `STATUS.md` (live state), `project-brief.md` (vision),
`tech-spec.md` (contract), `roadmap.md` (task backlog), `decisions.md` (this
log) — plus `agent-workflow.md` (handoff protocol). Facts-vs-behaviour-vs-
rationale-vs-todo are kept in separate files to avoid overlap and staleness.
**Consequences:** Each instance has one predictable place to look and to update.
Cost: the docs must be kept current — enforced by the "update STATUS.md before
you finish" rule in `CLAUDE.md` and `agent-workflow.md`.

## ADR-001 — Deterministic algorithmic extraction over per-site selectors
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Content extraction can be done with hand-tuned CSS selectors per
site or algorithmically. Selectors are precise but brittle and non-portable.
**Decision:** Use Trafilatura for main-content extraction, with markdownify only
as a fallback when Trafilatura returns nothing. No per-site selectors.
**Consequences:** Robust across arbitrary layouts and the core of the determinism
guarantee. Some sites may extract imperfectly; that is accepted over brittleness.
Adding per-site logic would violate this ADR — open a new one if ever needed.

## ADR-002 — Open, public API guarded by fair-use limits, not origin locks
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** The API serves the hosted UI, forks, third-party frontends, and
agents. Locking CORS to one origin would block legitimate reuse.
**Decision:** Permissive CORS (`allow_origins=["*"]`, no credentials). Protect the
shared compute with per-IP rate limiting (`slowapi`) and a global concurrency
ceiling (`asyncio.Semaphore`) rather than access control.
**Consequences:** Anyone can call it ("pay it forward"). Abuse risk is real;
first defence is rate limiting, with quotas/API-key tiers held in reserve (brief
§7). Every heavy endpoint must stay behind both guards.

## ADR-003 — Single response contract for humans and agents
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Two consumer classes (UI, LLM/agents) could each get a tailored
response shape.
**Decision:** One `MarkdownResponse` (`source`, `markdown`, `length`) for both,
described by the auto-generated OpenAPI schema.
**Consequences:** One thing to maintain and version; MCP/agent integration reuses
it directly. Consumer-specific needs must be met additively, not by forking the
contract.

## ADR-004 — Decoupled static frontend + containerized backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** UI and engine have very different hosting and iteration needs.
**Decision:** Static frontend on GitHub Pages; containerized FastAPI backend on
Hugging Face Spaces; they communicate only over HTTP. The frontend's sole backend
coupling is `MARKDOWN_API_BASE` in `config.js`.
**Consequences:** Each layer deploys and evolves independently; free hosting on
both tiers. No build-time coupling is permitted between them.

## ADR-005 — Version-locked Docker image for the backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Headless Chromium extraction is sensitive to system-library and
browser versions; "works on my machine" drift would break determinism.
**Decision:** Ship a `Dockerfile` (Python 3.11-slim + system Chromium/chromedriver)
that version-locks the runtime; run as non-root UID 1000 for Hugging Face.
**Consequences:** Reproducible runtime. *Open follow-up:* Python dependencies in
`requirements.txt` are currently unpinned — pinning them is a roadmap item and
would complete this decision's intent.
