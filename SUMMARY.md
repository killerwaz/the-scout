> **Superseded by `PLAN.md` (2026-10-21).** Kept for history; don't plan from this file.

# Headless Browser Project — Conversation Summary

**Date:** 2026-08-30
**Status:** Planning complete, nothing written yet. Original build plan superseded by a tool audit.

---

## 1. Goal

A personal, self-hosted browser platform that lets an AI agent browse many pages in parallel
and autonomously find requested information, without human intervention and without recurring
cost.

Explicitly **not** a browser engine. Chromium is ~35M lines; the products that are actually
world-class (Browserbase, Steel, Browserless, Hyperbrowser) are *control planes* over pooled
Chromium instances, not engines.

---

## 2. Confirmed decisions

| Decision | Choice |
|---|---|
| Use cases | AI agent browsing, scraping/extraction, personal automation |
| Stealth posture | Serious hardening |
| Stack | TypeScript |
| Deployment | Pluggable executor: native Windows Chrome + Docker pool |
| Cost ceiling | Zero recurring. No paid index, no paid API. Local GPU only. |
| Autonomy | Fully unattended. No human in the loop. |
| Rate limiting | Per-domain only. Global concurrency stays wide open. |

**Environment:** Node v24.15.0, npm 11.12.1, Docker 29.6.2,
Chrome 151.0.7922.175 at `C:\Program Files\Google\Chrome\Application\chrome.exe`

---

## 3. Requirements

| # | Requirement |
|---|---|
| R1 | Self-hosted, zero recurring cost |
| R2 | Native Chrome executor + Docker pool executor |
| R3 | Serious stealth hardening |
| R4 | Encrypted persistent profile vault (own logins) |
| R5 | Semantic layer: accessibility snapshot + element refs, not screenshots |
| R6 | MCP server so Claude can drive it |
| R7 | Autonomous research loop: plan → fan out → score → decide → synthesize |
| R8 | 20–25 parallel browser contexts |
| R9 | No paid search index |
| R10 | Local GPU (Ollama) for extraction, relevance scoring, embeddings |
| R11 | Block recovery ladder incl. archive fallback |
| R12 | Open-data API routing (EDGAR, arXiv, PubMed, CourtListener) |
| R13 | Session trace / HAR replay |
| R14 | Runs unattended; blocked sources never halt the run |

---

## 4. Key technical findings

### 4.1 Google `/goto` redirects — CONFIRMED, and it's new

Google rolled out `google.com/goto?url=[encoded]` in place of direct result links.
Confirmed by Google on **26 Aug 2026**, first spotted testing July 2026. Near-100% rollout
across residential IPs per Nozzle. Google's stated reason is abuse protection; timing aligns
with their SerpAPI litigation.

**The open question that determines difficulty:**

- **Case A** — payload is an encoded URL (base64-like). Decode locally. Free. Non-issue.
- **Case B** — payload is an opaque server-side ID. Must call Google to resolve *each* result.
  100 results becomes 101 requests → burst pattern → `/sorry/index` CAPTCHA.

Reporting says "base64-like encoding of URL **or** Google ID" — genuinely ambiguous.
**Untested. This is the first thing to probe.**

### 4.2 Parallelism does not conflict with per-domain limits

A research fan-out hits ~24 *different* domains, one page each. Each domain sees one request.
The per-domain limiter never fires. Full parallel speed, no throttle.

Rate limiting only binds on deep single-site crawls. Hence two job profiles:

| Profile | Shape | Concurrency | Per-domain limit |
|---|---|---|---|
| `research` | 30 domains × 1 page | 20–25 parallel | never fires |
| `harvest` | 1 domain × 500 pages | 20 parallel | required |

### 4.3 A local GPU cannot fix most blocks

Blocking decisions usually happen before any pixel exists:
IP reputation (connection time), TLS fingerprint JA3/JA4 (first packet),
HTTP/2 frame ordering (pre-render), request rate (arithmetic).

All network-layer. A GPU only helps with visual challenges, which are the minority.

**GPU's real value:** local LLM extraction (replaces per-page API cost — the biggest saving),
embeddings for semantic search, vision fallback on canvas-rendered pages.

**Strongest free anti-block assets, ranked:** home residential IP (already owned; commercial
scrapers pay ~$10/GB to fake it) → real Chrome TLS fingerprint → caching → persistent
per-site cookies.

### 4.4 "Blocked" is ~8 different failures, not one

| Cause | Share | Fix |
|---|---|---|
| Consent/cookie wall | large | click it |
| Needs JS rendering | large | real browser |
| Login wall, account held | medium | profile vault |
| 429 rate limit | medium | backoff, new context |
| Soft paywall, content in DOM/JSON-LD | medium | read what the server sent |
| Geo-block | small | different egress |
| WAF / CAPTCHA | small | out of scope (see §7) |
| Hard paywall, no account | small | nothing works |

**Recovery ladder** — a lead dies only after six attempts:
render → dismiss → open data path (RSS/JSON-LD/sitemap/API) → authenticate → retry →
archive (Wayback/archive.today) → *then* substitute source.

Rungs 3 and 6 are the highest-yield and most commonly skipped.

### 4.5 Open-data backdoors

For whole question categories, browsing is the wrong method — free, unlimited, unblocked
APIs exist with better structure than the HTML: SEC EDGAR, arXiv, PubMed, Semantic Scholar,
OpenAlex (~250M papers), CourtListener, Wikipedia/Wikidata dumps, Common Crawl.
Route here first when the question fits.

---

## 5. Tool audit — what already exists

**Finding: no single tool matches all requirements, but ~70% of the original build plan
already exists as open source.**

### vercel-labs/agent-browser — the big one
Apache 2.0 · Rust daemon over CDP · **41.5k stars** · https://github.com/vercel-labs/agent-browser

Covers R4, R5, R6, R13, and partially R8: element refs (`@e1`, `@e2`) built for LLMs,
accessibility-tree snapshots, session persistence with encryption, auth vault, HAR recording,
network interception, session isolation, MCP server.

This is functionally identical to the originally-planned Phases 0–2.

**Gap:** stealth only via *paid* cloud providers (Kernel, Browserless). No local stealth.
Open feature request: issue #120.

### Steel Browser
Apache 2.0 · TS/Docker · 7.6k stars · https://github.com/steel-dev/steel-browser

Closest on infrastructure: stealth plugins, fingerprint management, proxy chains, Docker.
**Catch:** Steel Local is *single-session*; the Chromium-fork Stealth Browser, Credentials API,
and CAPTCHA solving are Cloud-only. Self-hosted Steel delivers neither R8 nor its best R3.

### local-deep-research (LearningCircuit)
MIT · Python/LangGraph · 9k+ stars · https://github.com/LearningCircuit/local-deep-research

Covers R7, R9, R10, R12 and part of R11. Ollama-local, SearXNG, and its search backends are
the open-data router almost verbatim — arXiv, PubMed, Semantic Scholar, GitHub, **and Wayback
Machine as a first-class backend**. Benchmark: ~95% SimpleQA on Qwen3.6-27B, single RTX 3090.

**Blocker:** explicitly *"respects robots.txt and identifies itself honestly — no stealth or
anti-detection."* Also search-API driven, not browser driven. Fails R3 and R5.

### Camoufox
Strongest open-source stealth — evasion compiled into Firefox itself, 0% headless detection.
https://github.com/daijro/camoufox

**Caveats:** year-long maintenance gap, degraded by aging Firefox base, 200MB+ per instance,
~42s average on Turnstile, and cannot emulate Chromium fingerprints — which conflicts with R2,
whose whole point is matching your real Windows Chrome device.

### The whitespace
**Stealth + autonomous research in one tool does not exist.** Not an oversight: research agents
refuse stealth on ethical grounds, stealth tools don't do orchestration. Every project picks a
side. That gap is the actual build.

---

## 6. Revised plan

| Layer | Decision | Effort |
|---|---|---|
| Driver, refs, vault, MCP, HAR | **Adopt agent-browser** | 0 — kills old Phases 0–2 |
| Stealth | **Build** — Patchright/Camoufox provider behind its interface | ~2 days |
| Orchestrator | **Build** — browser-driven, architecture borrowed from LDR | ~2 days |
| Recovery ladder + open-data router | **Port from LDR** | ~1 day |
| Search | **SearXNG self-hosted in Docker** — free, no API key | 15 min |

**~5 days instead of 3+ weeks**, on a base with 41.5k stars of hardening.

### Orchestrator design
1. **Plan** — decompose goal into sub-questions (local LLM)
2. **Frontier** — scored URL queue, deduped by content hash
3. **Fan out** — N contexts pull in parallel
4. **Score** — each page rated against goal (local LLM — the GPU's main job)
5. **Decide** — synthesize, or expand frontier and loop

**Termination is critical for unattended runs.** Stop on budget (max pages / max minutes) or
when marginal new information per page falls below threshold. Without it, a run burns hours
on a question answered in the first 90 seconds.

### Concurrency
Browser *context* ≈ 80–120 MB with isolated cookies. Full process ≈ 400 MB.
Run ~20–25 contexts across 3–4 Chrome processes — cookie isolation at a fifth of the RAM.
A 25-tab fan-out completes in 40–90s.

---

## 7. Scope boundary

**No CAPTCHA/WAF solver.** Blocked pages are handled by the six-rung recovery ladder above.
When a challenge does appear, the default is: log it, park the lead, keep the other tabs
running, report it as "1 source unreachable." **The run never halts** (satisfies R14).

Search providers deliberately exclude Google proxies (SerpAPI, Serper) — they are both the
technical target of `/goto` and the defendant in Google's lawsuit. Self-hosted SearXNG
instead: free, no key, multi-engine fallback.

---

## 8. Corrections made during this conversation

Recorded because they changed decisions:

1. **`/url?q=` claimed retired** — stale. Google shipped the new `/goto` redirect on
   2026-08-26, after the assistant's May 2026 knowledge cutoff.
2. **SerpAPI/Serper recommended** — reversed. Both are downstream of the party actively
   engineering against them.
3. **Brave/Exa recommended** — dropped on the zero-cost constraint. Replaced by self-hosted SearXNG.
4. **"Blocked source becomes a dead lead"** — wrong. Collapsed eight distinct failure modes
   into one. Replaced by the recovery ladder (§4.4).
5. **Original 6-phase build plan** — superseded. Phases 0–2 duplicated agent-browser.

---

## 9. Open questions

1. **`/goto` Case A vs Case B** — untested, determines link-resolution cost. First probe.
2. **agent-browser stealth interface** — is its provider abstraction extensible enough to
   accept a local Patchright/Camoufox backend, or does it need a fork?
3. **Concurrency ceiling** — 20–25 contexts is an estimate; needs measuring on this box.
4. **Chromium vs Firefox stealth** — Camoufox is stronger but breaks the native-Windows-Chrome
   fingerprint match that R2 exists for.

---

## 10. Next action

Install agent-browser and run the `/goto` probe through it. Answers Q1 and Q2 together,
in about 10 minutes, before committing to the stack.

---

## Sources

- [Search Engine Land — Google confirms /goto redirects](https://searchengineland.com/google-confirms-deploying-goto-url-redirects-to-search-results-links-485926)
- [Seroundtable — google.com/goto tracking](https://www.seroundtable.com/google-search-goto-tracking-41957.html)
- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)
- [steel-dev/steel-browser](https://github.com/steel-dev/steel-browser)
- [LearningCircuit/local-deep-research](https://github.com/LearningCircuit/local-deep-research)
- [daijro/camoufox](https://github.com/daijro/camoufox)
