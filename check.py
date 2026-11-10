"""check.py - run this first. No browser needed, no network needed.
It proves fetcher.py works. Just run:  python check.py
"""
from fetcher import Result, Tier, BlockType, Budget, BudgetExhausted, Ladder, Transport, classify, browser_cookies

fails = []

# 1. Can it tell a real page from a fake success?
checks = [
    (200, "<html><body>" + "real article text. " * 60 + "</body></html>", BlockType.OK,        "a real page"),
    (200, "<title>Just a moment...</title>",                              BlockType.CHALLENGE, "a Cloudflare wall"),
    (403, "Sorry, you have been blocked",                                 BlockType.HARD_BLOCK,"a hard block"),
    (200, '<div class="g-recaptcha"></div>',                              BlockType.CAPTCHA,   "a captcha"),
    (200, '<div id="root"></div>',                                        BlockType.SOFT_BLOCK,"an empty shell"),
    (404, "",                                                             BlockType.NOT_FOUND, "a dead link"),
    (200, "<html><body>" + "real article text. " * 1500 + '<div class="g-recaptcha"></div></body></html>',
                                                                          BlockType.OK,        "a long article with a captcha form at the bottom"),
]
for status, html, want, label in checks:
    got = classify(status, html)
    ok = got is want
    print(f"  {'PASS' if ok else 'FAIL'}  recognises {label}")
    if not ok: fails.append(label)

# 2. Does it climb to the browser only when it has to?
class Fake(Transport):
    def __init__(s, tier, block, html=""): s.tier, s.name, s._b, s._h = tier, f"fake{int(tier)}", block, html
    def available(s): return True
    def fetch(s, url, budget): return Result(url=url, tier=s.tier, block=s._b, status=200, html=s._h, elapsed_ms=1)

real = "<html>" + "content " * 200 + "</html>"

with Ladder([Fake(Tier.HTTP, BlockType.CHALLENGE), Fake(Tier.BROWSER, BlockType.OK, real)]) as L:
    r = L.fetch("https://a.test")
    ok = r.ok and r.tier is Tier.BROWSER
    print(f"  {'PASS' if ok else 'FAIL'}  opens the browser when the cheap way is blocked")
    if not ok: fails.append("escalation")

with Ladder([Fake(Tier.HTTP, BlockType.NOT_FOUND), Fake(Tier.BROWSER, BlockType.OK, real)]) as L:
    ok = L.fetch("https://b.test").tier is Tier.HTTP
    print(f"  {'PASS' if ok else 'FAIL'}  does NOT open the browser for a dead link")
    if not ok: fails.append("404 escalation")

with Ladder([Fake(Tier.HTTP, BlockType.GEO_BLOCK), Fake(Tier.BROWSER, BlockType.OK, real)]) as L:
    ok = L.fetch("https://g.test").block is BlockType.GEO_BLOCK
    print(f"  {'PASS' if ok else 'FAIL'}  does NOT open the browser for a country block")
    if not ok: fails.append("geo escalation")

seen = []
with Ladder([Fake(Tier.HTTP, BlockType.CAPTCHA), Fake(Tier.BROWSER, BlockType.OK, real)], on_human_needed=seen.append) as L:
    ok = L.fetch("https://c.test").block is BlockType.CAPTCHA and len(seen) == 1
    print(f"  {'PASS' if ok else 'FAIL'}  asks you for help on a captcha instead of fighting it")
    if not ok: fails.append("captcha handoff")

# 2b. Can saved cookies go back into the browser without Chrome rejecting them?
ck = {c["name"]: c for c in browser_cookies("youtube.com", {"YSC": "a", "__Secure-YNID": "b", "__Host-x": "c"})}
ok = (ck["YSC"].get("domain") == ".youtube.com"
      and ck["__Secure-YNID"].get("secure") is True
      and ck["__Host-x"].get("secure") is True and "domain" not in ck["__Host-x"]
      and ck["__Host-x"].get("url") == "https://youtube.com/")
print(f"  {'PASS' if ok else 'FAIL'}  replays __Secure-/__Host- cookies in a form Chrome accepts")
if not ok: fails.append("cookie replay")

# 3. Does the stop button work?
b = Budget(max_steps=2)
try:
    b.spend(steps=5); print("  FAIL  stop button"); fails.append("kill switch")
except BudgetExhausted:
    print("  PASS  stops itself when it has tried too many times")

# 4. fetch() keeps one ladder per allow_browser, and close_ladder() lets the Chrome profile go
import fetcher
made = []
class FakeLadder:
    def __init__(s, max_tier): s.max_tier, s.closed = max_tier, False; made.append(s)
    def fetch(s, url, budget): return Result(url=url, tier=s.max_tier, block=BlockType.OK, status=200, html=real, elapsed_ms=1)
    def close(s): s.closed = True
_real_default = fetcher.default_ladder
fetcher.default_ladder = lambda max_tier=Tier.BROWSER, **kw: FakeLadder(max_tier)
try:
    a = fetcher.fetch("https://h.test", allow_browser=False, cache=False)
    b = fetcher.fetch("https://h.test", allow_browser=True, cache=False)
    ok = a.tier is Tier.HTTP and b.tier is Tier.BROWSER
    print(f"  {'PASS' if ok else 'FAIL'}  an HTTP-only fetch first doesn't stop a later one from using the browser")
    if not ok: fails.append("ladder per allow_browser")
    fetcher.close_ladder()
    ok = len(made) == 2 and all(x.closed for x in made)
    print(f"  {'PASS' if ok else 'FAIL'}  close_ladder() closes the shared browser (frees the Chrome profile)")
    if not ok: fails.append("close_ladder")
    fetcher.fetch("https://h.test", cache=False)
    ok = len(made) == 3 and not made[-1].closed
    print(f"  {'PASS' if ok else 'FAIL'}  fetch() after close_ladder() opens a fresh one")
    if not ok: fails.append("ladder reopens")
    fetcher.close_ladder()
finally:
    fetcher.default_ladder = _real_default

# 5. settle(): wait for the page to be ready instead of sleeping a fixed time
import types
class FakePage:
    def __init__(s, html=real, idle_raises=True, eval_raises=False):
        s.calls, s.html, s.idle_raises, s.eval_raises, s.url = [], html, idle_raises, eval_raises, "https://a.test/"
    def wait_for_load_state(s, state, timeout=None):
        s.calls.append((state, timeout))
        if state == "networkidle" and s.idle_raises: raise TimeoutError("Timeout 3000ms exceeded")
    def evaluate(s, js, arg=None, isolated_context=True):
        s.calls.append(("quiet", arg, isolated_context, "MutationObserver" in js))
        if s.eval_raises: raise RuntimeError("Execution context was destroyed")
    def wait_for_timeout(s, ms): s.calls.append(("sleep", ms))
    def goto(s, url, **kw): s.calls.append(("goto", url)); return types.SimpleNamespace(status=200, headers={})
    def content(s): s.calls.append(("content",)); return s.html
    def close(s): pass

p = FakePage()
fetcher.settle(p)
ok = (p.calls[0] == ("domcontentloaded", 10000) and p.calls[1] == ("networkidle", 3000)
      and p.calls[2] == ("quiet", [400, 5000], True, True) and not any(c[0] == "sleep" for c in p.calls))
print(f"  {'PASS' if ok else 'FAIL'}  settle(): load -> network idle (3 s cap) -> DOM quiet 400 ms (isolated world), no fixed sleep")
if not ok: fails.append("settle steps")
try:
    fetcher.settle(FakePage(eval_raises=True)); ok = True
except Exception:
    ok = False
print(f"  {'PASS' if ok else 'FAIL'}  settle(): a timeout or a navigation mid-wait is not an error")
if not ok: fails.append("settle errors")

class FakeCtx:
    def __init__(s, page): s.page = page
    def new_page(s): return s.page
    def add_cookies(s, c): pass
    def cookies(s, url=None): return []

def browser_fetch(html):
    tr = fetcher.PatchrightTransport(profile_dir="unused")
    page = FakePage(html=html)
    tr._ctxs["_direct"] = FakeCtx(page)
    return tr.fetch("https://a.test/", Budget(max_seconds=30)), page

r, p = browser_fetch(real)
ok = r.ok and ("quiet", [400, 5000], True, True) in p.calls and not any(c[0] == "sleep" for c in p.calls)
print(f"  {'PASS' if ok else 'FAIL'}  browser fetch: a normal page is read once it settles, no fixed 3 s sleep")
if not ok: fails.append("browser fetch settle")
r, p = browser_fetch("<title>Just a moment...</title>")
ok = ("sleep", 3000) in p.calls and p.calls.count(("content",)) == 2
print(f"  {'PASS' if ok else 'FAIL'}  browser fetch: a challenge page still gets the extra beat, then is read again")
if not ok: fails.append("browser fetch challenge beat")

print()
print("ALL GOOD - fetcher.py is working." if not fails else f"{len(fails)} FAILED: {fails}")

import sys
sys.exit(1 if fails else 0)
