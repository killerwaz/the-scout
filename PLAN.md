# PLAN — The Scout (personal headless browser agent)

**This is the working plan. Update it as we build:** tick boxes, move the "▶ Now" marker,
add a line to the Progress log at the bottom. If a decision changes, change it here first.

**Created:** 2026-10-21 · **Supersedes:** `SUMMARY.md` (Aug 30 TypeScript plan) and
`CHAT_SUMMARY.md`. `AUDIT.md` is still the evidence base for the stealth and discovery
decisions below, but its "20–25 parallel contexts" numbers are out of date (see §3).

---

## 1. What we're building

An AI agent that uses a real browser on your PC to find information or get things done,
across many tabs, without you stepping in. You start it from Claude chat or Telegram.

Things it should handle:

- "What did n8n and Anthropic ship this week?" (news)
- "Best noise-cancelling headphones under $300, with reasons." (decision research)
- "Every 2-bed rental in Austin on Zillow." (pull data)
- "What has Anthropic posted on X this month?" (people & companies)
- "Log into my utility account and download last month's bill." (act like a Cowork session)
  — works only on a session you've already logged into; see §5 "Expired sessions"

### Not doing

- No cloud hosting. It runs on this PC only
- No other users. One person, one Telegram ID
- No paid search APIs, CAPTCHA-solving services, or paid proxies
- No Docker pool for browsers (see §3). n8n runs in your existing Docker; the Scout and Chrome stay on Windows
- No TypeScript rewrite

---

## 2. Decisions (locked 2026-10-21)

| Topic | Decision |
|---|---|
| Language | Python. Builds on the existing `fetcher.py` (v1.6) |
| Search | **Free only. Gaps are accepted.** No paid search API, ever. **One exception (2026-10-26):** Brave Search API on its free tier only, and optional. It is used only when a key is in `.env`, and the free engines take over when there's no key or the free quota runs out. Never a paid plan |
| How you start it | **Claude chat** (via MCP), **Telegram**, and **n8n workflows** (added 2026-10-28 at your call; Phase 9) |
| Telegram brain | **Both, picked per message.** Normal message → Claude (`claude -p`, your subscription). `/local …` → local model on the GPU |
| Autonomy | **Full.** It may click, fill forms, submit, and send without asking. Hard limits in §5 |
| Output | Answer in chat/terminal. Markdown report only when you ask (`/report`) |
| Stealth | The stealth levels L0–L4 (§2a). L0–L3 get built; L4 stays a stub |
| CAPTCHAs | Not solved. Lead gets parked and named in the answer; the run carries on |

### 2a. Two ladders — don't mix them up

There are two numbered schemes. **Always say which one you mean.**

**Stealth levels (L0–L4)** — the strategy, from `AUDIT.md` §7b. Use these in this plan.

| Level | What |
|---|---|
| L0 | Feeds, sitemaps, JSON-LD, open-data APIs (no page load at all) |
| L1 | Real Chromium + your home IP |
| L2 | Patchright as the default browser |
| L3 | Persistent real-Chrome profile + saved cookies (unlocks X, LinkedIn, Meta) |
| L4 | Behaviour simulation, proxy rotation, archive substitution — **stub** |

**`Tier` enum (0–5)** — the code, in `fetcher.py:31`. Use these names (`Tier.HTTP`, not
"tier 0") when talking about the fetcher.

| `Tier` | What | Status |
|---|---|---|
| `HTTP` = 0 | curl_cffi with TLS impersonation, no browser (~50 ms) | built |
| `SIGNED` = 1 | declared UA + Web Bot Auth signature | stub |
| `BROWSER` = 2 | Patchright, headed Chrome, persistent profile (~2–4 s) | built |
| `CDP` = 3 | nodriver/zendriver, CDP-direct | stub |
| `ENGINE` = 4 | Camoufox | stub |
| `VENDOR` = 5 | managed signed-agent session | stub |

Rough mapping: L1–L2 ≈ `Tier.BROWSER`; L3 = `Tier.BROWSER` + `SessionStore` + a real
profile; L0 sits above the fetcher (Phase 2 `search.py`), not in the `Tier` enum.

## 3. Hardware budget

RTX 5070 (12 GB VRAM) · **16 GB system RAM** · Ryzen 7 7700.

- **RAM is the limit.** Plan for **about 8 parallel tabs**, not 20–25. That's 8 contexts
  in one Chrome, roughly 1–1.5 GB. Phase 3 measures the real ceiling.
  **Measured 2026-10-21:** ~400 MB + ~325 MB per tab, so 8 tabs ≈ 3 GB, not 1–1.5 GB. With
  other apps open, this PC had 0.8–2 GB free, enough for 1–3 tabs (see §7 question 11).
- **Local model:** a ~14B model at 4-bit (for example Qwen3 14B, about 9 GB) fits in VRAM.
  27B models don't fit. Phase 5 picks the model after testing.
- **Docker pool: dropped.** Not enough RAM to run containers alongside Chrome and a model.
  **n8n is the exception (2026-10-28):** it already runs in your Docker (~0.4 GB, next to `crawl4ai`
  ~0.3 GB). The Scout doesn't move into Docker: your logged-in Scout profile and the GPU are on Windows.

---

## 4. Architecture

```
  Claude chat (MCP)    Telegram bot (you only)    n8n in Docker (Phase 9)
            \                  |                   /  HTTP + token, 127.0.0.1:8787
             +---------- Orchestrator ------------+     plan → search → fan out → score → decide → answer
                       |                brain: Claude (claude -p) or local (Ollama)
          +------------+-------------+
     Discovery      Tab pool       Act layer
   (free search,   (~8 parallel    (snapshot + refs,
    feeds, APIs)    contexts)       click/type/submit)
          \            |             /
           +------ fetcher.py -------+   ✅ BUILT: HTTP → real Chrome ladder,
                                          block types, sessions, pacer, cache, budget
```

### 4a. Key files and entry points

| File | What it is |
|---|---|
| `fetcher.py` | The engine (~1,380 lines). Don't read it all; start from the names below |
| `search.py` | Discovery: free engines, Brave API, direct APIs, feeds, Wayback (Phase 2) |
| `tabpool.py` | Parallel tabs in one Chrome for pages that need a browser (Phase 3) |
| `act.py` | Snapshot + refs, `act()`, the hard limits (§5) in `check()`, per-surface stop (Phase 4, 8) |
| `scout.py` | The research loop and the two brains (Phase 5) |
| `mcp_server.py` | The 10 tools in Claude chat (Phase 6, 8) |
| `telegram_bot.py` | The Telegram bot, your user ID only (Phase 6) |
| `api_server.py` | Local HTTP API for n8n on `127.0.0.1:8787`, token in `.env`; reuses `mcp_server`'s `do_*` as the `n8n` surface (Phase 9) |
| `n8n/scout-morning-digest.json` | Sample n8n workflow: 07:00 or by hand → `/research` → answer on Telegram (Phase 9) |
| `check.py` | Offline self-test, no network: `python check.py`. Every module has its own `check_*.py` (incl. `check_api.py`); all must pass |
| `run_checks.py` | Runs every `check_*.py` and `regress.py`; exits 1 if any check fails: `python run_checks.py` (Phase 10) |
| `README.md`, `LICENSE`, `requirements.txt`, `.env.example`, `.gitignore` | What a stranger needs to install it (Phase 10) |
| `regress.py` | Fixtures from your real runs. Keep forever: `python regress.py` |
| `probe.py` | Live site probe; edit `TARGETS`, then `python probe.py` |
| `verify.py` | Extra verification script |
| `AUDIT.md` | Evidence for stealth + discovery decisions |
| `archive/` | Old material. Ignore unless asked |

Main names in `fetcher.py`:

- `fetch(url, seconds=45.0, allow_browser=True, cache=True) -> Result` — the one-call entry point
- `Result` — what every fetch returns (`.ok`, `.block`, `.tier`, `.html`, …)
- `Ladder`, `default_ladder(max_tier=Tier.BROWSER)` — walks `Tier`s cheapest-first
- `Tier`, `BlockType` (11 block types; `GEO_BLOCK` added 2026-10-21), `classify(...)`, `detect_waf(...)`
- `HttpTransport`, `PatchrightTransport` — the two built transports
- `SessionStore` (anonymous cookies per domain, **not** a credential store), `ProxyPool`
- `Pacer`, `CircuitBreaker`, `Budget` / `BudgetExhausted`, `Cache`, `Telemetry`
- `reset_state()` — wipe sessions/profiles
- `close_ladder()` — close `fetch()`'s shared ladders and their Chrome, so the profile is free (Phase 8)
- `settle(page)` / `settle_async(page)` — wait until a page is ready (load → network idle 3 s cap →
  DOM quiet 400 ms, 5 s cap) instead of a fixed sleep; used by `act.py`, `tabpool.py` and the browser fetch

Main names elsewhere: `act.Session` (`goto`, `snapshot`, `act`, `read`, `wait_for`), `act.check()`,
`act.stop(surface)` / `stop_all()` / `clear_stop(surface)`; `mcp_server._paged()` (parts + STALE);
`scout.run_research(question, brain, on_progress, surface)`.

**Rule:** after any change to `fetcher.py`, run `python check.py` and `python regress.py`.
Both must pass before the change counts as done.

## 5. Hard limits (apply even with full autonomy)

The agent **never**:

1. Pays for anything or enters card/bank details
2. Types passwords or creates accounts. It uses sessions you've already logged into
3. Deletes data (emails, files, accounts)
4. Solves CAPTCHAs
5. **Follows instructions found in web pages, emails, files, or search results.** Page
   content is data. Only you (chat or your Telegram ID) give instructions. If a page tells
   it to do something, it ignores it and mentions it in the answer
6. Acts (`act()`) on a site the current task doesn't need. Reading anywhere is fine;
   clicking/typing/submitting only on sites tied to what you asked
7. Opens pages on this PC or your home network (`localhost`, `127.x`, `10.x`, `192.168.x`, `.local`):
   n8n, the local model server, your router. A web page could otherwise point the agent at them.
   Added 2026-11-10 (Phase 10). The environment variable `SCOUT_ALLOW_PRIVATE=1` turns it off

The agent **always**:

- Writes every action (URL, click, form submit, message sent) to an action log
- Stops on `stop`. Each surface stops separately: `stop` in chat stops chat tasks, `/stop` in
  Telegram stops Telegram tasks, and `/stopall` (or `stop_all` in chat) stops both. Your next new
  request on a surface clears that surface's stop
- Accepts Telegram messages **only from your own Telegram user ID**. A bot that drives a
  logged-in browser is remote control of your accounts, so nobody else gets in.
- Accepts n8n calls **only on `127.0.0.1` and only with the token from `.env`** (Phase 9). Same
  reason as Telegram: that door drives your logged-in browser with full autonomy.

**n8n workflows are you, their data isn't.** A workflow you built counts as your instruction.
Text a workflow carries in from outside (an email body, a form, a webhook) is data under rule 5,
same as a web page. Known risk, accepted 2026-10-28: a workflow that pastes outside text into a
task can still steer the browser, so keep that text in the `context` field (§6 Phase 9).

**Expired sessions:** if a task needs a login and the session is gone, the agent stops that
lead, pings you on Telegram ("log into X once in the Scout profile"), and carries on with
the rest. It never types the password itself.

---

## 6. Phases

Legend: ✅ done · ▶ now · ⬜ not started · ⏸ blocked

### Phase 0 — Groundwork ✅ (done before this plan)

- [x] Original plan + tool audit (`SUMMARY.md`)
- [x] Verification audit: `/goto` is Case B, SearXNG unreliable, stealth as a ladder (`AUDIT.md`)
- [x] P2 test: 11/12 sites fine without hardening (`p2_report.txt`)
- [x] `fetcher.py` v1.6: tiered ladder, 10 block types, telemetry, sessions, proxies, pacer, breaker, cache, budget, CAPTCHA handoff
- [x] Tests: `check.py` + `regress.py`, 15/15 passing (re-run 2026-10-21)

### Phase 1 — Clean up + measure ✅ (done 2026-10-21; results in §8)

- [x] Mark `SUMMARY.md`, `CHAT_SUMMARY.md` and the `p2_report.txt` verdict as superseded (one header line each)
- [x] Add a header note to `AUDIT.md`: parallel count is now ~8, not 20–25 (PLAN §3)
- [x] I draft a real target list from your 5 use cases → you edit it
- [x] Put it in `probe.py` `TARGETS`, run `python probe.py`, record per site the lowest
      `Tier` that works and the stealth level (L0–L3) it needs
- [x] P2b: repeat visits, deep pages (not homepages), one `harvest` run (one site, ~50 pages)
- [x] Re-test the x.com client-hint mismatch theory (AUDIT §7b)

**Done when:** a table in the Progress log says, per target, which `Tier` / level works and what breaks.

### Phase 2 — Discovery, free only ✅ (done 2026-10-21; 62/100 relevant, 10/10 questions ≥5)

Finds *which pages to read*. Biggest risk area (AUDIT §3).

- [x] Write the 10 test questions in `tests/questions.md` (2 per use case in §1) — approved 2026-10-21
- [x] `search.py` with swappable sources: DuckDuckGo HTML, Bing, Mojeek, Wikipedia. Engines
      that refuse over HTTP get one retry through `Tier.BROWSER`; results sharing no word with
      the query count as "poisoned" (a quiet refusal)
- [x] Direct APIs (free, no key) tried first when the question fits: HN Algolia, Reddit JSON,
      GitHub, arXiv, OpenAlex, SEC EDGAR. Reddit JSON 403s from here; Reddit is reached via
      `site:reddit.com` engine queries instead. Routing is keyword rules (`pick_sources`) until Phase 5
- [x] L0: RSS, sitemaps, JSON-LD on known sites (`read_feed`, `discover_feeds`, `sitemap_urls`, `jsonld`)
- [x] Archive fallback (Wayback) for pages that block (`wayback`, `fetch_or_archive`)
- [x] Health tracking: a source that fails 3× gets skipped for an hour, and the answer says
      "Google unavailable, used Bing + DDG"
- [x] Skip Google results pages entirely (`/goto` makes them cost 500+ requests)
- [x] Verify: `python check_search.py` (offline) passes; `python tests/run_questions.py`
      writes `tests/results-<date>.md` (`--only 4` reruns one question into its own file);
      **you mark each link y/n**, I record the hit rate

**Done when:** each of the 10 questions returns ≥5 relevant links with no paid service.
"Relevant" = you'd click it: on topic, from the right time window, not a spam/SEO farm.
You judge the first run; I record the hit rate.

### Phase 3 — Parallel tabs ✅ (done 2026-10-21; results in §8)

- [x] Tab pool: N tabs in one Chrome, each pulling from a shared queue (`tabpool.py`). Built as
      N tabs in **one** persistent context rather than N contexts: one cookie jar per site for
      the run, and lighter on RAM. Per-tab fingerprints stay Phase 7
- [x] Per-site pacer and circuit breaker shared across tabs (`AsyncPacer`, fetcher's `CircuitBreaker`)
- [x] Same site → same context/cookies within a run (session continuity, AUDIT §2)
- [x] Measure the RAM ceiling on this PC; set default N from that → N is picked from free RAM
      at start (`tabs_for(free_ram_mb())`, max 8), because free RAM here swings from <1 GB to ~2 GB
- [x] Verify: `python check_tabpool.py` (offline) passes

**Done when:** 8 different sites fetch in parallel, total time ≈ the slowest single page.

### Phase 4 — Act layer (Cowork-style) ✅ (done 2026-10-21; results in §8)

- [x] `snapshot()`: accessibility tree → short text with refs (`[e12] button "Search"`).
      Same idea as agent-browser, written in Python on Patchright (`act.py`; built on
      Patchright's `aria_snapshot(mode="ai")` + `aria-ref=` locators, compacted ~3.5×)
- [x] `act(ref, click|type|select|scroll|submit)`
- [x] Action log + the hard limits in §5 enforced in code (blocked actions raise, not warn).
      Includes the site allowlist (§5.6). Log: `~/.fetcher/actions.jsonl`; `act.stop()` halts
- [x] Logged-in sessions reused from a Chrome profile (L3) — dedicated Scout profile (open
      question 4 → (a)). Cookies survive restarts (checked); **a real login is not tested yet:
      run `python act.py login <url>` for a site you want it to use**
- [x] Verify: `python check_act.py` (offline) passes
- [x] Independent review of the hard limits (one subagent, xhigh) — 21 findings, all fixed (§8)

**Done when:** it searches a site, applies a filter, opens a result, and reads it, all through refs.

### Phase 5 — Orchestrator + brains ✅ (done 2026-10-21; both brains answer the headphones question in ~1 min)

- [x] Loop: plan sub-questions → discovery → tab pool → score each page → enough? answer : expand
      (`scout.py`; HTTP first, tab pool only for pages that need a browser)
- [x] Page score (brain rates 0–3): 0 off-topic · 1 on-topic, nothing new · 2 adds a fact
      the answer needs · 3 primary source (official page, filing, the post itself)
- [x] "Enough" = every sub-question has ≥2 sources scoring 2+, or one scoring 3
- [x] Stop rules (defaults, change freely): **40 pages**, **5 minutes**, or "last 5 pages
      added nothing new" (all scored ≤1) — whichever comes first
- [x] `ClaudeBrain` via `claude -p` — built: no tools, no MCP, clean environment.
      ⏸ **Not run yet: the standalone `claude` CLI isn't logged in** (you, once)
- [x] `LocalBrain` — built against the OpenAI-style API, so it works with **LM Studio (already
      installed, has `qwen/qwen3.5-9b`)** or Ollama; nothing to install. ⏸ **Not run yet: LM Studio's
      server requires an API token** (you, once: token in `.env`, or turn the requirement off)
- [x] Pick the local model by testing (open question 3) — `qwen/qwen3.5-9b` in LM Studio, the
      only installed model that fits the GPU; it works but is weaker than Claude (§8). Trying
      other models means downloads — your call. `ClaudeBrain` ✅ live (CLI logged in 2026-10-21)
- [x] Answer format: short answer + numbered sources + "couldn't reach: …" line
- [x] `/report` writes `reports/YYYY-MM-DD-<topic>.md`
- [x] Verify: `python check_scout.py` (offline) passes

**Done when:** the headphones question gets a sourced answer in under 3 minutes, unattended.

### Phase 6 — Surfaces ✅ (~1 day)

- [x] MCP server (Python `mcp` SDK v2): `research`, `research_status`, `fetch_page`, `browse`,
      `snapshot(part)`, `read_page(part)`, `wait_for`, `act`, `stop`, `stop_all` — 10 tools since
      Phase 8 (`mcp_server.py`). Tested live over stdio. ⏸ **You: register it in Claude** (Settings →
      Developer → Edit Config)
- [x] Telegram bot: allowlisted user ID, `/local`, `/stop`, `/stopall` (Phase 8), `/report`, progress pings
      (`telegram_bot.py`, stdlib only). ⏸ **You: create the bot with @BotFather, put
      `TELEGRAM_BOT_TOKEN` in `.env`, run `python telegram_bot.py whoami`, put
      `TELEGRAM_ALLOWED_USER_ID` in `.env`**
- [x] Runs in the background on Windows startup — `the-scout-telegram.cmd` in your Startup folder
- [x] Claude chat and Telegram use separate Chrome profiles (`chrome-profile-mcp` /
      `chrome-profile-telegram`); verified live in Claude chat 2026-10-21
- [x] Verify: `python check_surfaces.py` (offline) passes

**Done when:** the same question asked in Claude chat and on Telegram both get answers.

### Phase 7 — Hardening, only where Phase 1 says so ✅ (~½ day + as needed)

- [x] ~~Fix User-Agent / client-hint coherence~~ dropped at your call: Phase 1 got 200 on all 10
      x.com setups, mismatched ones included; X timelines need login (L3), not a fingerprint fix
- [x] Different fingerprints per tab: deferred, build only when a site blocks parallel tabs.
      Tabs stay in one Chrome (one fingerprint) because a Chrome per tab is ~400 MB each and would
      cut 8 tabs to 2–3 on your usual free RAM
- [x] L4 (behaviour simulation, proxies) stays a stub unless a target you need demands it

### Phase 8 — Reliability fixes from the browsentic comparison ✅ (code 2026-10-26; live checks done 2026-10-29)

Source: 2026-10-26 gap review (§8). Order = do first. Write each test first and watch it fail.

- [x] **Stop per surface + recover a dead Chrome** (§5, Q15). `act.py`: one stop file per surface
      (`~/.fetcher/STOP.mcp`, `STOP.telegram`, `STOP.cli`); `stop_all()` writes all of them (no
      separate `STOP.all`, so one surface's new request can't clear the other's); `Session` takes the surface name.
      A new user request (`browse`/`research`/Telegram message) clears only its own surface's file.
      Add `stop_all` (MCP) and `/stopall` (Telegram). `mcp_server._guarded`: catch Playwright
      errors. On "target/browser closed", drop `_session` and return
      `ERROR BROWSER_CLOSED: call browse again`.
      Verify: `check_act.py` + `check_surfaces.py`. Telegram stop leaves MCP alone; `stop_all`
      stops both; `browse` works right after `stop` + new request; a fake closed browser recovers
- [x] **Rent words only blocked at the pay/apply step** (Q14). `act.py` `_PAYMENT`/`_ACCOUNT`
      (~156–171): drop bare `rent`/`reserve`/`subscribe`/`join`. Block them only in pay-shaped
      phrases ("rent now", "pay rent", a currency amount on the control), on "Apply" / tour or
      application forms asking for an SSN or fee, or on any page with payment fields. Luhn, IBAN,
      card-field and password checks don't change.
      Verify: `check_act.py` new cases. "For Rent" tab allowed, "Reserve" date picker allowed;
      "Rent now – $1,200", "Apply", and an SSN form blocked; all old blocked cases still blocked
- [x] **No silent truncation** (`mcp_server._fmt`, `MAX_TEXT`). Cut at a line break and add
      `TRUNCATED: part 1 of N; call snapshot(part=2)`. `snapshot(part=1)` pages through. A digest
      of the snapshot returns `STALE: page changed, call snapshot()` if it changed between parts.
      Expose `Session.read()` as `read_page(part=1)` with the same paging.
      Verify: `check_surfaces.py`. 70k-char fake page gives 3 parts, every ref reachable; changed
      page gives STALE
- [x] **`fetch_page` stops holding the research profile** (`fetcher.py` `_LADDER`, ~1230/1303).
      One ladder per `allow_browser` value plus `fetcher.close_ladder()`. `mcp_server.do_fetch`
      closes it in `finally` when the browser tier was used.
      Verify: `check.py` + `check_surfaces.py`. After a browser `fetch_page`, a research TabPool
      opens `chrome-profile-mcp` with no profile-lock error; live: `fetch_page` on a browser-tier
      site, then `research`, in one Claude chat
- [x] **Real waits instead of fixed sleeps.** One `settle(page)` helper: domcontentloaded →
      networkidle (3 s cap, timeout ignored) → DOM quiet for 400 ms (MutationObserver in the
      isolated world, 5 s cap). Use it in `act.Session._settle`, `tabpool.py` (~158) and the
      browser fetch (`fetcher.py` ~730). New MCP tool `wait_for(text, timeout=10)`.
      Verify: all `check_*.py` + `regress.py` pass; live: a Messenger thread reads after
      "Loading messages" is gone; time `regress.py` against before
- [x] Update §4a key files and the Phase 6 tool list (now 10 tools: + `stop_all`, `read_page`,
      `wait_for`)

**Done when:** every `check_*.py` and `regress.py` pass, and one live Claude-chat session runs
`stop` → new `browse`, Zillow "For Rent", a long page read in parts, and `fetch_page` → `research`
without a restart or a block.

### Phase 9 — n8n integration ⏸ (code done 2026-10-28; your live check is left)

n8n (already running in Docker, port 5678) starts Scout tasks over a local HTTP API, the same
way Claude chat uses MCP. Write each test first and watch it fail.

- [x] **`api_server.py`**: stdlib `ThreadingHTTPServer` on `127.0.0.1:8787` (checked 2026-10-28:
      n8n reaches a 127.0.0.1-only server at `host.docker.internal`, so nothing listens on your
      network). Endpoints mirror the MCP tools: `POST /research`, `/fetch`, `/browse`, `/snapshot`,
      `/read`, `/act`, `/wait_for`, `/stop`, `/stop_all`; `GET /jobs/<id>`, `GET /health`.
      Reuse `mcp_server`'s `do_*` helpers (made surface-aware) rather than copying them.
      Verify: `check_api.py`. Each endpoint returns what the matching MCP tool returns
- [x] **Long runs as jobs.** `/research` returns `{"job_id"}` at once; `GET /jobs/<id>` gives
      status, progress lines and the answer. `"wait": 120` holds the reply up to 120 s for simple
      workflows. One job at a time: a second one gets `409 busy` (like Telegram's "still working").
      Verify: fake runner; job finishes, a second job gets 409, `wait` times out into `running`
- [x] **New surface `n8n`.** `act.SURFACES` gains `n8n` → `STOP.n8n`; `stop_all()` and Telegram
      `/stopall` also stop n8n jobs; Chrome profile `chrome-profile-n8n` (Chrome locks a profile,
      §8 2026-10-21). Verify: `check_act.py` + `check_surfaces.py`. Telegram `/stop` leaves n8n
      running; `/stopall` stops it; new n8n request clears only `STOP.n8n`
- [x] **Token + logging.** `SCOUT_API_TOKEN` in `.env` (I generate a random one into `.env`; you copy
      it into an n8n "Header Auth" credential; it never goes in chat). Missing/wrong token → 401,
      compared in constant time. Every call goes to the action log. Request fields `question` /
      `url` = instruction; optional `context` = data, fenced like page text (§5).
      Verify: `check_api.py` (no token, wrong token, `context` with "ignore previous instructions")
- [x] **Answers to Telegram (optional).** `"notify": "telegram"` sends the finished answer through
      the existing bot to your user ID, so a workflow doesn't need its own Telegram node
- [x] **Starts with Windows**: `api_server.py install-startup` / `remove-startup` (same pattern as
      `telegram_bot.py`)
- [x] **Sample workflow** `n8n/scout-morning-digest.json`: Schedule 07:00 → HTTP Request
      `POST http://host.docker.internal:8787/research` → answer to Telegram. You import it in n8n
- [x] Update §4a key files
- [ ] **Live check (yours):** `make-token` → run the server → n8n credential → import + run the sample

**Done when:** the imported sample workflow, run by hand in n8n, gets a sourced answer on
Telegram; a request without the token gets 401; `stop_all` from Claude chat stops a running n8n job.

### Phase 10 — Publish prep ▶ (~½ day; added 2026-11-10)

Source: 2026-11-10 readiness audit (§8). Goal: safe to push to a repo, and a stranger can install it.
Write each test first and watch it fail.

- [x] **Repo files.** `.gitignore` (`.env`, `__pycache__/`, `.claude/settings.local.json`, `reports/`),
      `.env.example` (key names only), `requirements.txt` (pinned), `LICENSE` (MIT), `README.md`.
      Verify: the fresh-eyes review follows the README from scratch
- [x] **Tests can gate.** Every `check_*.py` and `regress.py` exits 1 on a failure; `check_scout.py`
      uses a temp stop folder (Q18); new `run_checks.py` runs them all.
      Verify: `python run_checks.py` exits 0 with `~/.fetcher/STOP.cli` still in place
- [x] **Private details out** of this file and `regress.py`: city, bot handle, chat name, PIN date
- [x] **No private addresses** (new §5 limit 7). `fetcher.private_host()`; refused in `act.check_goto`
      (so `browse` and `fetch_page`), in `scout.fetch_pages`, and again after a redirect lands.
      Verify: `check_act.py`, `check_surfaces.py`, `check_scout.py`: localhost, LAN, `127.1`, `[::1]` blocked
- [x] **Page text can't close its own fence** (`scout.py` `_score`). Verify: `check_scout.py`
- [x] **API limits** (`api_server.py`): body over 1 MB → 413, bad length → 400, only the last 50 jobs kept.
      Verify: `check_api.py`
- [x] **Portable.** `register_claude.py` finds the Claude config instead of a hardcoded folder;
      `tabpool.free_ram_mb` works off Windows. Verify: `check_tabpool.py`
- [x] **Fresh-eyes review** by a separate agent: secrets, private details, README install steps
- [x] **Yours:** public, at your call 2026-11-10. Pushed to GitHub as `the-scout`

**Done when:** `python run_checks.py` exits 0, the review finds no secret or private detail, and the
README alone is enough to install and run it.

---

## 7. Open questions

1. **Target list.** I'll draft it in Phase 1. You'll need to add your own must-have sites.
2. **Telegram bot token.** You create the bot with @BotFather (2 min) when we reach Phase 6.
   I won't handle the token in chat; it goes in a local `.env` file.
3. **Local model choice.** Decided in Phase 5 by testing 2–3 models on the same questions.
   ✅ **For now: `qwen/qwen3.5-9b`** (LM Studio, 2026-10-21) — the only installed model that fits
   12 GB of VRAM; `qwen3.6-35b-a3b` (15.9 GB) would spill into scarce RAM. It answers in ~1 min
   but scores pages leniently and mixes up facts, so Claude stays the default and `/local` is
   for offline/private questions. Comparing others needs downloads (~6–9 GB each) — ask first.
4. **Which Chrome profile for logged-in sessions (Phase 4).** Chrome locks a profile while
   it's open, so the agent can't share your everyday one live. Options: (a) a dedicated
   "Scout" profile you log into once per site; (b) a copy of your profile refreshed on
   demand; (c) close Chrome before runs. Leaning (a). Decide before Phase 4.
   ✅ **Decided 2026-10-21: (a).** `~/.fetcher/scout-profile`, kept separate from fetcher's
   anonymous crawl profile; you log in with `python act.py login <url>`.
5. **This PC's IP is in Bangladesh (ipinfo, checked 2026-10-21).** Best Buy redirects
   to its country picker, and Craigslist, Redfin and Zillow refused every visit. Some of that
   is probably geography, not bot detection (not proven). US rental and retail data may need a
   US exit, which clashes with "no paid proxies" (§1). Accept the gap, or use free sources?
6. ✅ **Fixed 2026-10-21 (§8).** **The fetcher reports "ok" when it got no real content.** rtings returns 200 with only
   ~1,200 chars of navigation at `Tier.HTTP` (the review sits in a script blob), Best Buy's
   country picker passes as ok, and docs.n8n.io is flagged as a challenge/CAPTCHA because a chat
   widget's JSON contains "just a moment". Fix in `classify()` before Phase 5 trusts results?
7. **x.com client-hint theory not confirmed (§8).** Phase 7's first item ("fix UA/client-hint
   coherence, the likely x.com cause") may not be needed. Drop it or keep it as insurance?
   **Answered 2026-10-21: dropped.**
8. ✅ **Fixed 2026-10-21 (§8).** **Bug: replaying an HTTP-tier cookie into the browser can break it.** YouTube: Patchright
   `add_cookies` failed with "Invalid cookie fields" after `SessionStore` held a cookie from
   the HTTP arm (seen in the per-tier run; the normal ladder forgets cookies before escalating,
   so `probe.py` didn't hit it). Fix in `fetcher.py`, then run `check.py` + `regress.py`.
9. **Web search needs the browser from this connection.** Over plain HTTP, DuckDuckGo starts
   challenging (HTTP 202 "bots use DuckDuckGo too") after a few dozen queries, Bing answers with
   unrelated pages, and Mojeek shows a JS challenge even in the browser. Through `Tier.BROWSER`
   both DDG and Bing answer, but Bing still mixes in junk (travel guides, Bangladesh tourism),
   which the relevance filter drops. Every search now costs a browser load. Accept, or look
   for another free engine? (Brave 429, Ecosia Cloudflare, Yahoo/Qwant/Startpage gave no
   parseable results on 2026-10-21.)
   **Answered 2026-10-23: (a) Brave Search API** (your key in `.env`), first web engine; the browser fallback only runs when Brave fails.
   **Updated 2026-10-26:** free tier only and optional; see §2.
10. **SEC wants a contact email in the User-Agent.** Without one, the ticker list 403s; the
    endpoints `search.py` uses answer anyway. Put an address in `API_UA`, or leave it?
11. **Free RAM, not the code, limits parallel tabs.** Other apps leave 0.8–2 GB of the 15.5 GB
    free, and 8 tabs need ~3 GB: the 8-tab test pushed free memory to 18 MB (swapping). The pool
    now sizes itself to free RAM, so today runs get 1–3 tabs. Phase 5's local model (Ollama)
    also wants RAM. Options: close other apps during runs, add RAM, or accept 2–3 tabs.
    **Answered 2026-10-23: (c) accept it.** Runs use 2-3 tabs on low-RAM days.
12. **Answers lean on small review sites.** The first good headphones run cited an owner's blog
    (scored 3) and comparison sites like allreviews.news / headsetatlas.com ahead of RTINGS, whose
    facts didn't make it into the text, and Wirecutter wasn't read. Options: prefer known review
    authorities when picking the next batch, or tell the scorer to rank hands-on testers above
    roundups. Not a blocker; worth doing before Phase 6 answers go to Telegram?
    **Answered 2026-10-23: both (c), built; live check pending.**
13. **Who sets the act allowlist?** Today the AI picks `allowed_sites` on each `browse` call, so
    a malicious page could talk it into widening its own scope.
    **Answered 2026-10-26: keep it AI-chosen.** Known risk, accepted.
14. **"Rent / reserve / subscribe / join" block the Zillow "For Rent" filter.**
    **Answered 2026-10-26:** browsing and filtering are allowed. Still blocked: "Rent now – $X",
    "Apply", tour or application forms that ask for an SSN or a fee, and any click on a page with
    payment fields. The card, IBAN and password checks don't change.
15. **STOP is shared by both surfaces and jams browse/act until a research run clears it.**
    **Answered 2026-10-26:** separate stop per surface, plus `/stopall`, and a new request clears
    its surface's stop (see §5).
16. **Bare "Apply" is blocked (Q14), and some sites use "Apply" to close a filter popover.**
    "Apply filters" / "Apply 3 filters" are allowed; a button that only says "Apply" is refused
    as a rental application step, even inside a price or beds filter. If that stops a Zillow
    filter in the live check, options: (a) allow bare "Apply" inside a listbox / filter dialog,
    (b) keep it blocked and pick the filter another way (the URL or the dropdown's own options).

17. **n8n and Claude chat can't browse at the same time.** Both use your logged-in Scout profile,
    and Chrome lets only one process open it. If chat is browsing, an n8n `/browse` returns
    `ERROR: …` (profile in use) until chat's browser closes. Research isn't affected (each surface
    has its own research profile). Options if it gets in the way: (a) live with it, (b) a second
    Scout profile for n8n that you log into separately.
18. **A stray `~/.fetcher/STOP.cli` (2026-10-27) makes `check_scout.py` fail.** It's a stop flag from
    a `stop_all` during the Phase 8 live checks. Harmless for real use (a new CLI request clears it),
    but `check_scout.py` uses the real stop folder. With an empty stop folder it passes. Fix: delete
    the file, and/or make `check_scout.py` use a temp `act.STOP_DIR` like the other checks.
    ✅ **Fixed 2026-11-10 (Phase 10):** `check_scout.py` now uses a temp stop folder.

---

## 8. Progress log

| Date | What happened |
|---|---|
| 2026-10-21 | Plan created. Decisions locked (§2). Next: Phase 1 cleanup + target list. |
| 2026-10-21 | Plan review fixes: split "tier" into stealth levels L0–L4 vs `fetcher.Tier` (§2a); added key-files map (§4a); added hard limits on page instructions, site allowlist, and send-message approval (§5); expired-session rule; "Not doing" list; defined "relevant", page scoring, and stop-rule defaults; Chrome profile is open question 4. Next: still Phase 1. |
| 2026-10-21 | Send-message approval removed at your call; sending stays fully autonomous (§2). Page-instruction and site-allowlist limits kept. |
| 2026-10-21 | **Phase 1 done.** Superseded headers on `SUMMARY.md`, `CHAT_SUMMARY.md`, `p2_report.txt`; ~8-tab note on `AUDIT.md`. Target list (19 sites, utility row skipped) approved and put in `probe.py` (rtings URL corrected from its sitemap after the drafted one 404'd). Runs: `probe.py` ×3 (real ladder; the last two at 07:04 and 07:15 BST), one per-tier pass (each URL at `Tier.HTTP` and `Tier.BROWSER` separately), one deep-link pass, one 50-page rtings harvest, one x.com client-hint test. Exit IP: Bangladesh. Table below. Next: Phase 2. |
| 2026-10-21 | **Fixed open questions 6 and 8 in `fetcher.py`** (tests written first and watched fail). `classify()`: challenge/CAPTCHA markers only count below 20k chars of visible text (`CHALLENGE_TEXT_CEILING`; real Zillow/Indeed interstitials are ≤8.6k); a page with <1,500 chars of text inside >150KB of markup is now `soft_block`, so the ladder escalates to the browser (`THIN_PAGE_TEXT`/`THIN_PAGE_BYTES`); new `BlockType.GEO_BLOCK` for country pickers, which doesn't escalate. New `browser_cookies()` sends `__Secure-`/`__Host-` cookies with the flags Chrome requires. 5 new cases in `regress.py`, 3 in `check.py`; both pass. Live recheck: docs.n8n.io `ok` at both tiers; rtings `soft_block` at HTTP → `ok` at BROWSER (24–39k chars), and the ladder escalates by itself; Best Buy `geo_block` with no browser launch; YouTube browser `ok` with `__Secure-YNID` stored (was "Invalid cookie fields"); x.com and anthropic.com unchanged (`ok`). Next: Phase 2. |
| 2026-10-21 | **Phase 2 built; waiting on your relevance marks.** Questions approved. `search.py`: DDG / Bing / Mojeek / Wikipedia + HN, Reddit, GitHub, arXiv, OpenAlex, EDGAR; feeds, sitemaps, JSON-LD; Wayback fallback; health skip after 3 failures; Google results pages never fetched. Found live and fixed: Bing's normal page tripped the CAPTCHA marker (engines now judge by parsed results); Bing over HTTP returns unrelated pages and DDG challenges HTTP after heavy use, so engines retry once via `Tier.BROWSER`; Bing in the browser still mixes in junk, so each link must share words with the query (numbers and `site:` parts ignored, 2 words needed for 3+-word queries); arXiv abs/pdf/html copies de-duplicated. Mojeek fails even in the browser; Reddit JSON 403s (reached via `site:reddit.com` instead). `check_search.py` 26/26, `check.py` + `regress.py` pass. Final run (`tests/run_questions.py`, hand-written query plans standing in for Phase 5's planner): every question returned ≥10 links, from DDG + Bing (browser) + the fitting APIs/feeds. Note: most `check_search.py` tests were written after the code; only the per-link filter, relevance and arXiv-html tests were watched failing first. Next: you mark `tests/results-2026-10-21.md`. |
| 2026-10-21 | **First-pass relevance marks (mine, from titles/URLs/dates; pages not opened), waiting on your corrections:** 60/100 links `y`; 9/10 questions meet ≥5. Q4 (budget keyboard) fails at 4/10: 5 of its links are generic SEO best-of sites and no Reddit thread surfaced. Weak sources seen: the GitHub search API (unrelated repos for Q6), HN (off-topic hits in Q1/Q7), Wikipedia (loosely related pages in Q8). EDGAR's top filings for NVIDIA are insider Form 4s, not 10-Q/10-K. |
| 2026-10-21 | **Phase 2 done.** You confirmed the marks. Fixed Q4: `demote_listicles()` moves "N Best … (Month YYYY)" SEO lists to the end of every result list (test watched failing first), and Q4's Reddit query lost its past-year filter (the good r/MechanicalKeyboards threads are older). Q4 rerun (`tests/results-2026-10-21-q4.md`): **6/10 `y`, was 4/10**; 3 Reddit threads now in its top 10. Final hit rate **62/100, 10/10 questions ≥5 relevant**, no paid service. `check_search.py` 27/27, `check.py` + `regress.py` pass. Other questions weren't rerun with the listicle change. Next: Phase 3 (set effort to **medium**). |
| 2026-10-21 | **Phase 3 done.** `tabpool.py`: N tabs in one Chrome (one persistent context), shared queue, shared per-site `AsyncPacer`, fetcher's `CircuitBreaker`, `Telemetry` and `SessionStore`. `check_tabpool.py` 14/14 (written first, watched failing). **Live, 8 different sites:** 8 tabs in parallel took **5.6 s**; one tab, one after another, took 31.6 s, with the slowest single page 4.7 s → parallel ≈ slowest page ✓. 7/8 OK in parallel; apartments.com returned a 59-char blank page in parallel but was OK one-at-a-time. **RAM:** our Chrome 1 tab 701 MB · 2 tabs 1,012 · 4 tabs 1,477 · 8 tabs 2,968 MB (working set). Free RAM before the runs was 1.4–2 GB; the 8-tab run drove it to 18 MB, so 12/16-tab runs were skipped to keep your PC usable. Default N is now picked from free RAM (`tabs_for`: 800 MB reserve, max 8). Next: Phase 4 (set effort to **xhigh**; open question 4, the Chrome profile, needs your answer first). |
| 2026-10-21 | **Phase 4 done.** Profile: you chose (a), a dedicated Scout profile. `act.py`: `Session(allowed_sites=…)` with `goto` / `snapshot` / `act` / `read`; snapshots from Patchright's AI aria snapshot, compacted (GitHub results page 32,883 → 9,512 chars, 122 refs) and labelled as page data. Hard limits in `check()`, raising `Blocked` before the page is touched: allowlist, CAPTCHA frames/buttons, password and 2FA fields, login-form submits, sign-up buttons, card numbers (Luhn) / IBANs (mod-97) in any field, card/bank fields, pay/order/checkout buttons and forms, delete/trash/remove (except "remove filter"). Every goto/snapshot/action, blocked ones too, goes to `~/.fetcher/actions.jsonl` (URL where it happened + where it led). `check_act.py` 53/53, written first; 4 deliberate guard breaks all caught. **Live on GitHub, refs only:** typed "browser agent" → submit → clicked the "Python" language filter → opened `browser-use/web-ui` → read 9,210 chars ✓. Live blocks: "Sign up for GitHub" → `account`; a click on Wikipedia with only github.com allowed → `allowlist`. Fixed during the run: log rows recorded the post-click URL; repeated label text cluttered snapshots. Not yet tested: a real logged-in site (needs you to log in once). Next: Phase 5 (effort **xhigh**). |
| 2026-10-21 | **Independent review of the hard limits** (one subagent, xhigh, no access to my reasoning; read-only). 21 findings: 5 high, 10 medium, 6 low. I turned each into a test first; **all 32 new tests failed on the old code, so every finding was real**. High: payment/delete matched only a short English word list (other languages, lookalike Cyrillic letters, invisible characters, fullwidth letters all got through); Enter in a field checked the field, not the button it presses; elements inside a cross-site iframe weren't checked against the allowlist; logging in without typing (autofilled password, "Continue with Google") and OAuth "Authorize" pages weren't blocked. Medium: more sign-up / card-field / password-field / CAPTCHA wording; card numbers inside longer text or split across fields; the check used the snapshot name, not the element's current label; link targets and form/button actions weren't read; checkout URLs weren't context; `goto` accepted `file:` / `javascript:` and URLs that act when opened. Low: blocked card numbers and passwords were written to the log in plain text; some failures weren't logged; select could pick an option by its hidden value; bad allowlist entries accepted; page text could pose as a ref line; `/stop` not rechecked before acting. **Fixes in `act.py`:** text normalised (NFKC, invisible characters stripped, lookalike letters folded) and checked across name, live label, text, attributes, the default submit button, the chosen option and the surrounding dialog; multilingual word lists + currency amounts; page, frame, link and form URLs checked for checkout / delete / sign-up / login / CAPTCHA; frames must be on the allowlist; any click on a page with a password field, any sign-in/authorise button or login/OAuth URL is blocked; split card numbers caught across fields; `check_goto` (http(s) only, no action URLs); `check_sites` validates the allowlist; secrets redacted in the log; every attempt logged with frame URL. `check_act.py` **89/89** (was 53); 7 deliberate breaks of the new rules all caught; all other test files pass; the live GitHub search → filter → open → read demo still works and both live blocks still fire. Trade-off, by design: more false blocks (e.g. any link whose text says "join" or "delete"); the brain can `goto` a link's URL instead, which is reading. |
| 2026-10-21 | **Phase 5 built, live run blocked on two logins.** `scout.py`: plan (sub-questions + searches, unknown sources dropped, falls back to the question as typed) → discovery via `search.py` → batches of ≤8 pages, ≤2 per site, blocked sites skipped after one block → HTTP first, tab pool for pages that need a browser (search's browser is closed first: Chrome locks the profile) → brain scores pages 0–3 with page text fenced as data; a page that tries to give instructions is dropped from sources and named → enough / 40 pages / 5 min / last-5-added-nothing / no-more-leads → answer with [n] citations, numbered sources, "Couldn't reach", search notes; `--report` writes `reports/`. `check_scout.py` 29/29 (written first; a deliberate-break run found 2 untested rules, tests added; 2 rules are double-guarded so breaking one layer can't show). **Brains:** `ClaudeBrain` runs `claude -p --tools "" --strict-mcp-config --no-session-persistence` (Sonnet by default, for speed) in an empty folder with a clean environment; `LocalBrain` uses the OpenAI-style API (LM Studio found already installed with `qwen/qwen3.5-9b`, so no Ollama install). **Live attempt (local brain):** model loaded on the GPU (6.1 GB, 16k context, 18 s); the run finished in 31 s but every brain call got **401: LM Studio requires an API token**, so it fell back and reported "couldn't find sources good enough". `claude -p` from a clean environment says **"Not logged in"**: the standalone CLI has never been signed in. Both need you once; I won't handle either credential. Model unloaded afterwards. Next: your two logins → rerun the headphones question on both brains. |
| 2026-10-21 | **Phase 5 done (Claude brain).** You logged the CLI in (`auth status`: logged in, claude.ai; ~4 s per brain call from a clean environment). Local model deferred at your call. **Run 1** (71 s): 8 pages read, "0 useful" — the report showed page 1 scored with facts and pages 2–6 blank. Re-scoring the same 6 pages in isolation gave RTINGS 3, Cybernews 2, etc., so the reply was fine to read but my parser took only the first JSON chunk it could decode and silently scored every missing page 0; the stall rule then stopped after one batch. Raw reply not saved, so the exact shape is inferred. **Fixed (tests first):** `parse_items` collects every `{"id"…}` item across blocks / wrappers / past a broken item; pages the reply skips are asked about once more; any still missing stay **unscored (-1)**, are named in the answer and don't count toward the stall rule; every brain reply is kept in the report. Titles now decode HTML entities. `check_scout.py` 38/38; all six test files pass. **Run 2 (done when ✓): sourced answer in 77 s wall / 64 s in-run, unattended** — 4 sub-questions, 4 searches, 8 pages read, 7 scored (one 3, five 2s, one 1), stopped on "enough sources"; picks Sony WH-1000XM5, Sennheiser Momentum 4, Bose QC Ultra (on sale), budget Soundcore Space One / CMF Headphone Pro, with [n] citations, conflicting prices flagged, and "Couldn't reach: memeburn.com (challenge)". Quality caveat: sources lean on small review sites (open question 12). Report: `reports/2026-10-21-best-noise-cancelling-headphones-under-300-with-re.md`. Next: Phase 6 (effort **medium**). |
| 2026-10-21 | **Local brain run.** You added the LM Studio token to `.env` (read by `LocalBrain`; I didn't open the file). Loaded `qwen/qwen3.5-9b` (16k context): RAM free fell from 5.1 GB to 0.86 GB while loaded (LM Studio also auto-loaded a 0.6 GB embedding model). One call ~2.4 s. **Headphones question: 62 s wall / 57 s in-run, unattended**, 3 searches, 12 pages read, 10 scored 2+, stopped on "enough sources". It replied with ids as strings (`"3"`), which the new parser handles. **Versus Claude (77 s):** Claude gave specific picks with reasons (XM5 / Momentum 4 / QC Ultra on sale / budget options) and flagged conflicting prices; the 9B gave a vaguer, budget-leaning answer, scored almost every page 2 (incl. an SEO site), and its extracted facts contradict each other (Sony WH-CH720N "50-hour battery" from one page vs "35 hours" from another; it also claims LDAC on the WH-CH720N, which I believe is wrong — not checked against the page). Decision for open question 3: keep Claude as the default, `/local` = qwen3.5-9b for offline/private use. Models unloaded and LM Studio's server stopped afterwards (RAM back to 7.2 GB free). Phase 6 note: `/local` must load the model itself (`lms load …`) and unload it after, given the RAM cost. Report: `reports/2026-10-21-best-noise-cancelling-headphones-under-300-with-re-2.md`. |
| 2026-10-21 | **Phase 6 built; waiting on your setup steps.** Installed `mcp` 2.3.0 (PyPI; v2 renames FastMCP → `MCPServer`). `scout.run_research()` is the one entry point for both surfaces: clears an old /stop, loads/unloads the LM Studio model around `/local` runs (`lmstudio_loaded`), and `Scout` now stops after the current batch when /stop is raised. **`mcp_server.py`**: 6 tools; research / fetch / browse+act each on their own thread (Playwright's sync API is thread-bound); blocked actions return "BLOCKED by hard limit […]" text; page text labelled as data. **`telegram_bot.py`**: stdlib Bot API (long polling, sendMessage, sendDocument); answers only your user ID in a private chat (everything else: no reply, logged to `~/.fetcher/telegram.log`); one run at a time ("still working on…" otherwise); `/stop` works mid-run; pings throttled to one per 20 s; answers split under 4,096 chars; `whoami` prints your ID locally; `install-startup` / `remove-startup`. `check_surfaces.py` 20/20, written first; 6 deliberate breaks (anyone allowed, group chats allowed, no busy lock, /stop ignored by bot or by scout, blocked act crashing the chat) all caught; all 7 test files pass. **Live over stdio, as Claude chat would call it:** `research("What did n8n ship in the last 7 days?")` → **60 s**, 16 pages, sourced release-by-release answer (2.43.7 → 2.45.1, brand refresh) with 3 progress messages; `browse` GitHub search → `act` click on the Python filter worked; `act` on "Sign up for GitHub" → BLOCKED [account]. Not yet run: Telegram (no bot token yet) and the Claude app (not registered). Next: your four steps, then the done-when check (same question in Claude chat and Telegram). |
| 2026-10-21 | **Done-when check, part 1.** Startup entry installed (your OK): `%APPDATA%\…\Startup\the-scout-telegram.cmd` runs `pythonw telegram_bot.py`. Token fixed (it had been pasted with an extra piece); the bot is started via the startup entry. **Telegram ✓**: "What did Anthropic announce this week?" → pings + sourced answer in ~1 min (Public Benefit Fund, no new model, investor-day report). Fixed: `whoami` now marks your message read (the bot had researched your leftover "hi"); `**` stripped from Telegram answers (shown literally in plain text). **Claude chat ✗**: the app said it had no the-scout tool. Its config lives at `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json` and didn't list it. With your OK: backed up (`…backup-20261021-215220.json`), added `the-scout` beside your three servers (unchanged), and checked that the same command starts from `C:\` with all 6 tools. Next: you restart the Claude app and ask in a new chat. |
| 2026-10-21 | **Claude chat still failed: the app overwrote the edit.** On quit it writes its in-memory config back (21:59:03), dropping `the-scout`; that's likely why your own attempt didn't stick either. MCP logs for all servers stop on 22 Aug, so they're no help. Fix: `register_claude.py` waits (detached, max 30 min) until the desktop app has fully quit, backs up the config, adds only `the-scout`, and logs to `~/.fetcher/register_claude.log`. Started at 22:00. Next: you quit Claude fully, wait ~10 s, reopen, ask in a new chat. |
| 2026-10-21 | **Registered; research timed out in the app.** The detached waiter was killed with the app, so you ran `register_claude.py` yourself with Claude closed: added at 22:14:32 (backup `…-221432.json`), and it survived the restart — the app now lists the 6 tools. Calling `research` through the app gave **"Request timed out"**: the app gives up on a tool call well before a 1–3 min run, and progress messages don't extend it. Fix (tests first): `research` waits at most 40 s and otherwise returns "STILL RUNNING … call research_status"; new tool `research_status` returns progress or the finished answer (job kept as a thread future). `check_surfaces.py` passes; all 7 test files pass. Next: restart Claude once more (config untouched), ask in a new chat. |
| 2026-10-21 | **Profile collision fixed (code), live check pending.** The Claude-chat answer was weak because the MCP server and the Telegram bot shared `~/.fetcher/chrome-profile`; Chrome locks a profile, so the second browser failed with "Opening in existing browser session" and search lost its browser fallback. `fetcher.use_profile()` now sets each process's profile: `mcp_server.py` uses `chrome-profile-mcp`, `telegram_bot.py` uses `chrome-profile-telegram`; `PatchrightTransport` (search) and `TabPool` both read it, and proxy profiles are per surface too. 4 new tests in `check_surfaces.py`, watched failing first; all 7 suites pass. Bot restarted (it wasn't running). Next: restart the Claude app, re-ask in Claude chat, then close Phase 6. |
| 2026-10-21 | **Phase 6 done.** You restarted Claude and re-asked the Anthropic question. The MCP server created `~/.fetcher/chrome-profile-mcp` (22:42), and the run left DuckDuckGo at 0 fails (its browser search worked) and Bing failing only on poisoned results after Chrome loaded the page, so no profile-lock error this time. The answer text wasn't fact-checked. Next: Phase 7. |
| 2026-10-21 | **Phase 7 done (decisions, no code).** You dropped the x.com UA/client-hint fix (open question 7 answered: Phase 1 showed no 403s, the limit is login). Per-tab fingerprints are deferred until a site blocks parallel tabs, to keep 8 tabs on your free RAM. L4 stays a stub. All phases ✅. |
| 2026-10-21 | **Logged-in act tested on Facebook.** You logged in with `act.py login`; the Scout opened Messenger signed in, found a Facebook Messenger chat via Messenger search (the list row itself has no clickable ref) and sent your message; the page showed "Message sent", and it's in `actions.jsonl`. Bug found and fixed (test first, in `check_act.py`): a failed Chrome launch (profile locked by the login window) left the driver running, so every later `browse` in the MCP server failed until restart. Reaches Claude chat after the next app restart. |
| 2026-10-23 | **Logged-in browsing verified through Claude chat.** After the app restart, a new chat (outside projects; the first try was in a project without the connector) used `browse` + `act` to open a Facebook Messenger chat and read the last 3 messages; nothing sent. Minor: the thread still showed "Loading messages" when snapshotted, so older messages weren't loaded. |
| 2026-10-23 | **Open question 12 fixed (code), live check pending.** You chose (c), both. `scout.trusted()`: a list of hands-on testers, major news, reference and primary-source sites, plus .gov/.edu and the official site of anything the question names (anthropic.com for an Anthropic question); `_next_batch()` reads trusted pages first, keeping search order within each group. Scorer prompt: a roundup or "N best" list that only restates others scores at most 1 unless it's the only page with a needed fact. 7 new tests in `check_scout.py`, watched failing first; all 7 suites pass. Live run skipped to save your usage limit: re-ask the headphones question after the reset and compare with the 2026-10-21 report. |
| 2026-10-23 | **Open question 9 fixed: Brave Search API.** You signed up and put `BRAVE_API_KEY` in `.env` (read by `search._brave_key()`, sent only in the request header). `search.brave()` is the first web engine and is added to any search that names a web engine (the planner says ddg/bing); when it answers, refused engines skip the browser fallback, so most searches need no Chrome load. Planner prompt lists brave. 8 new tests in `check_search.py`, watched failing first; all 7 suites pass. Live: one Brave search returned CNET and a Reddit thread over HTTP. A full research run is still to do. |
| 2026-10-23 | **Verification run (Brave + source ranking).** Telegram bot restarted on the new code. Re-ran the headphones question (`reports/2026-10-23-best-noise-cancelling-headphones-under-300-with-re.md`). Better sources: the first batch was all trusted (Wirecutter, TechRadar, RTINGS ×3, CNET, The Verge, PCMag, SoundGuys), and the answer's sources [1]-[3] are Wirecutter, TechRadar and RTINGS, plus Tom's Guide and The Verge; on 2026-10-21 the only trusted page read was one RTINGS page, behind allreviews.news, headsetatlas.com and flovonovo.com. Still weak: recordingnow.com (a small blog) scored 3 twice and is cited 5 times, and the run read all 40 pages (172 s vs 64 s) because 'enough sources' never triggered with 4 sub-questions. No web-engine browser loads needed. |
| 2026-10-23 | **Stop rule: trusted sources cover the question.** At your call. New `Scout._trusted_cover()`: after 2 batches, if ≥3 trusted pages scored ≥2 and trusted pages cover all but one sub-question, stop and name the gap (the gap may have weak small-site coverage; live run 2 kept going because one Yahoo deals page touched it). 4 new tests in `check_scout.py`, each watched failing first; all 7 suites pass. Live: run 2 (first version) read 40 pages, 126 s; run 3 stopped at 16 pages, 71 s (`…-re-3.md`), all 9 cited sources trusted (RTINGS ×3, Wirecutter, TechRadar, The Verge, What Hi-Fi, Tom's Guide, SoundGuys), gap = user reliability reports (needs Reddit). Trade-off: the answer is thinner, says 'partial', gives fewer prices, and lists deals and reliability under Not covered. |
| 2026-10-23 | **Trusted-cover stop now waits 3 batches (24 pages), at your call, for more prices.** Tests updated first and watched failing; `check_scout.py` passes. Live run 4 (`…-re-4.md`): stopped by trusted cover at 32 pages (the cover wasn't met at 24 this time), 105 s. The answer has more prices (Momentum 4 ~$290, Space One ~$100, XM6 $425-458, QC Ultra $449, one A$ sale) and 6 products. Sources: 7 of 11 trusted; small ones crept back in (recordingnow.com, cybernews, a YouTube video). |
| 2026-10-23 | **Open question 11 answered: accept low RAM (c).** Snapshot: 3.0 GB free of 15.5; the Claude app 2.1 GB, Brave 1.6, llama-server + LM Studio 2.4 (a model still loaded outside `/local`), WSL 1.2. No code change. |
| 2026-10-26 | **Comparison with browsentic, and four decisions.** Read-only code reviews of the Scout and browsentic produced a gap scorecard with ranked fixes (top: STOP jam + dead-Chrome recovery, rent-word false blocks, silent 30k truncation, `fetcher._LADDER` profile lock, fixed sleeps). Decided: Brave free tier only and optional (§2 exception); allowlist stays AI-chosen (Q13, risk accepted); rent words blocked only at pay/apply steps (Q14); stop per surface + `/stopall` (§5, Q15). Q9/Q11 answers moved under their questions. No code change yet. |
| 2026-10-26 | **Phase 8 planned: top 5 reliability fixes** (§6). Stop per surface + dead-Chrome recovery, rent words only at pay/apply, paged snapshots instead of silent truncation, `fetch_page` releasing the research profile, real waits instead of fixed sleeps. Adds 3 MCP tools (`stop_all`, `read_page`, `wait_for`; 10 total). No code change yet. |
| 2026-10-26 | **Phase 8.1 done: stop per surface + dead-Chrome recovery.** `act.stop(surface)` / `stop_all()` / `clear_stop(surface)` / `stop_requested(surface)`; files `~/.fetcher/STOP.mcp`, `STOP.telegram`, `STOP.cli` (stop_all writes all three instead of a `STOP.all`, so each surface's next request clears only its own). `Session`, `Scout` and `run_research` take `surface`; `browse`/`research` clear chat's stop, a Telegram question clears Telegram's. New MCP tool `stop_all`, Telegram `/stopall`. `mcp_server._guarded` turns Playwright errors into `ERROR: …` text; "target/browser closed" drops the session and returns `ERROR BROWSER_CLOSED`. Tests written first and watched failing: 6 in `check_act.py`, 11 in `check_surfaces.py`; all suites pass. The old `~/.fetcher/STOP` file is no longer read. |
| 2026-10-26 | **Phase 8.2 done: rent words only at the pay/apply step (Q14).** `act._PAY` / `_ACCOUNT` lose bare `rent`/`reserve`/`subscribe`/`join`; pay-shaped phrases stay blocked ("rent now", "pay rent", "reserve now/and pay", "subscribe now/for", "join now/free", any currency amount). New rule `application`: "Apply", "Apply now", "Start/Submit application" (but "Apply filters" is fine), typing into an SSN field, clicking in a form whose fields ask for an SSN or an application/screening fee. New: any click on a page with card/bank fields or a hosted card iframe (Stripe, Braintree, Adyen, PayPal…) is blocked; `_TARGET_JS` now reports `form_fields` and `page_fields`. Luhn, IBAN, card-field and password checks unchanged. Old tests "Reserve", "Rent", "Join" became "Reserve now", "Rent now", "Join now"; "Join GitHub" is still blocked through its `/join` link. 25 new cases in `check_act.py`, watched failing first; all pass. New open question 16 (bare "Apply" in filter popovers). |
| 2026-10-26 | **Phase 8.3 done: no silent truncation.** `mcp_server._paged()` cuts long text at line breaks into parts that fit `MAX_TEXT` (30k), each with the page-data header and URL, ending `TRUNCATED: part 1 of N; call snapshot(part=2)` (last part says it's the end). Part 1 stores a SHA-1 of the text; a later part of different text returns `STALE: page changed since part 1`. `snapshot(part=1)` pages; new MCP tool `read_page(part=1)` exposes `Session.read()` the same way. `fetch_page` now says when it cut a page. 15 new tests in `check_surfaces.py` (70k-char fake page = 3 parts, all 700 refs reachable, STALE on change), watched failing first; all pass. |
| 2026-10-26 | **Phase 8.4 done: `fetch_page` lets go of the research profile.** `fetcher._LADDER` became `_LADDERS`, one per `allow_browser` value (before, an HTTP-only first call would have served later browser fetches too), plus `fetcher.close_ladder()`. `mcp_server.do_fetch` calls it in `finally` when the read used the browser tier (or failed), so `chrome-profile-mcp` is free for `research`'s tab pool. 3 new tests in `check.py`, 4 in `check_surfaces.py` (incl. `fetch_page` now saying when it cut a page), watched failing first; `check.py`, `regress.py`, `check_surfaces.py` pass. Live check (fetch_page on a browser site, then research, one chat) is yours. |
| 2026-10-26 | **Phase 8.5 done: real waits instead of fixed sleeps.** New `fetcher.settle(page)` / `settle_async(page)`: DOMContentLoaded → network idle (3 s cap, timeout ignored) → DOM quiet for 400 ms via a MutationObserver in Patchright's isolated world (5 s cap). Used by `act.Session._settle` (was a fixed 1.5 s), `tabpool._load` and `PatchrightTransport.fetch` (were 3 s). A page that still reads as a challenge after settling gets the old 3 s beat and is read again, so Cloudflare interstitials aren't misread. New `Session.wait_for(text, timeout, gone)` (polls the text every 250 ms, max 30 s, logged) and MCP tool `wait_for(text, timeout=10, gone=False)`; a timeout returns `TIMEOUT:` plus the page as it is. Tests first, watched failing: 4 in `check.py`, 2 in `check_tabpool.py`, 9 in `check_act.py`, 3 in `check_surfaces.py`; all 7 suites pass. Smoke test in a real headless Chrome (temp profile, local files, no network): a page that changes for 1.2 s settles in 1.6 s; `wait_for` sees text go and come in ~1.75 s; a missing text times out cleanly. `regress.py` time unchanged (0.29 s; it classifies saved fixtures, no browser), so the speed-up shows only in live runs. |
| 2026-10-26 | **Phase 8 code complete (items 1-6).** §4a lists every module plus the new fetcher names; Phase 6 lists the 10 MCP tools and `/stopall`. All 6 offline `check_*.py` and `regress.py` pass. Left for you (needs the Scout profile / Claude app, so not run here): restart the Claude app (and the Telegram bot) to load the new code, then one Claude-chat session: `stop` → new `browse`; Zillow "For Rent" (see Q16 about a bare "Apply"); a long page read with `snapshot(part=2)` / `read_page`; `fetch_page` on a browser-tier site then `research`; a Messenger thread with `wait_for("Loading messages", gone=True)`; Telegram `/stop` while chat browses. |
| 2026-10-27 | **Phase 8 live checks (Claude Code session + your side chat).** ✅ `stop` → `act` refused (`STOPPED`) → new `browse` works. ✅ Dead Chrome: killed the Scout's Chrome, next call returned `ERROR BROWSER_CLOSED`, `browse` reopened it. ✅ Profile lock: `fetch_page` on community.n8n.io used the browser tier (telemetry tier 2), no `chrome-profile-mcp` Chrome left open, then `research` (n8n latest release) answered in 39 s with no lock error. ✅ Rent words: Zillow "Rent" and apartments.com "For Rent" clickable (your side chat); apartments.com "Apply Now" → `BLOCKED [application]`; "Request Tour" opens the tour picker (no SSN/fee asked; stopped there). ✅ Paged reading: Wikipedia WWII in 7 parts (your side chat). Not yet run: `wait_for` on Messenger, Telegram `/stop` while chat browses. Also: Tool permissions in the Claude app had 7 Scout tools on Block (fixed by you). Noticed: `fetch_page` text keeps HTML entities (`&#x27;`, `&nbsp;`, `&amp;`); a pre-Phase-8 `~/.fetcher/STOP` file is left over (harmless, no longer read). |
| 2026-10-27 | **HTML entities decoded in page text; old STOP file removed.** `fetcher._extract`'s tag-strip fallback (used when trafilatura isn't installed or finds nothing) now unescapes entities after stripping tags, so `fetch_page` and research pages show `'`, `&` and spaces instead of `&#x27;`, `&amp;`, `&nbsp;`. An escaped `&lt;b&gt;` stays as text. New test in `check_scout.py` failed first, then passed. All 6 `check_*.py` and `regress.py` pass. Deleted the unused pre-Phase-8 `~/.fetcher/STOP` (it only held a timestamp; no code reads it). Restart the Claude app to load the fix. |
| 2026-10-28 | **Phase 9 (n8n integration) added to the plan.** §2 "How you start it" now includes n8n workflows (locked decision changed with your approval). §1/§3: n8n is the only Docker exception; the Scout and Chrome stay on Windows. §4 diagram: n8n over HTTP + token on `127.0.0.1:8787`. §5: loopback-only, `SCOUT_API_TOKEN` in `.env`, outside text goes in `context` as data (known risk accepted). Design drafted in a side session; saved 2026-10-29 after diff review. Phase 8's last 2 live checks (`wait_for` on Messenger, Telegram `/stop` while chat browses) still come first. |
| 2026-10-29 | **Phase 8 last 2 live checks.** ✅ Telegram `/stop` while Claude chat ran `research`: Telegram stopped, chat research finished. ⚠️ Messenger: a "Restore your chats" PIN dialog (E2EE backup) blocked the thread. The Scout did not enter the PIN and did not pick "Don't restore messages" (both correct under §5), opened the thread by URL, and `read_page` returned the last 3 messages. `wait_for("Loading messages", gone=True)` timed out after 30 s because the dialog kept that text on screen: timeout path verified, success path not yet. To close: you enter the PIN once in the Scout's Chrome, then re-run the check. |
| 2026-10-29 | **Phase 8 done.** After you entered the Messenger PIN once in the Scout's Chrome, `wait_for("Loading messages", gone=True)` returned without a timeout and `read_page` gave the last 3 messages. All Phase 8 live checks pass. ▶ moves to Phase 9 (n8n). |
| 2026-10-29 | **Phase 9 code complete.** `check_api.py` written first (failed: no module), then `api_server.py`: stdlib `ThreadingHTTPServer` on `127.0.0.1:8787`, Bearer token from `.env` (constant-time compare, 401 otherwise, never logged), research as one-at-a-time jobs (202 + `job_id`, `wait` up to 300 s, 409 busy), `context` fenced as data (closing marker stripped), `notify: telegram`, `make-token`, `install-startup` / `remove-startup`. Reuses `mcp_server`'s `do_*` by setting its surface to `n8n` (own process, own state). `act.SURFACES` gains `n8n`; chat `stop_all` and Telegram `/stopall` texts mention n8n. Sample workflow `n8n/scout-morning-digest.json`. `check_api.py` 37/37; `check.py`, `check_act.py`, `check_search.py`, `check_surfaces.py`, `check_tabpool.py`, `regress.py` pass; `check_scout.py` passes with an empty stop folder (Q18). Live smoke: `make-token` wrote `SCOUT_API_TOKEN` to `.env` (value never shown); from inside the `n8n` container, `GET http://host.docker.internal:8787/health` gave 200 with the token and 401 without, both logged in `~/.fetcher/api.log`. Server left running. Left: your live check (credential, import, run). |
| 2026-11-10 | **Readiness audit, then Phase 10 (publish prep) built.** Audit: a solid personal tool, not safe to push as it was (no `.gitignore` over `.env`, private details in this file, no README / licence / dependency list, checks never exited non-zero, no private-address guard). Done: `.gitignore`, `.env.example`, pinned `requirements.txt`, MIT `LICENSE`, `README.md`, `run_checks.py`; every check exits 1 on a failure and `check_scout.py` uses a temp stop folder (Q18). New §5 limit 7: `fetcher.private_host()` refuses localhost / LAN / names that resolve there, before a page opens and after a redirect lands (`act.check_goto`, `act.check`, `Session.snapshot` / `read`, `mcp_server.do_fetch`, `scout.fetch_pages`); live: `localtest.me` (→ 127.0.0.1) refused, wikipedia / rtings / x.com fine. Page text can no longer close its `<page>` fence. API: body over 1 MB → 413, bad length → 400, last 50 jobs kept. `register_claude.py` finds the Claude config; `tabpool.free_ram_mb` works off Windows. Removed from this file: city, bot handle, chat name, PIN date. 9 new tests written first and watched failing. `python run_checks.py`: 8 of 8. **Fresh-eyes review** (a separate agent, read-only) then found, and I fixed test-first: two ways round limit 7 (a `\` in the URL, which Chrome and Python read differently, so such URLs are now refused; and 127.0.0.1 written inside IPv6), a bad `wait` that still started a job, no read timeout on the API (now 30 s), an off-by-one in job pruning, one chat name still in this file, and settings missing from the README. Checks again 8 of 8. You chose public: banner and pipeline graphics added (`assets/`), README restyled, pushed to GitHub as `the-scout`. Phase 9's live check is still yours. |

**Phase 1 results (2026-10-21).** "Lowest Tier" = cheapest `fetcher.Tier` that returned real content. Level per §2a: L0 = API/feed, L1 = no browser needed at home IP, L2 = needs the Patchright browser, L3 = needs a logged-in profile. "—" = nothing built so far works.

| # | Target | Lowest Tier | Level | What breaks |
|---|---|---|---|---|
| 1 | anthropic.com/news (+ /news/announcements) | `HTTP` | L1 | Nothing. ok every visit |
| 2 | docs.claude.com release notes | `HTTP` | L1 | Nothing |
| 3 | github.com n8n releases (+ a release tag page) | `HTTP` | L1 | Nothing |
| 4 | docs.n8n.io/release-notes | `HTTP` (content is there: ~273k chars) | L1 | Classifier false positive: "just a moment" in a chat widget's JSON → `challenge` at HTTP and `captcha` in browser; the circuit breaker then opened on the 4th visit (open question 6) |
| 5 | community.n8n.io announcements | `BROWSER` | L2 | HTTP gets a 465-char shell (JS-rendered forum). Browser ok on all 4 visits |
| 6 | HN Algolia API | `HTTP` | L0 | Nothing |
| 7 | rtings best-list + 50 review pages (harvest) | `BROWSER` for content | L2 | Harvest: 50/50 `ok` at HTTP, 148 s at 3 s pacing, **no blocks at all**. But HTTP yields only ~1,200 chars of nav per page; the browser gets 27–39k chars of review. Silent degradation (open question 6) |
| 8 | Wirecutter (nytimes) best headphones (+ a deep page) | `BROWSER` | L2 | DataDome. HTTP ok 2 of 4 times, `transport_error` the other 2. Browser ok every time |
| 9 | reddit r/headphones search | — | — | HTTP `transport_error`, browser `captcha`; breaker opened after |
| 10 | amazon.com search (+ a product page) | `HTTP` | L1 | ok at HTTP every visit. The browser got a 503 once |
| 11 | bestbuy.com search | — | geo | Both tiers land on "Best Buy International: Select your Country" (760 chars) and it's wrongly scored `ok`. Bangladesh IP (open question 5) |
| 12 | zillow.com Austin rentals | — | — | PerimeterX `challenge` at both tiers on all 3 visits; breaker opened |
| 13 | apartments.com Austin 2-bed (+ a listing page) | `BROWSER` cold, then `HTTP` | L2 → L1 with cookies | Akamai 403 at HTTP on a cold visit. After one browser visit, the saved session cookies let HTTP through on the next 2 runs. The listing page was HTTP 403 / browser ok cold |
| 14 | redfin.com Austin rentals | — | — | AWS WAF 405 "Human Verification" at both tiers, 3/3 |
| 15 | austin.craigslist.org apartments | — | — | 403 "blocked" at both tiers, 3/3. Likely IP or geography, not proven |
| 16 | x.com/AnthropicAI | `HTTP` for a single post; timeline: L3 | L1 / L3 | Profile is 200 at every tier, but 0 posts rendered logged out (header only). A single status page is fully readable at HTTP |
| 17 | linkedin.com company posts | — | L3 | HTTP `login_wall` 4/4; the browser got status 999 (scored `captcha`) |
| 18 | reddit r/ClaudeAI/new.json | — | — | 403 at both tiers; breaker opened. The Reddit JSON API listed for Phase 2 does not work from here |
| 19 | youtube.com Anthropic videos (+ a watch page) | `BROWSER` | L2 | HTTP gets a 175-char shell. The cookie-replay bug hit here in the per-tier run (open question 8) |

**x.com client-hint re-test (AUDIT §7b), 2 rounds × 5 setups:** curl_cffi coherent; curl_cffi with a contradicting `Sec-CH-UA`; bundled Chromium with a Chrome/141 user-agent (sent with `Sec-CH-UA: "Chromium";v="151"`, the P2 mismatch); bundled Chromium with no override; real Chrome. **All 10 got 200 and the same profile header; none got a 403.** The mismatch theory is not confirmed: x.com no longer refuses that shape. None of the 10 rendered any posts, so the real limit for X timelines is login (L3), not the fingerprint.

**What it means:** every target that works at all works at `Tier.BROWSER` or below. The rest fail on vendor WAFs (PerimeterX, AWS WAF), login (LinkedIn, X timeline), or probably geography (Best Buy, Craigslist). Building `Tier.CDP`/`Tier.ENGINE` isn't justified by this list. The bigger risk is the fetcher reporting `ok` on empty or wrong pages (open question 6). Raw outputs: session notes folder `phase1-raw/`.
