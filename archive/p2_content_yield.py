#!/usr/bin/env python3
"""
P2 - CONTENT YIELD DIFF  (run this on YOUR Windows box, not in the cloud)
=========================================================================
Answers the only question that decides whether the stealth layer is worth
building: on YOUR corpus, from YOUR residential IP, how much content do you
lose by NOT having stealth?

The interesting failure is not a 403. It is a 200 that quietly returns less.
So this measures extracted text, word count, link count and JSON-LD presence
per arm, and diffs them against your real Chrome as the ceiling.

ARMS
  1 plain    - plain HTTP, no browser            (floor)
  2 pw       - vanilla Playwright Chromium       (automation, no hardening)
  3 patch    - Patchright Chromium               (hardened: Runtime.enable leak fixed)
  4 chrome   - your real installed Chrome        (ceiling: real TLS + real fingerprint)

SETUP  (PowerShell, ~2 min)
  pip install playwright patchright
  python -m playwright install chromium
  python -m patchright install chromium

RUN
  python p2_content_yield.py            # all four arms
  python p2_content_yield.py chrome     # a single arm

OUTPUT
  p2_results.json   raw records
  p2_report.txt     the yield table + the verdict line

READ THE VERDICT LINE. If arms 2/3 recover >=90% of arm 4's words on most
targets, stealth is buying you almost nothing and the build should be
descoped. If they collapse, stealth is load-bearing and the ranking of
arm 2 vs arm 3 tells you whether hardening or the IP is doing the work.
"""
import json, re, sys, time, os

# --- targets: The Scout's actual topic set, spread across the 8 failure modes ---
URLS = [
    ("n8n-community", "https://community.n8n.io/",                          "forum / Discourse"),
    ("n8n-blog",      "https://blog.n8n.io/",                               "vendor blog"),
    ("hn",            "https://news.ycombinator.com/",                      "open control"),
    ("github",        "https://github.com/browser-use/browser-use",         "open control"),
    ("claude-docs",   "https://docs.claude.com/en/docs/about-claude/models", "docs"),
    ("anthropic-eng", "https://www.anthropic.com/engineering",              "CF-fronted corp"),
    ("reddit",        "https://www.reddit.com/r/ClaudeAI/",                 "bot-hostile"),
    ("linkedin",      "https://www.linkedin.com/pulse/",                    "login wall"),
    ("medium",        "https://medium.com/tag/ai-automation",               "soft paywall"),
    ("latent-space",  "https://www.latent.space/",                          "substack"),
    ("x-twitter",     "https://x.com/AnthropicAI",                          "hard login wall"),
    ("simonwillison", "https://simonwillison.net/",                         "open control"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

TAG_RE   = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
STRIP_RE = re.compile(r"<[^>]+>")
WS_RE    = re.compile(r"\s+")


def textify(html):
    if not html:
        return ""
    h = TAG_RE.sub(" ", html)
    h = STRIP_RE.sub(" ", h)
    return WS_RE.sub(" ", h).strip()


def signals(html):
    text = textify(html)
    low = (html or "").lower()
    return {
        "words":  len(text.split()),
        "chars":  len(text),
        "links":  low.count("<a "),
        "jsonld": low.count("application/ld+json"),
        "cf_challenge": any(s in low for s in [
            "just a moment", "cf-browser-verification", "cf_chl",
            "challenges.cloudflare.com", "enable javascript and cookies to continue"]),
        "login_wall": any(s in low for s in [
            "sign in to continue", "log in to continue", "join linkedin",
            "you must log in to continue", "sign up to continue"]),
        "captcha": any(s in low for s in ["recaptcha", "hcaptcha", "datadome", "px-captcha"]),
        "consent": any(s in low for s in [
            "accept all cookies", "cookie consent", "we use cookies", "manage preferences"]),
    }


def arm_plain():
    import urllib.request
    out = {}
    for key, url, _ in URLS:
        rec, t0 = {}, time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read().decode("utf-8", "replace")
                rec["status"] = r.status
                rec.update(signals(body))
        except Exception as e:
            rec["status"] = None
            rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        rec["ms"] = int((time.time() - t0) * 1000)
        out[key] = rec
        print(f"  plain  {key:15s} {str(rec.get('status')):5s} {rec.get('words','-')}w "
              f"{rec.get('error','')}", flush=True)
    return out


def arm_browser(mod_name, label, channel=None):
    out = {}
    try:
        mod = __import__(f"{mod_name}.sync_api", fromlist=["sync_playwright"])
        sync_playwright = mod.sync_playwright
    except Exception as e:
        print(f"  !! {label}: import failed ({e}) - pip install {mod_name}", flush=True)
        return {k: {"status": None, "error": "import failed"} for k, _, _ in URLS}

    with sync_playwright() as p:
        kw = {"headless": True}
        if channel:
            kw["channel"] = channel
        try:
            browser = p.chromium.launch(**kw)
        except Exception as e:
            print(f"  !! {label}: launch failed ({str(e)[:160]})", flush=True)
            return {k: {"status": None, "error": "launch failed"} for k, _, _ in URLS}

        ctx = browser.new_context(
            user_agent=UA, locale="en-US", timezone_id="UTC",
            viewport={"width": 1440, "height": 900},
        )
        for key, url, _ in URLS:
            rec, t0 = {}, time.time()
            page = ctx.new_page()
            try:
                resp = page.goto(url, timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                html = page.content()
                rec["status"] = resp.status if resp else None
                rec["final_url"] = page.url
                rec.update(signals(html))
            except Exception as e:
                rec["status"] = None
                rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            rec["ms"] = int((time.time() - t0) * 1000)
            out[key] = rec
            print(f"  {label:6s} {key:15s} {str(rec.get('status')):5s} {rec.get('words','-')}w "
                  f"{rec.get('error','')}", flush=True)
            try:
                page.close()
            except Exception:
                pass
        browser.close()
    return out


ARMS = {
    "plain":  lambda: arm_plain(),
    "pw":     lambda: arm_browser("playwright", "pw"),
    "patch":  lambda: arm_browser("patchright", "patch"),
    "chrome": lambda: arm_browser("playwright", "chrome", channel="chrome"),
}


def report(results):
    lines = []
    W = lambda s="": lines.append(s)
    W("P2 CONTENT YIELD - words extracted per arm (higher is better)")
    W("=" * 78)
    W(f"{'target':16s}{'plain':>9s}{'pw':>9s}{'patch':>9s}{'chrome':>9s}   {'pw%':>5s}{'patch%':>7s}")
    W("-" * 78)

    ratios_pw, ratios_patch = [], []
    for key, _, _ in URLS:
        row = {a: results.get(a, {}).get(key, {}) for a in ARMS}
        vals = {a: row[a].get("words") for a in ARMS}
        base = vals.get("chrome") or 0

        def cell(a):
            v = vals.get(a)
            if v is None:
                r = row[a]
                return "BLOCK" if r.get("status") in (403, 429) else "ERR"
            return str(v)

        pct = lambda a: (f"{100*vals[a]/base:.0f}%" if base and vals.get(a) is not None else "-")
        if base and vals.get("pw") is not None:
            ratios_pw.append(vals["pw"] / base)
        if base and vals.get("patch") is not None:
            ratios_patch.append(vals["patch"] / base)

        W(f"{key:16s}{cell('plain'):>9s}{cell('pw'):>9s}{cell('patch'):>9s}"
          f"{cell('chrome'):>9s}   {pct('pw'):>5s}{pct('patch'):>7s}")

    W("-" * 78)
    med = lambda xs: (sorted(xs)[len(xs)//2] if xs else 0)
    m_pw, m_patch = med(ratios_pw), med(ratios_patch)
    W(f"median yield vs real Chrome:  vanilla Playwright {m_pw*100:.0f}%   Patchright {m_patch*100:.0f}%")
    W("")
    W("VERDICT")
    if m_patch >= 0.90 and m_pw >= 0.90:
        W("  Automation is not being punished on this corpus. Both automated arms recover")
        W("  >=90% of real Chrome's content. The stealth layer is NOT load-bearing here -")
        W("  descope it. Spend the days on the discovery layer and the recovery ladder.")
    elif m_patch - m_pw >= 0.15:
        W("  Hardening is doing real work: Patchright materially outperforms vanilla")
        W("  Playwright. A thin Patchright provider is justified. Note this is a RUNTIME")
        W("  win, which is exactly the separable layer - it argues for composition, not")
        W("  for bundling stealth into the orchestrator.")
    elif m_pw < 0.6 and m_patch < 0.6:
        W("  Both automated arms collapse while real Chrome succeeds, and hardening does")
        W("  not close the gap. That points at the IP/TLS layer or at real-profile cookies,")
        W("  NOT at browser patching. Buying a stealth library would not fix this.")
        W("  The fix is: drive your real Chrome profile, or change egress.")
    else:
        W("  Mixed. Read the per-target rows: the targets where pw/patch collapse are your")
        W("  actual stealth requirement, and they are probably few enough to name.")
    W("")
    W("Then check the flags in p2_results.json: cf_challenge / login_wall / consent /")
    W("captcha. A target that fails on 'consent' or 'login_wall' is NOT a stealth problem -")
    W("it is a click and a cookie jar. Do not let those inflate the stealth case.")
    return "\n".join(lines)


if __name__ == "__main__":
    which = sys.argv[1:] or list(ARMS)
    results = {}
    for a in which:
        if a not in ARMS:
            print(f"unknown arm {a}; choose from {list(ARMS)}")
            sys.exit(1)
        print(f"\nARM: {a}")
        results[a] = ARMS[a]()

    with open("p2_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)

    if len(which) == len(ARMS):
        txt = report(results)
        print("\n" + txt)
        with open("p2_report.txt", "w", encoding="utf-8") as f:
            f.write(txt)
        print("\nwrote p2_results.json + p2_report.txt")
    else:
        print("\nwrote p2_results.json (run all four arms for the yield table)")
