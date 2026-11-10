"""check_tabpool.py - offline self-test for tabpool.py. No browser, no network.
    python check_tabpool.py"""
import asyncio
import tempfile
import time
from pathlib import Path

from fetcher import BlockType, CircuitBreaker, SessionStore, Telemetry
from tabpool import AsyncPacer, TabPool, run_queue

fails = []


def t(label, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        fails.append(label)


# 1. The queue: N workers, results in input order, never more than N at once.
async def q_test():
    live, peak = 0, 0

    async def work(worker_id, item):
        nonlocal live, peak
        live += 1; peak = max(peak, live)
        await asyncio.sleep(0.3)
        live -= 1
        return item * 10

    t0 = time.monotonic()
    out = await run_queue(list(range(8)), 8, work)
    wall = time.monotonic() - t0
    t("queue: results come back in input order", out == [i * 10 for i in range(8)])
    t("queue: 8 jobs on 8 workers take about one job's time", wall < 0.6)
    live, peak = 0, 0
    await run_queue(list(range(10)), 3, work)
    t("queue: never more than N jobs at once", peak == 3)
asyncio.run(q_test())


# 2. Pacer shared across tabs: same site waits, different sites don't.
async def p_test():
    p = AsyncPacer(min_interval=0.5, jitter=0.0)
    t0 = time.monotonic()
    await asyncio.gather(p.wait("a.com"), p.wait("a.com"))
    same = time.monotonic() - t0
    t0 = time.monotonic()
    await asyncio.gather(p.wait("b.com"), p.wait("c.com"))
    diff = time.monotonic() - t0
    t("pacer: two tabs on one site are spaced out", same >= 0.45)
    t("pacer: different sites don't wait for each other", diff < 0.2)
asyncio.run(p_test())


# 2b. How many tabs: from free RAM (measured 2026-10-21: ~400 MB base + ~325 MB a tab).
from tabpool import tabs_for  # noqa: E402
t("tabs: ~2 GB free (today) -> 2 tabs", tabs_for(1983) == 2)
t("tabs: almost nothing free -> still 1", tabs_for(300) == 1)
t("tabs: plenty free -> capped at 8", tabs_for(12000) == 8)
t("tabs: ~3.5 GB free -> 7", tabs_for(3500) == 7)

# 3. The pool itself, with the browser stubbed out.
REAL = "<html><body>" + "real article text. " * 80 + "</body></html>"
BLOCKED = "<html><head><title>Just a moment...</title></head></html>"


class FakePool(TabPool):
    loads: list = []

    async def _start(self):
        self._ctx = object()

    async def _new_page(self):
        return object()

    async def _close_page(self, page):
        pass

    async def _load(self, page, url, timeout_ms):
        FakePool.loads.append(url)
        html = BLOCKED if "bad.test" in url else REAL
        return 200, html, url, {}, {"sid_cookie_ok": "1", "pref": "dark"}

    async def _stop(self):
        pass


tmp = Path(tempfile.mkdtemp())
tel = Telemetry(tmp / "t.db")
breaker = CircuitBreaker(tel, threshold=3)
sessions = SessionStore(tmp / "sessions")
urls = [f"https://bad.test/{i}" for i in range(5)] + ["https://good.test/a"]
with FakePool(n=1, pacer=AsyncPacer(0.0, 0.0), breaker=breaker, sessions=sessions,
              telemetry=tel) as pool:
    res = pool.fetch_all(urls)
t("pool: one Result per URL, in order", [r.url for r in res] == urls)
t("pool: blocked pages are classified (challenge)", res[0].block is BlockType.CHALLENGE)
t("pool: breaker shared across the run opens after 3 failures, later URLs aren't loaded",
  res[3].block is BlockType.CIRCUIT_OPEN and "https://bad.test/3" not in FakePool.loads)
t("pool: an OK page saves its cookies for the site (session continuity)",
  res[5].ok and sessions.cookies("good.test").get("pref") == "dark")
t("pool: telemetry records each load", tel.report("bad.test").startswith("bad.test: BROWSER"))

# 4. A tab waits for the page to settle, not a fixed 3 s (Phase 8).
import types  # noqa: E402


class AsyncFakePage:
    def __init__(self, html):
        self.calls, self.html, self.url = [], html, "https://a.test/"

    async def goto(self, url, **kw):
        return types.SimpleNamespace(status=200, headers={})

    async def wait_for_load_state(self, state, timeout=None):
        self.calls.append((state, timeout))

    async def evaluate(self, js, arg=None, isolated_context=True):
        self.calls.append(("quiet", arg, isolated_context))

    async def wait_for_timeout(self, ms):
        self.calls.append(("sleep", ms))

    async def content(self):
        self.calls.append(("content",))
        return self.html


class AsyncFakeCtx:
    async def add_cookies(self, c):
        pass

    async def cookies(self, url=None):
        return []


def tab_load(html):
    pool = TabPool(n=1, sessions=SessionStore(tmp / "s4"), telemetry=tel)
    pool._ctx = AsyncFakeCtx()
    page = AsyncFakePage(html)
    try:
        out = pool._loop.run_until_complete(pool._load(page, "https://a.test/", 30000))
    finally:
        pool._loop.close()
    return out, page


(status, html, *_), page = tab_load(REAL)
t("tab: a normal page is read once it settles (network idle, DOM quiet), no fixed sleep",
  ("networkidle", 3000) in page.calls and ("quiet", [400, 5000], True) in page.calls
  and not any(c[0] == "sleep" for c in page.calls))
_, page = tab_load(BLOCKED)
t("tab: a challenge page still gets the extra beat, then is read again",
  ("sleep", 3000) in page.calls and page.calls.count(("content",)) == 2)

print()
print("ALL GOOD - tabpool.py is working." if not fails else f"{len(fails)} FAILED: {fails}")

import sys
sys.exit(1 if fails else 0)
