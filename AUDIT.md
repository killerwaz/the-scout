> **Note (2026-10-21):** the plan now runs **~8 parallel tabs**, not 20–25 (16 GB RAM; see `PLAN.md` §3). Read every "20–25 / 25-way parallel" figure below with that in mind. `PLAN.md` is the working plan; this file stays the evidence base.

# Audit — Stealth + Autonomous Research: necessary?

**Date:** 2026-08-30 · **Audits:** `SUMMARY.md` (2026-08-30)
**Scope:** personal self-hosted tool. Not a product. Legal/moat criteria dropped.
**Status:** verification complete · P2 run on Windows (§7a) · P2b (repeat-load, deep pages,
fan-out, x.com re-test) not yet run · P3/P4 not yet run
**Revision:** 2026-08-30 (later same day) — verdict corrected after P2's scope was
challenged. P2 tested one request per site, on homepages, from a fresh IP. That's real
evidence for what it covers, and it doesn't cover the shape of load The Scout will actually
put on these sites. See §7b.

---

## Verdict

**Build stealth as a ladder. Climb only the rungs your sites actually force you onto —
don't build the top rung, and don't skip the ladder either.**

`SUMMARY.md` §5's coupling question is empirically closed: the behavioral-coupling argument
for *fusing* stealth into the orchestrator is refuted (§2). That is different from "stealth
is unnecessary," which is not what the evidence shows — see §7b for why the first version of
this verdict overreached.

The whitespace in §5 is real but **commercial, not technical**. Six vendors already ship
stealth + autonomous research in one package; every one gates stealth behind a paid tier.
Meanwhile the discovery layer the whole design rests on failed verification — that risk is
unchanged by anything below and is still the bigger threat to the project.

---

## 1. The gap is a price tag, not an invention

§5 claims stealth + autonomous research "does not exist," and explains it ethically.
Both halves are wrong.

**Six shipped counterexamples (Aug 2026):** browser-use Cloud (own maintained Chromium
fork behind the agent loop), Notte, Skyvern Cloud, Hyperbrowser (stealth flags documented
as applying to Agent Tasks), Anchor Browser, Bright Data.

**Zero ethical refusals found** across browser-use, Skyvern, Firecrawl, GPT Researcher,
Notte, Steel. The one project that explains itself gives a commercial reason:

> "All of the core logic powering Skyvern is available in this open source repository
> licensed under the AGPL-3.0 License, **with the exception of anti-bot measures available
> in our managed cloud offering**." — Skyvern-AI/skyvern README

Steel implements the identical boundary: `solveCaptcha: false` is a hardcoded literal,
field exposed in the schema, no implementation behind it.

**What this changes:** the genuinely unoccupied position is *self-hostable + zero recurring
cost + stealth + autonomous*. That gap is real and yours to occupy — but it is licensing
arbitrage, not invention. Don't budget it as solving an unsolved problem.

---

## 2. The coupling argument loses

The strongest case for bundling — only the orchestrator knows intent, so only it can shape
timing/dwell/navigation-order into something human-looking — does not survive contact with
the 2026 literature. (I proposed this steelman myself before running the audit. It lost.)

**FP-Agent** (arXiv 2605.01247), 7 agents incl. Browser Use and Skyvern:

| Feature set | F₁ |
|---|---|
| Browser fingerprint only | 0.797 |
| Behavioral only | **0.9993** |
| Combined | 1.0 |

Mechanism: agents "do not exhibit any mouse movements and instead directly teleport to"
click targets. Cloudflare case study: Cloudflare blocked 1 of 7 agents; FP-Agent caught 7 of 7.

**arXiv 2607.26935** narrows it to two features — `mouse_event_rate` + `teleport_click_ratio`
— holding **100% agent recall at every evasion level**, including Level-5 where the agent
replays real recorded human cursor trajectories. Why replay doesn't help:

> "the discriminative signal is an **API execution artifact rather than an agent cognition
> signal**… Playwright does not emit the raw pointer-move and wheel-delta streams that a
> physical input device produces."

The leak is at the automation-API boundary — the *separable runtime layer*. An agent with
perfect self-knowledge cannot emit pointer streams it has no API to emit. This argues **for**
composition.

**The one coupling requirement that survives:** stealth state must persist across the whole
multi-step trajectory — same IP, same profile, same fingerprint across a 25-way fan-out.
Composition can do this; naive composition botches it. Design for it. It is a session-
continuity requirement, not a reason to fuse layers.

---

## 3. The real risk is discovery

### `/goto` resolved as Case B — the expensive one

> "the goto links cannot be decoded locally, which means providers have to follow the
> redirect" — Derek Perkins, Nozzle

**500–1,000 requests per five-page ranking.** HEAD refused, so every link needs its own GET.
No local-decode path exists. Google is finished as a free discovery source at volume.
Q1 in §9 is answered — against you.

### The SearXNG backbone is a treadmill

Google engine breakage: Oct 2025 → Feb 2026 → Mar 2026 (HTTP 403 on all requests) →
Apr 2026 (CAPTCHA). Each fixed; each fix needs an upstream patch **and** a user-side upgrade.
DuckDuckGo CAPTCHA bug #4824 open **15 months**.

> "the ongoing problems with the major web search providers raise doubts as to whether we
> will be able to maintain this strategy in the future." — return42 (maintainer), searxng
> discussion #5651

On a residential IP, Aug 2026: Google zero parseable results, Brave suspended on rate limit,
Startpage behind CAPTCHA. A viable config exists — drop the majors, enable the long tail
(Crossref, OpenAlex, Yandex, Baidu, Seznam, Yahoo) — but that shifts result *character*
toward academic/regional. It is not Google-equivalent general web coverage. No upstream
`/goto` response exists yet; assume a break is pending.

### The load-bearing correction

**Every** entry on local-deep-research's benchmark leaderboard — including the ~95% SimpleQA
figure cited as justification for porting it — used **Serper, a paid Google SERP API**.
Zero SearXNG runs. "Fully local" refers to the LLM, not search. The plan pairs a benchmark
with an architecture that has never produced it.

### By your own numbers, stealth was never the bottleneck

§4.4 sizes it: consent walls and JS rendering *large*; login walls, 429s, soft paywalls
*medium*; **WAF/CAPTCHA *small*** — and §7 then puts WAF/CAPTCHA out of scope. Everything in
the majority is answered by a real browser, a consent clicker, a profile vault and backoff.
The plan allocated its second-largest build block to its smallest bucket.

---

## 4. Claims ledger

Tiers: T1 reproduced · T2 independent benchmark · T3 vendor/project docs (never sufficient
alone) · T4 community failure reports.

| # | Claim | Verdict | Tier |
|---|---|---|---|
| 1 | agent-browser 41.5k stars, Rust/CDP, Apache-2.0, covers R4/R5/R6/R13 | **CONFIRMED** — 41.1k at check; refs, a11y snapshots, AES-256-GCM sessions, auth vault, HAR, MCP all present | T1·T3 |
| 2 | agent-browser stealth only via paid cloud providers | **CONFIRMED** — AgentCore/Browserbase/Browserless/Browser Use/Kernel + local Chrome, Lightpanda. README never mentions stealth | T3 |
| 3 | Camoufox year-long maintenance gap | **UNDERSTATED** — 17.5mo PyPI gap (0.4.11 Jan 2025 → 0.5.3 Jul 2026). Also: daijro stepped down Jan 2026, dev moved to a company fork, canonical repo is now a checkpoint mirror, rebranding to "AI agent browser" | T1·T3 |
| 4 | Camoufox "0% headless detection" / "~42s Turnstile" | **STRUCK** — not Camoufox's claims. Both trace to one paid-scraping-vendor marketing page, no methodology, purchase CTA. Community tracking issue lists DataDome, PerimeterX, Google, hCaptcha, CF-in-Docker as detecting | T3-vendor, contradicted T4 |
| 5 | Camoufox cannot emulate Chromium fingerprints | **CONFIRMED** — structural; V8-specific JS behavior is unspoofable from Firefox | T3 |
| 6 | LDR ~95% SimpleQA on Qwen3.6-27B / 3090 | **CORRECTED** — model is real. But 95.7% is n=300 (6.9% of SimpleQA); n=500 scores *lower* at 94.8%; maintainer-submitted; LLM-graded with conceded error rate; **all runs used paid Serper** | T1 |
| 7 | LDR explicitly refuses stealth | **CONFIRMED** as stated intent (README self-assertion, not verified behavior). Only such statement found anywhere | T3 |
| 8 | Steel Local single-session; stealth + CAPTCHA cloud-only | **HALF WRONG** — single-session verified in source (`activeSession` is one object not a map; `endSession()` takes no ID) but it's per *container*, so fleet = one container per session. **Stealth is NOT cloud-only**: fingerprint gen/injection + proxy chaining ship in the Apache-2.0 repo. Only CAPTCHA withheld. Steel's own arch doc claims "concurrent session management" — contradicted by its code | T1 |
| 9 | `/goto` Case A vs B undetermined | **RESOLVED AS CASE B** — opaque, 500–1000 req per 5-page ranking, HEAD refused | T2 |
| 10 | "Stealth + autonomous research does not exist" | **FALSE** — six counterexamples | T3×6 |
| 11 | "...because research agents refuse stealth ethically" | **FALSE** — zero ethical refusals; gating is commercial and Skyvern says so | T3-primary |
| 12 | SearXNG self-hosted: "free, no key" — 15 min | **NOT SUPPORTABLE AS WRITTEN** — majors break every 1–3 months; unusable on residential IP today; maintainer publicly doubts sustainability. Free to run, not free to operate | T1·T2 |
| 13 | §4.4 failure-mode shares (large/medium/small) | **PARTIALLY MEASURED, see §7a/§7b** — P2 confirms the intuition on single, homepage-only requests (11/12 needed no stealth). Unmeasured: repeated/scheduled load, deep pages, the `harvest` job profile — the shape most likely to trip WAF/CAPTCHA | P1, scope-limited |
| 14 | Parallelism vs per-domain limits; two job profiles | **HOLDS** — reasoning sound. RAM figures behind 20–25 contexts still unmeasured (P4) | reasoning |

---

## 5. If you build stealth, build on Patchright — not Camoufox

| Tool | License | Last release | Maintenance | Verdict (solo maintainer) |
|---|---|---|---|---|
| **Patchright** | Apache-2.0 | 2026-08-29 | Active | **Safe — lowest risk here** |
| Scrapling | BSD-3 | 2026-08-23 | Active | Healthiest project surveyed |
| nodriver | **AGPL-3.0** | 2026-05-13 | Stale ~3.5mo, bus factor 1 | Best evasion, worst license |
| Camoufox | MPL-2.0 | 2026-08-18 | Revived after 17.5mo | Caution — handoff + fork split |
| rebrowser-patches | Apache-2.0 | 2025-05-09 | Dormant ~15mo | **Do not adopt** |

Two things settle it: Patchright is **Chromium**, which is the entire point of R2 (Camoufox
structurally cannot match your native Windows Chrome fingerprint); and **Scrapling already
migrated its own stealth path off Camoufox onto Patchright** and now carries no Camoufox
dependency. Any description of Scrapling as a Camoufox wrapper is out of date.

*Benchmark caveat:* the only non-marketing cross-tool test is one independent run (7 tools ×
31 targets × 3 sweeps) whose author states the limits himself — "one residential IP, one
operating system, thirty-one targets, one night." Cloudflare-centric, no Akamai, mismatched
browser versions across tools, and Camoufox tested on the stalled Firefox 135 build. nodriver
topped it (28 OK / 3 gated / 0 blocked); Patchright and Camoufox tied mid-pack. Signal, not ranking.

---

## 6. Revised build order

The 5-day estimate is roughly right. The **sequence** is wrong — it front-loads the layer with
the least evidence and treats the layer that just failed verification as a 15-minute footnote.

| # | Step | Effort | Why here |
|---|---|---|---|
| 1 | **Run P2** (`p2_content_yield.py`) | 30 min | Only measurement that says whether stealth is load-bearing on *your* corpus. Everything below branches on it |
| 2 | **Solve discovery** | ~2 days | The actual risk. Long-tail engine mix + open-data router. Decide honestly whether a capped paid API is the fallback |
| 3 | **Adopt agent-browser** | 0 days | Verified as claimed. Kills old Phases 0–2. Confirm the provider interface extends without a fork (Q2 — a source-read, not a guess) |
| 4 | **Build orchestrator** | ~2 days | Plan → frontier → fan out → score → decide, with budget/marginal-information termination. Borrow LDR's architecture, not its benchmark |
| 5 | **Port recovery ladder** | ~1 day | Highest-yield rungs (open-data path, archive fallback) handle the *large* buckets in §4.4 |
| 6 | **Stealth tiers 0–3, not tier 4** | ~½ day | See §7b. Tier 2 (Patchright swap) is ~30 min, not a custom provider. Tier 3 (real Chrome profile / R4) is what actually unlocks LinkedIn, Meta, X — build it, don't skip it. Tier 4 (behavioral simulation, proxy rotation) stays stubbed until evidence demands it |

---

## 7. Falsifiable threshold — what P2 decides

- **Both automated arms ≥90% of real Chrome** → automation isn't being punished *on the
  requests P2 tested*. Read this as clearance for tiers 0–1, not as clearance for the whole
  stealth question — see §7b for what P2 didn't test.
- **Patchright beats vanilla by ≥15 points** → hardening is doing real work; the thin provider
  is justified. Note this is a *runtime* win — it argues for composition, not bundling.
- **Both collapse below 60% while real Chrome succeeds** → the block is IP/TLS-layer or
  real-profile cookies. **No stealth library fixes that.** Drive your real Chrome profile, or
  change egress.

## 7a. P2 results (run on Windows, 2026-08-30)

Median content yield vs real Chrome: **vanilla Playwright 100% · Patchright 100%.**
11 of 12 Scout-topic targets returned equal-or-better word count under plain automation —
no browser hardening needed.

| target | plain | pw | patch | chrome | pw% | patch% |
|---|--:|--:|--:|--:|--:|--:|
| n8n-community | 43 | 1993 | 1993 | 1993 | 100% | 100% |
| n8n-blog | 397 | 1757 | 1757 | 1757 | 100% | 100% |
| hn | 717 | 717 | 717 | 717 | 100% | 100% |
| github | 1733 | 1991 | 1991 | 1991 | 100% | 100% |
| claude-docs | 791 | 792 | 792 | 792 | 100% | 100% |
| anthropic-eng | 516 | 518 | 518 | 518 | 100% | 100% |
| reddit | ERR | 1142 | 1136 | 1202 | 95% | 95% |
| linkedin | ERR | 978 | 978 | 978 | 100% | 100% |
| medium | 470 | 529 | 480 | 480 | 110% | 100% |
| latent-space | 106 | 773 | 773 | 773 | 100% | 100% |
| **x-twitter** | 391 | **0** | **0** | 408 | **0%** | **0%** |
| simonwillison | 5995 | 5995 | 5995 | 5995 | 100% | 100% |

**The only outright failure is x.com — a hard 403 on both automated arms in under 4 seconds
(before any JS ran), 200 on real Chrome.** Everything flagged as `consent` or `captcha` in
`p2_results.json` (n8n-community, n8n-blog, claude-docs, reddit, medium) appeared
**identically across all four arms, real Chrome included** — confirmed false positives (a
page referencing the word "captcha" in a script tag, or a normal cookie banner), not blocks.
Content yield on those targets was 95–110%.

This licenses tiers 0–1 of the ladder (§7b): a real browser on a residential IP clears 11 of
12 targets with no hardening. It does **not** license skipping tiers 2–3, and it does not
license "stealth is unnecessary" — see §7b for why.

---

## 7b. Why "descope stealth entirely" overreached — read before treating §7 as settled

The verdict above this line was the audit's first pass, written the moment P2 came back
clean. It's wrong in scope, not in its data. What P2 actually established: **a real Chromium,
driven once, from a clean unused residential IP, against twelve index/homepages, matches
hand-driven Chrome.** What got claimed from that: "stealth isn't load-bearing for this
project." That's a much bigger claim than the test supports, for five concrete reasons.

**1. n=1 per target, and bot defense is mostly rate/reputation-based.** Nothing fires on
request #1 from a clean IP. It fires on request #400 that hour, or day 40 of a daily crawl.
The Scout is scheduled and unattended — P2 tested the single politest request it will ever make.

**2. P2 tested homepages, not the pages The Scout will actually crawl.** Index pages are the
most SEO-critical, most bot-friendly surface a site has — sites *want* Googlebot there.
Article pages, site search, pagination past page 2, and query-parameter URLs are where WAF
rules tighten. P2 tested the shop window.

**3. `SUMMARY.md` §4.2 defines two job profiles; P2 tested one.** `research` (30 domains × 1
page) is the easy shape — per-domain limits never fire, confirmed by both the original
reasoning and P2. `harvest` (1 domain × 500 pages) is the shape that gets accounts and IPs
banned, and P2 never touched it.

**4. Zero session accumulation, day-zero IP reputation.** Fresh browser context every fetch,
no cookie continuity, no test of the authenticated paths R4's profile vault exists for. The
home IP tested is presumably unflagged — the best-case starting point on a curve that only
degrades with use.

**5. The corpus tested undersells the corpus that matters.** `SUMMARY.md`'s stated targets
include Cloudflare-protected news portals, LinkedIn, and Meta. P2 tested ~zero hard-Cloudflare
news portals, LinkedIn's public index page (not a profile or post), and no Meta at all. The
100% column describes sites that were never going to fight back.

**The x.com result likely isn't "one weird site" — it's the one site that checked.** Both
automated arms 403'd in under 4 seconds, before JS executed, ruling out behavioral or
JS-runtime detection. The probable cause is a fingerprint *inconsistency* in the P2 harness
itself: the script set a Chrome `User-Agent`, but bundled Chromium's automatic `Sec-CH-UA`
client hints say "Chromium" — two contradictory identities in one request. Real Chrome (the
`chrome` arm) has no such contradiction and passed. If confirmed, x.com isn't an outlier;
it's the only target in the twelve that checked internal consistency, and it caught the
mismatch instantly. That reframes the finding from "1/12 sites need stealth" to "the one site
that actually looked, found a problem stealth tooling exists to fix." Needs a controlled
re-test (P2b), not assumed.

**Corrected read of the evidence:** P2 clears tiers 0–1 of the ladder below. It says nothing
about tiers 2–4, which is exactly the part of the original plan that mattered most for your
named hard targets. "Descope entirely" is retracted in favor of the tiered build in §6.

### The stealth ladder

| Tier | What | Build now? | Why |
|---|---|---|---|
| 0 | Feeds, sitemaps, JSON-LD, open-data APIs | Yes | cheapest, highest yield |
| 1 | Real Chromium + residential IP | Yes | P2 confirms this clears 11/12 targets |
| 2 | Patchright as default executor | Yes — ~30 min | drop-in import swap; insurance against the client-hint leak that likely bit x.com |
| 3 | Persistent real-Chrome profile + cookie vault (R4) | Yes | this is what actually unlocks X, LinkedIn, Meta — your named hard targets |
| 4 | Behavioral simulation, proxy rotation, archive substitution | No — stub | build only if P2b's instrumentation says a target you need demands it |

Detection has four layers, and only one has a hard ceiling worth respecting:

1. **Network** (IP reputation, TLS/JA3-JA4 fingerprint) — free win, covered by tier 1.
2. **Runtime/JS** (`navigator.webdriver`, CDP leaks, client-hint coherence) — tier 2's territory, probably where x.com caught the harness.
3. **Behavioral** (mouse event rate, teleport clicks) — per FP-Agent and arXiv 2607.26935, detectors hit ~100% recall here even against replayed real human cursor traces, because the signal is an automation-API execution artifact, not a cognition signal. **No browser-patching tool beats this layer.** This is why tier 4 stays stubbed — buying evasion here doesn't work; the fix, if ever needed, isn't a library.
4. **Reputation/graph** (cookie age, session continuity, account standing) — tier 3's territory.

Two things the original audit missed that argue *for* building capability, not against it:
**fingerprint diversity across the 20–25 parallel contexts** (identical fingerprints across a
fan-out are correlatable into one actor — P2 tested one context at a time and couldn't see
this), and **silent degradation under R14** (an unattended system's dangerous failure mode
isn't a loud 403, it's returning 11/12 sources indefinitely while reporting success — stealth
capability here is instrumentation insurance, not evasion for its own sake).

**Next test (P2b, not yet run):** repeat-load over time on the same targets, deep pages
instead of homepages, the `harvest` job profile, 25-way parallel fan-out, and a controlled
re-test of the x.com client-hint hypothesis with a corrected UA/client-hint match. Blocked on
The Scout's actual source registry to know what to test against.

---

## Sources

- vercel-labs/agent-browser · daijro/camoufox · Kaliiiiiiiiii-Vinyzu/patchright · D4Vinci/Scrapling · steel-dev/steel-browser · Skyvern-AI/skyvern · LearningCircuit/local-deep-research
- LDR benchmark leaderboard: huggingface.co/datasets/local-deep-research/ldr-benchmarks
- searxng issues #5958, #5867, #5286, #4824 · discussion #5651
- Search Engine Land, Google confirms /goto redirects (2026-08-26)
- ppc.land, "Google forces rank trackers into 500 to 1,000 requests per query"
- FP-Agent, arXiv 2605.01247 · Minimal behavioral feature sets, arXiv 2607.26935
- Paterson, "Anti-detect browser benchmark 2026" (7 tools, 31 targets, 651 verdicts)
