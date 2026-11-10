> **Superseded by `PLAN.md` (2026-10-21).** Kept for history; don't plan from this file.

# Chat Summary — Stealth + Autonomous Research Audit

**Date:** 2026-08-30 · **Subject:** whether the Headless Browser project needs a bundled stealth + autonomous research layer

## Where this ended up

**Build the stealth ladder, climb only the rungs your sites force you onto.** Not "skip stealth," not "build the full stack" — a tiered approach with instrumentation to tell you which tier each target needs.

| Tier | What | Build now? | Effort |
|---|---|---|---|
| 0 | RSS, sitemaps, open-data APIs | Yes | cheapest, highest yield |
| 1 | Real Chromium + your residential IP | Yes | free — this is what P2 tested |
| 2 | Patchright (fixes browser-internals leaks) | Yes | ~30 min, drop-in swap |
| 3 | Real Chrome profile + cookie vault (R4) | Yes | unlocks X / LinkedIn / Meta |
| 4 | Behavioral simulation, proxy rotation | No — stub only | build if evidence demands it |

## How we got here

1. **Verified `SUMMARY.md`'s claims.** Several broke: "stealth + autonomous research doesn't exist" is false (6 vendors ship it, gated by price not ethics); Camoufox's "0% detection" stat traced to an unrelated vendor's marketing page; LDR's 95% SimpleQA benchmark used paid Google search, not the free SearXNG stack the plan assumed; Google's new `/goto` redirect makes free search-result scraping expensive (500–1000 requests per page of results).
2. **Ran P2** — a script comparing plain Playwright vs. Patchright vs. real Chrome against 12 of The Scout's target sites. Result: automated browsers matched real Chrome on 11/12 sites. Only x.com failed (blocked on both automated arms, worked on real Chrome).
3. **Initially concluded: cut stealth entirely.** Overcorrected — the test only proved automation isn't punished on *easy, single-visit, homepage* requests from a *clean, unused* residential IP.
4. **You pushed back, correctly.** The test didn't cover: repeated/scheduled visits (reputation decays with use), deep pages vs. homepages, the `harvest` job profile (500 pages on one domain — the actual risk profile), 25 parallel identical browser fingerprints, or your named hard targets (LinkedIn, Meta, Cloudflare news portals).
5. **Revised verdict:** stealth isn't unnecessary, it's *unproven at the volumes and targets that matter*. Build it as an escalation ladder instead of a monolith.

## The x.com failure, re-diagnosed

Both automated browsers got a 403 in under 4 seconds — before any page content loaded. Likely cause: a fingerprint mismatch, not detection. The script's `User-Agent` claimed Chrome, but bundled Chromium's automatic client hints (`Sec-CH-UA`) said Chromium. X likely checked those disagree and blocked on that alone. This reframes it from "one odd site" to "the one site that checks internal consistency" — worth a controlled re-test.

## Discovery risk (unchanged, still the bigger threat)

- Google's `/goto` redirect (rolled out 2026-08-26) makes scraping Google search results expensive — no free local decode, ~500–1000 requests per 5-page ranking.
- SearXNG (the proposed free search backbone) has a recurring breakage pattern with Google's engine; the maintainer has publicly doubted its long-term viability.
- The LDR benchmark cited as justification (~95% SimpleQA) used paid Serper/Google search, not the free stack the plan assumes — no evidence yet that free search hits the same accuracy.

## Deliverables produced this session

- `AUDIT.md` — full verification + P2 findings, saved to your Headless Browser folder (superseded in spirit by the tiered-ladder correction above; content still accurate, verdict language needs the same softening applied here)
- `p2_content_yield.py` — the test harness, saved to the same folder
- `p2_results.json` / `p2_report.txt` — your actual run output
- Published artifact page with the original audit (pre-correction)

## Open items / next step

**P2b needed** — repeat-visit testing over time, deep pages instead of homepages, 25-way parallel fan-out (fingerprint diversity), and a clean re-test of the x.com client-hint mismatch theory. Blocked on: your Scout source registry (curated target list) to know what to actually test against.

**AUDIT.md in your folder still says "descope stealth entirely"** — that line is now superseded by the tiered-ladder conclusion above and should be corrected before you treat it as the final word.
