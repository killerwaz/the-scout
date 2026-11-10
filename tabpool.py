"""tabpool.py - Phase 3: fetch many URLs in parallel tabs of one Chrome.

    from tabpool import TabPool
    with TabPool() as pool:                # n=None: as many tabs as free RAM allows (max 8)
        results = pool.fetch_all(urls)     # list[fetcher.Result], same order as urls

One headed Chrome on fetcher's persistent profile (Tier.BROWSER), N tabs, each
pulling from a shared queue. Shared across every tab for the whole run:
  - the per-site pacer (two tabs never hit one site faster than the pacer allows)
  - fetcher's CircuitBreaker (a site that blocks 3 times is left alone)
  - Telemetry and SessionStore (cookies an OK page earns are saved for the site)

Session continuity: all tabs live in one browser context, so a site sees one
cookie jar for the whole run whichever tab picks its URL up. Different
fingerprints per tab is Phase 7.

This is the browser rung only. Cheap HTTP first is the caller's choice
(fetcher.fetch / Ladder); Phase 5 decides which URLs need a tab.
"""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence, TypeVar

from fetcher import (BlockType, current_profile, CircuitBreaker, Result, SessionStore,
                     Telemetry, Tier, browser_cookies, classify, detect_waf, domain_of,
                     dump_failure, settle_async)

T = TypeVar("T")
R = TypeVar("R")

#: Phase 3 RAM measurement on this PC (2026-10-21, PLAN.md §8): our Chrome is
#: ~400 MB with one tab and ~325 MB more per tab on real pages (8 tabs ~3 GB).
#: The PC has 15.5 GB but other apps leave ~1.5-2 GB free, so the tab count is
#: picked from free RAM at start, not fixed.
MAX_TABS = 8
CHROME_BASE_MB = 400
TAB_MB = 325
RESERVE_MB = 800        # left for Windows and everything else


def tabs_for(free_mb: float, max_tabs: int = MAX_TABS) -> int:
    """How many tabs fit in this much free RAM, between 1 and max_tabs."""
    return max(1, min(max_tabs, int((free_mb - RESERVE_MB - CHROME_BASE_MB) // TAB_MB)))


def _free_ram_posix() -> float:
    """Linux: sysconf. Where that isn't there (macOS), assume room for 4 tabs."""
    import os
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (AttributeError, ValueError, OSError):
        return float(RESERVE_MB + CHROME_BASE_MB + 4 * TAB_MB)


def free_ram_mb() -> float:
    """Free physical memory right now (Windows GlobalMemoryStatusEx; sysconf elsewhere)."""
    import ctypes
    if not hasattr(ctypes, "windll"):
        return _free_ram_posix()

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**20


async def run_queue(items: Sequence[T], n: int,
                    work: Callable[[int, T], Awaitable[R]]) -> list[R]:
    """Run work(worker_id, item) over items with n workers pulling from one
    queue. Results come back in input order."""
    q: asyncio.Queue = asyncio.Queue()
    for i, item in enumerate(items):
        q.put_nowait((i, item))
    out: list = [None] * len(items)

    async def worker(wid: int) -> None:
        while True:
            try:
                i, item = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            out[i] = await work(wid, item)

    await asyncio.gather(*(worker(w) for w in range(max(1, min(n, len(items))))))
    return out


class AsyncPacer:
    """fetcher.Pacer for coroutines: per-site spacing with jitter, shared by
    every tab. Sites are paced independently, so 8 different sites go at once."""

    def __init__(self, min_interval: float = 3.0, jitter: float = 0.4) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, domain: str) -> float:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            gap = self.min_interval * (1.0 + random.uniform(-self.jitter, self.jitter))
            last = self._last.get(domain)
            slept = 0.0
            if last is not None:
                slept = max(0.0, gap - (time.monotonic() - last))
                if slept:
                    await asyncio.sleep(slept)
            self._last[domain] = time.monotonic()
            return slept


class TabPool:
    def __init__(self, n: Optional[int] = None, profile_dir: Path | str | None = None,
                 headless: bool = False, settle_ms: int = 3000, seconds: float = 45.0,
                 pacer: Optional[AsyncPacer] = None, breaker: Optional[CircuitBreaker] = None,
                 sessions: Optional[SessionStore] = None,
                 telemetry: Optional[Telemetry] = None) -> None:
        self.n = n or tabs_for(free_ram_mb())    # None = as many as free RAM allows
        self.profile_dir = Path(profile_dir or current_profile())
        self.headless = headless
        self.settle_ms = settle_ms
        self.seconds = seconds
        self.telemetry = telemetry or Telemetry()
        self.breaker = breaker or CircuitBreaker(self.telemetry)
        self.sessions = sessions or SessionStore()
        self.pacer = pacer or AsyncPacer()
        self._loop = asyncio.new_event_loop()
        self._pw = None
        self._ctx = None
        self._seeded: set[str] = set()

    # -- browser plumbing (overridden in check_tabpool.py) --------------------

    async def _start(self) -> None:
        from patchright.async_api import async_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir), channel="chrome",
            headless=self.headless, no_viewport=True)

    async def _new_page(self):
        return await self._ctx.new_page()

    async def _close_page(self, page) -> None:
        try:
            await page.close()
        except Exception:
            pass

    async def _load(self, page, url: str, timeout_ms: int):
        """-> (status, html, final_url, headers, cookies for that URL)"""
        dom = domain_of(url)
        if dom not in self._seeded:          # one cookie jar per site per run
            self._seeded.add(dom)
            stored = self.sessions.cookies(dom)
            if stored:
                await self._ctx.add_cookies(browser_cookies(dom, stored))
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await settle_async(page)
        html = await page.content()
        if classify(resp.status if resp else None, html) is BlockType.CHALLENGE:
            await page.wait_for_timeout(self.settle_ms)      # interstitials need a beat more
            await settle_async(page)
            html = await page.content()
        cks = {c["name"]: c["value"] for c in await self._ctx.cookies(page.url)}
        return (resp.status if resp else None, html, page.url,
                resp.headers if resp else {}, cks)

    async def _stop(self) -> None:
        if self._ctx is not None:
            await self._ctx.close()
        if self._pw is not None:
            await self._pw.stop()

    # -- the pool --------------------------------------------------------------

    async def _fetch_one(self, page, url: str) -> Result:
        dom = domain_of(url)
        if self.breaker and not self.breaker.allow(dom):
            return Result(url=url, tier=Tier.BROWSER, block=BlockType.CIRCUIT_OPEN,
                          error=f"circuit breaker open for {dom}")
        await self.pacer.wait(dom)
        t0 = time.monotonic()
        try:
            status, html, final, hdrs, cks = await self._load(
                page, url, int(min(30.0, self.seconds) * 1000))
            block = classify(status, html)
            r = Result(url=url, tier=Tier.BROWSER, block=block, status=status,
                       final_url=final, html=html, waf=detect_waf(hdrs, cks, html, status),
                       elapsed_ms=int((time.monotonic() - t0) * 1000))
            if block is BlockType.OK:
                self.sessions.update(dom, cks, url=final)
        except Exception as exc:
            r = Result(url=url, tier=Tier.BROWSER, block=BlockType.TRANSPORT_ERROR,
                       error=f"{type(exc).__name__}: {exc}",
                       elapsed_ms=int((time.monotonic() - t0) * 1000))
        self.telemetry.record(url, r.tier, r.block, r.elapsed_ms)
        if self.breaker and r.block is not BlockType.NOT_FOUND:
            self.breaker.record(dom, r.ok)
        if not r.ok:
            dump_failure(r)
        return r

    async def _fetch_all(self, urls: Sequence[str]) -> list[Result]:
        pages: dict[int, object] = {}

        async def work(wid: int, url: str) -> Result:
            if wid not in pages:
                pages[wid] = await self._new_page()
            return await self._fetch_one(pages[wid], url)

        try:
            return await run_queue(list(urls), self.n, work)
        finally:
            for p in pages.values():
                await self._close_page(p)

    def fetch_all(self, urls: Sequence[str]) -> list[Result]:
        """Fetch every URL in parallel tabs. One Result per URL, in order."""
        return self._loop.run_until_complete(self._fetch_all(urls))

    def __enter__(self) -> "TabPool":
        self._loop.run_until_complete(self._start())
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._loop.run_until_complete(self._stop())
        finally:
            self._loop.close()
