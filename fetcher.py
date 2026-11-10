"""fetcher.py - one file. Fetches web pages, escalating from a plain HTTP
request to a real Chrome browser only when the cheap way gets blocked.

Usage:
    from fetcher import fetch
    page = fetch("https://example.com")
    print(page.ok, page.block, len(page.html))
"""
from __future__ import annotations

__version__ = "1.6"

import hashlib
import ipaddress
import json
import os
import logging
import random
import re
import socket
import sqlite3
import time
from abc import ABC, abstractmethod
from curl_cffi import requests as cffi
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from html import unescape
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit


class Tier(IntEnum):
    """Escalation rungs, cheapest first. Ordering is load-bearing: the ladder
    walks these in ascending order, so never reorder without changing routing."""

    HTTP = 0      # curl_cffi, TLS-impersonated. No browser. ~50ms, free.
    SIGNED = 1    # declared UA + Web Bot Auth signature. Honest lane. STUB.
    BROWSER = 2   # Patchright, headed Chrome, real profile. ~2-4s, free.
    CDP = 3       # nodriver/zendriver, CDP-direct, no Playwright shim. STUB.
    ENGINE = 4    # Camoufox, engine-level fingerprint. ~200MB, 42s/challenge. STUB.
    VENDOR = 5    # managed session w/ signed-agent identity you can't self-issue. STUB.


class BlockType(str, Enum):
    """Five failures that look alike and need different responses.
    Collapsing these into a bool is the single most common instrumentation bug."""

    OK = "ok"
    NOT_FOUND = "not_found"              # 404/410 — the URL is wrong. Do NOT escalate.
    HARD_BLOCK = "hard_block"            # 403/401 pre-body. Escalate.
    CHALLENGE = "challenge"              # interstitial (Turnstile/JS check). Escalate.
    CAPTCHA = "captcha"                  # explicit human challenge. Hand to human.
    RATE_LIMIT = "rate_limit"            # 429 / soft 503. Back off, do NOT escalate.
    SOFT_BLOCK = "soft_block"            # 200 + shell/wall/empty. Escalate.
    LOGIN_WALL = "login_wall"            # 200 + "sign in to continue". Do NOT escalate.
    GEO_BLOCK = "geo_block"              # 200 + country picker / "not in your region". Do NOT escalate.
    TRANSPORT_ERROR = "transport_error"  # DNS/TLS/timeout. Retry same tier once.
    CIRCUIT_OPEN = "circuit_open"        # WE refused, the site never saw us.

    @property
    def should_escalate(self) -> bool:
        return self in {
            BlockType.HARD_BLOCK,
            BlockType.CHALLENGE,
            BlockType.SOFT_BLOCK,
        }

    @property
    def needs_human(self) -> bool:
        return self is BlockType.CAPTCHA

    @property
    def is_boundary(self) -> bool:
        """Hit the logged-out/logged-in line. Stop. Climbing a rung does not
        cross it; only credentials do, and that is a different legal category."""
        return self is BlockType.LOGIN_WALL


class BudgetExhausted(RuntimeError):
    """Raised the instant any cap is crossed. This is the kill switch."""


@dataclass
class Budget:
    """Hard caps. Every fetch takes one. There is no unbounded path through
    this codebase — that is the point of passing it explicitly."""

    max_steps: int = 6
    max_seconds: float = 45.0
    max_usd: float = 0.02

    steps: int = 0
    usd: float = 0.0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed)

    def spend(self, steps: int = 1, usd: float = 0.0) -> None:
        self.steps += steps
        self.usd += usd
        self.check()

    def check(self) -> None:
        if self.steps > self.max_steps:
            raise BudgetExhausted(f"steps {self.steps} > {self.max_steps}")
        if self.elapsed > self.max_seconds:
            raise BudgetExhausted(f"elapsed {self.elapsed:.1f}s > {self.max_seconds}s")
        if self.usd > self.max_usd:
            raise BudgetExhausted(f"spend ${self.usd:.4f} > ${self.max_usd}")

    @property
    def exhausted(self) -> bool:
        try:
            self.check()
            return False
        except BudgetExhausted:
            return True


@dataclass
class Result:
    url: str
    tier: Tier
    block: BlockType
    status: Optional[int] = None
    final_url: Optional[str] = None
    html: str = ""
    elapsed_ms: int = 0
    error: Optional[str] = None
    waf: str = "unknown"
    _text: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.block is BlockType.OK

    @property
    def text(self) -> str:
        """The readable article, with nav/ads/footers stripped.

        Use this, not .html, for anything downstream. A real page is ~400KB of
        markup and ~8KB of words; feeding the markup to a model costs ~50x more
        and reads worse. Uses trafilatura when installed, falls back to a tag
        stripper when not."""
        if self._text is None:
            self._text = _extract(self.html)
        return self._text

    def __repr__(self) -> str:  # keeps logs readable
        return (
            f"<Result {self.block.value} tier={self.tier.name} "
            f"status={self.status} waf={self.waf} "
            f"bytes={len(self.html)} {self.elapsed_ms}ms>"
        )


# Interstitial / challenge markers. Ordered by specificity.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "_incapsula_resource",
    "px-captcha",
    "perimeterx",
)

_CAPTCHA_MARKERS = (
    "g-recaptcha",
    "h-captcha",
    "cf-turnstile",
    "recaptcha/api.js",
    "hcaptcha.com/captcha",
    "are you a robot",
    "verify you are human",
)

_ACCESS_DENIED_MARKERS = (
    "access denied",
    "you have been blocked",
    "request blocked",
    "error 1020",
    "attention required! | cloudflare",
    "sorry, you have been blocked",
)

# A page whose entire body is a JS bootstrap. Real content arrives after render,
# so a tier-0 fetch that returns this is a SOFT_BLOCK, not a success.
_SHELL_HINTS = (
    'id="root"></div>',
    'id="app"></div>',
    "you need to enable javascript to run this app",
    "please enable javascript",
)

# Auth walls. These return HTTP 200 with plenty of visible text — nav, legal
# boilerplate, a signup pitch — so a text-length check passes them as content.
# They are the single largest false-positive source on social platforms.
_WALL_MARKERS = (
    # Phrases that are the PAGE, not the page furniture. Generic nav strings
    # like "continue with google" or "sign in" appear on perfectly good content
    # pages too — matching those flags real articles as walls.
    "sign in to continue",
    "log in to continue",
    "sign up to continue",
    "you must log in to continue",
    "join linkedin to see",
    "new to linkedin?",
    "/authwall",
    "sign up for facebook to",
    "log in to see this content",
    "this content isn't available right now",
    "subscribe to continue reading",
    "this content is available to subscribers",
    "members-only content",
    "create an account to read",
    "register to continue",
)

#: A wall marker only means a wall when the marker is most of what's there.
#: Above this much visible text, the page has real content and the login
#: prompt is just chrome around it. Tuned against live x.com (2,419 chars of
#: real posts) and linkedin.com (14,361 chars of real company data), both of
#: which contain login boilerplate AND genuine content.
WALL_TEXT_CEILING = 1200

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

#: Below this many characters of visible text, a 200 is treated as a shell.
MIN_TEXT_CHARS = 500

#: Challenge/CAPTCHA markers only count on a page this short. Real interstitials
#: are small (live zillow/indeed PerimeterX pages: 7-8.6k chars); a 270k-char
#: docs page whose chat widget says "just a moment" (docs.n8n.io) is content.
CHALLENGE_TEXT_CEILING = 20_000

#: Lots of markup, little visible text: the content is in a script blob and only
#: a browser renders it. rtings reviews at tier 0: 266KB, ~1.2k chars of nav.
#: Tuned against x.com profiles (181KB, 2.8k chars of real text) - keep below.
THIN_PAGE_TEXT = 1500
THIN_PAGE_BYTES = 150_000

#: Country pickers and region locks. A better browser does not change the IP's
#: country, so these do not escalate. bestbuy.com from Bangladesh, 2026-10-21.
_GEO_MARKERS = (
    "select your country",
    "choose a country",
    "not available in your country",
    "not available in your region",
    "not available in your location",
)


#: Vendor fingerprints. Knowing WHICH system blocked you is most of the
#: diagnosis — a technique that clears Cloudflare is reportedly near-useless on
#: PerimeterX, so "blocked" without a vendor name is an unactionable result.
def detect_waf(headers: dict, cookies: dict, html: str, status: int | None) -> str:
    """Identify the anti-bot vendor from headers, cookies and script URLs.

    Deliberately does NOT scan body prose. A page that merely *writes about*
    DataDome is not protected by it — an early version of this function
    reported github.com as DataDome because the README named the vendor.
    """
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    c = " ".join(k.lower() for k in (cookies or {}))
    setck = h.get("set-cookie", "")
    # Only <script src=...> and <iframe src=...> URLs, never page text.
    srcs = " ".join(re.findall(r'(?is)<(?:script|iframe)[^>]+src=["\']([^"\']+)', html or "")).lower()

    def any_of(*needles: str) -> bool:
        return any(n in c or n in setck or n in srcs for n in needles)

    if "cf-ray" in h or any_of("cf_clearance", "__cf_bm", "/cdn-cgi/challenge-platform"):
        return "cloudflare"
    if any_of("_abck", "ak_bmsc", "akam/"):
        return "akamai"
    if any_of("datadome", "captcha-delivery.com", "js.datadome.co"):
        return "datadome"
    if any_of("_pxhd", "_px2", "_px3", "/px/", "perimeterx.net", "human-security"):
        return "perimeterx"
    if any_of("incap_ses", "visid_incap", "_incapsula_resource"):
        return "imperva"
    if "x-amzn-waf-action" in h or any_of("awswaf"):
        return "aws-waf"
    # Kasada brands nothing: a bare 429 with essentially no body is its tell.
    if status == 429 and len((html or "").strip()) < 200:
        return "kasada"
    return "unknown"


DEBUG_DIR = Path.home() / ".fetcher" / "debug"


def dump_failure(result: "Result") -> Optional[Path]:
    """Save a non-OK page for inspection. Enable with FETCHER_DEBUG=1.

    Every wrong diagnosis in this project came from reasoning about a page
    nobody had looked at. One saved file ends that argument.
    """
    if os.environ.get("FETCHER_DEBUG", "") not in ("1", "true", "yes"):
        return None
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"{int(time.time())}-{result.block.value}-{domain_of(result.url)}"
        f = DEBUG_DIR / (stem + ".html")
        f.write_text(result.html or "", encoding="utf-8", errors="replace")
        (DEBUG_DIR / (stem + ".txt")).write_text(
            f"url    : {result.url}\n"
            f"tier   : {result.tier.name}\n"
            f"status : {result.status}\n"
            f"waf    : {result.waf}\n"
            f"block  : {result.block.value}\n"
            f"bytes  : {len(result.html)}\n"
            f"error  : {result.error}\n\n"
            f"--- first 1500 chars of visible text ---\n"
            f"{_extract(result.html)[:1500]}\n",
            encoding="utf-8", errors="replace")
        log.warning("saved failing page -> %s", f)
        return f
    except Exception:
        return None


def _extract(html: str) -> str:
    """Article text. trafilatura if available (better), tag-strip if not."""
    if not html:
        return ""
    try:
        import trafilatura
        out = trafilatura.extract(html, include_comments=False, include_tables=True)
        if out:
            return out
    except ImportError:
        pass
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    # Decode entities after stripping tags, so an escaped "&lt;b&gt;" stays text, not a tag
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", body))).strip()


def visible_text_len(html: str) -> int:
    """Cheap proxy for 'is there actually content here'. Strips script/style
    blocks and tags, then counts. Not a parser — deliberately. It runs on every
    fetch and must stay in the microseconds."""
    if not html:
        return 0
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    body = _TAG_RE.sub(" ", body)
    return len(_WS_RE.sub(" ", body).strip())


def classify(
    status: Optional[int],
    html: str = "",
    error: Optional[str] = None,
    min_text_chars: int = MIN_TEXT_CHARS,
) -> BlockType:
    if error:
        return BlockType.TRANSPORT_ERROR

    if status is None:
        return BlockType.TRANSPORT_ERROR

    if status in (404, 410):
        return BlockType.NOT_FOUND

    if status == 429:
        return BlockType.RATE_LIMIT

    low = html.lower().replace("\u2019", "'").replace("\u2018", "'")
    text_len = visible_text_len(html)
    gate = text_len < CHALLENGE_TEXT_CEILING

    # Check CAPTCHA before CHALLENGE: a captcha IS a challenge, but it needs a
    # human, and escalating a browser tier at it just burns budget.
    if gate and any(m in low for m in _CAPTCHA_MARKERS):
        return BlockType.CAPTCHA

    if gate and any(m in low for m in _CHALLENGE_MARKERS):
        return BlockType.CHALLENGE

    if status in (401, 403) or any(m in low for m in _ACCESS_DENIED_MARKERS):
        return BlockType.HARD_BLOCK

    if status == 503:
        # 503 + challenge markers already caught above; a bare 503 is capacity.
        return BlockType.RATE_LIMIT

    if status >= 500:
        return BlockType.TRANSPORT_ERROR

    if status >= 400:
        return BlockType.HARD_BLOCK

    # 2xx/3xx from here. The interesting case.
    if any(m in low for m in _WALL_MARKERS) and text_len < WALL_TEXT_CEILING:
        return BlockType.LOGIN_WALL

    if any(m in low for m in _GEO_MARKERS) and text_len < WALL_TEXT_CEILING:
        return BlockType.GEO_BLOCK

    if any(h in low for h in _SHELL_HINTS) and text_len < min_text_chars:
        return BlockType.SOFT_BLOCK

    if text_len < min_text_chars:
        return BlockType.SOFT_BLOCK

    if text_len < THIN_PAGE_TEXT and len(html) > THIN_PAGE_BYTES:
        return BlockType.SOFT_BLOCK

    return BlockType.OK


class Transport(ABC):
    tier: Tier
    name: str
    sessions: Optional["SessionStore"] = None
    proxies: Optional["ProxyPool"] = None

    @abstractmethod
    def available(self) -> bool:
        """True if this rung's dependencies are installed and usable.
        Checked once at ladder construction; unavailable rungs are dropped
        rather than raising at fetch time."""

    @abstractmethod
    def fetch(self, url: str, budget: Budget) -> Result:
        """Fetch one URL. MUST NOT retry internally and MUST NOT escalate —
        the ladder owns both. MUST respect budget.remaining_seconds as its
        timeout. Never raises for a blocked page; returns a Result with the
        appropriate BlockType."""

    def close(self) -> None:
        """Release browsers, connections, profiles. Idempotent."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} tier={self.tier.name}>"


DEFAULT_DB = Path.home() / ".fetcher" / "telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetches (
    id         INTEGER PRIMARY KEY,
    ts         REAL    NOT NULL,
    domain     TEXT    NOT NULL,
    tier       INTEGER NOT NULL,
    block      TEXT    NOT NULL,
    elapsed_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fetches_domain_tier ON fetches(domain, tier);
CREATE INDEX IF NOT EXISTS ix_fetches_ts ON fetches(ts);
"""


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


#: Names that mean this PC or the local network without a lookup.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
#: How a Result's .error starts when the fetch was refused for where it pointed.
PRIVATE = "private address"
_NAT64 = int(ipaddress.IPv6Address("64:ff9b::")) >> 32


def _resolve(host: str) -> set:
    """Every address a name resolves to. Replaced by a fake in the offline checks."""
    return {info[4][0] for info in socket.getaddrinfo(host, None)}


def _private_ip(text: str) -> bool:
    """True for an address that isn't on the public internet: loopback, LAN, link-local,
    carrier NAT, reserved. Takes the short and numeric spellings a browser accepts
    (127.1, 0x7f.0.0.1, 2130706433) and IPv4 wrapped in IPv6. A name is not an address."""
    text = text.split("%")[0]                        # fe80::1%eth0
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_aton(text))
        except (OSError, ValueError):
            return False
    if ip.version == 6:
        # An IPv4 address carried inside IPv6: mapped, 6to4, Teredo, ::a.b.c.d, NAT64 (64:ff9b::a.b.c.d).
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and int(ip) >> 32 in (0, _NAT64):
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is not None:
            return not inner.is_global
        return ip.is_site_local or not ip.is_global
    return not ip.is_global


def private_host(url: str, resolve: bool = True) -> Optional[str]:
    """Why this URL points at this PC or the local network (n8n, the local model server,
    the router), or None when it doesn't. PLAN §5 limit 7: a web page must not be able to
    send the agent there. Checks the name, the address as written, and (resolve=True) what
    the name resolves to, so a public-looking name with a 192.168.x record is caught too.

    Not covered: a DNS server that answers differently between this check and the
    connection. SCOUT_ALLOW_PRIVATE=1 turns the limit off."""
    if os.environ.get("SCOUT_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes"):
        return None
    if re.search(r"[\\\x00-\x1f\x7f]", url):
        # Chrome reads "\" as "/" and drops tabs and newlines; Python and curl don't. A URL like
        # http://localhost\@example.com/ is example.com to one and localhost to another: refuse it.
        return "the URL has a backslash or control character, and programs disagree on where such a URL points"
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return None
    if not host:
        return None
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return f"{host} is this PC or the local network"
    if _private_ip(host):
        return f"{host} is a private address"
    if resolve:
        try:
            addresses = _resolve(host)
        except (OSError, UnicodeError):
            return None                              # doesn't resolve: the fetch fails on its own
        if any(_private_ip(a) for a in addresses):
            return f"{host} resolves to a private address"
    return None


def refuse_private(r: "Result") -> "Result":
    """r, or a blocked Result without the page when the fetch was redirected to another
    host that is private. The caller checks the URL it asked for; this checks where it ended."""
    if not r.final_url or domain_of(r.final_url) == domain_of(r.url):
        return r
    why = private_host(r.final_url)
    if not why:
        return r
    return Result(url=r.url, tier=r.tier, block=BlockType.HARD_BLOCK, status=r.status,
                  final_url=r.final_url, error=f"{PRIVATE}: redirected to a page where {why}")


class Telemetry:
    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB,
        min_samples: int = 3,
        success_floor: float = 0.6,
        window_days: float = 14.0,
    ) -> None:
        self.min_samples = min_samples
        self.success_floor = success_floor
        self.window_seconds = window_days * 86400
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record(self, url: str, tier: Tier, block: BlockType, elapsed_ms: int) -> None:
        self.conn.execute(
            "INSERT INTO fetches (ts, domain, tier, block, elapsed_ms) "
            "VALUES (?,?,?,?,?)",
            (time.time(), domain_of(url), int(tier), block.value, elapsed_ms),
        )
        self.conn.commit()

    def _rates(self, domain: str) -> dict[int, tuple[int, float]]:
        """tier -> (samples, success_rate) inside the window."""
        cutoff = time.time() - self.window_seconds
        rows = self.conn.execute(
            "SELECT tier, COUNT(*), "
            "       SUM(CASE WHEN block = 'ok' THEN 1 ELSE 0 END) "
            "FROM fetches WHERE domain = ? AND ts > ? GROUP BY tier",
            (domain, cutoff),
        ).fetchall()
        return {t: (n, (ok or 0) / n) for t, n, ok in rows if n}

    def start_tier(self, url: str) -> Optional[Tier]:
        """Cheapest tier with enough evidence that it works for this domain.

        Returns None when there is no evidence — the ladder then starts at the
        bottom, which is correct: an unknown domain is presumed easy until it
        proves otherwise. Optimism here costs ~50ms; pessimism costs a browser
        launch on every first contact.
        """
        rates = self._rates(domain_of(url))
        viable = [
            t for t, (n, rate) in sorted(rates.items())
            if n >= self.min_samples and rate >= self.success_floor
        ]
        return Tier(viable[0]) if viable else None

    def report(self, domain: str) -> str:
        rates = self._rates(domain)
        if not rates:
            return f"{domain}: no data"
        parts = [
            f"{Tier(t).name}={rate:.0%} (n={n})"
            for t, (n, rate) in sorted(rates.items())
        ]
        return f"{domain}: " + "  ".join(parts)

    def close(self) -> None:
        self.conn.close()


#: Bump this as Chrome ships. A stale impersonation target is itself a signal.
IMPERSONATE = "chrome"

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
# Deliberately absent: User-Agent and Sec-CH-UA. curl_cffi sets both to match
# the impersonation target. Overriding either creates the exact internal
# inconsistency that gets a request refused before the body loads.


class HttpTransport(Transport):
    tier = Tier.HTTP
    name = "curl_cffi"

    def __init__(self, impersonate: str = IMPERSONATE) -> None:
        self.impersonate = impersonate
        self._session: cffi.Session | None = None

    def available(self) -> bool:
        return True

    def _sess(self) -> cffi.Session:
        if self._session is None:
            self._session = cffi.Session(impersonate=self.impersonate)
        return self._session

    def fetch(self, url: str, budget: Budget) -> Result:
        t0 = time.monotonic()
        timeout = min(15.0, max(1.0, budget.remaining_seconds))
        dom = domain_of(url)
        headers, cookies = {}, {}
        if self.sessions:
            cookies = self.sessions.cookies(dom)
            prev = self.sessions.last_url(dom)
            # Arriving at a deep page with no referer is a tell: real people
            # come from somewhere. Only ever same-origin, never cross-site.
            if prev and prev != url:
                headers["Referer"] = prev
        try:
            px = self.proxies.for_domain(dom) if self.proxies else None
            r = self._sess().get(url, timeout=timeout, allow_redirects=True,
                                 headers=headers or None, cookies=cookies or None,
                                 proxies=px.dict if px else None)
            html = r.text or ""
            block = classify(r.status_code, html)
            waf = detect_waf(dict(r.headers), dict(r.cookies), html, r.status_code)
            if self.sessions and block is BlockType.OK:
                # Only on success. A cookie issued with a challenge identifies
                # the flagged session; replaying it re-flags you.
                self.sessions.update(dom, dict(r.cookies), url=str(r.url))
            return Result(
                waf=waf,
                url=url,
                tier=self.tier,
                block=block,
                status=r.status_code,
                final_url=str(r.url),
                html=html,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:  # network-layer only; classify() owns the rest
            return Result(
                url=url,
                tier=self.tier,
                block=BlockType.TRANSPORT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


DEFAULT_PROFILE = Path.home() / ".fetcher" / "chrome-profile"
_profile = DEFAULT_PROFILE


def use_profile(name: str) -> None:
    """Pick this process's Chrome profile under ~/.fetcher. Chrome locks a
    profile while it's open, so processes that run at once (the MCP server and
    the Telegram bot) each need their own, or the second browser fails with
    "Opening in existing browser session" (2026-10-21)."""
    global _profile
    _profile = DEFAULT_PROFILE.parent / name


def current_profile() -> Path:
    return _profile


def proxy_profile(profile_dir: Path, key: str) -> Path:
    """Per-exit-node profile; keeps the old name for the default profile."""
    if profile_dir == DEFAULT_PROFILE:
        return profile_dir.parent / f"profile-{key}"
    return profile_dir.parent / f"{profile_dir.name}-profile-{key}"


def browser_cookies(domain: str, cookies: dict) -> list[dict]:
    """SessionStore cookies in the shape Playwright's add_cookies takes.

    Chrome enforces the cookie-name prefixes: `__Secure-` must be secure, and
    `__Host-` must be secure, path=/ and host-only (no domain). add_cookies is
    all-or-nothing, so one malformed cookie (YouTube's `__Secure-YNID`, live
    run 2026-10-21) failed the whole browser fetch."""
    out = []
    for k, v in cookies.items():
        if k.startswith("__Host-"):
            out.append({"name": k, "value": v, "url": f"https://{domain}/", "secure": True})
            continue
        c = {"name": k, "value": v, "domain": "." + domain, "path": "/"}
        if k.startswith("__Secure-"):
            c["secure"] = True
        out.append(c)
    return out


#: Resolves once the DOM has had no changes for quiet ms, or after cap ms.
#: Runs in Patchright's isolated world (evaluate's default), so page scripts can't see it.
_QUIET_JS = """([quiet, cap]) => new Promise(resolve => {
  let timer = null;
  const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, quiet); });
  const stop = setTimeout(done, cap);
  function done() { obs.disconnect(); clearTimeout(timer); clearTimeout(stop); resolve(true); }
  obs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  timer = setTimeout(done, quiet);
})"""
SETTLE = dict(load_ms=10000, idle_ms=3000, quiet_ms=400, cap_ms=5000)


def settle(page, load_ms: int = SETTLE["load_ms"], idle_ms: int = SETTLE["idle_ms"],
           quiet_ms: int = SETTLE["quiet_ms"], cap_ms: int = SETTLE["cap_ms"]) -> None:
    """Wait until the page is ready to read, instead of sleeping a fixed time:
    DOMContentLoaded -> network idle (capped; pages that poll never go idle) ->
    the DOM quiet for quiet_ms (capped). Timeouts and navigations mid-wait are ignored."""
    for state, ms in (("domcontentloaded", load_ms), ("networkidle", idle_ms)):
        try:
            page.wait_for_load_state(state, timeout=ms)
        except Exception:
            pass
    try:
        page.evaluate(_QUIET_JS, [quiet_ms, cap_ms], isolated_context=True)
    except Exception:
        pass


async def settle_async(page, load_ms: int = SETTLE["load_ms"], idle_ms: int = SETTLE["idle_ms"],
                       quiet_ms: int = SETTLE["quiet_ms"], cap_ms: int = SETTLE["cap_ms"]) -> None:
    """settle() for Playwright's async API (the tab pool)."""
    for state, ms in (("domcontentloaded", load_ms), ("networkidle", idle_ms)):
        try:
            await page.wait_for_load_state(state, timeout=ms)
        except Exception:
            pass
    try:
        await page.evaluate(_QUIET_JS, [quiet_ms, cap_ms], isolated_context=True)
    except Exception:
        pass


class PatchrightTransport(Transport):
    tier = Tier.BROWSER
    name = "patchright"

    def __init__(
        self,
        profile_dir: Path | str | None = None,
        headless: bool = False,
        wait_until: str = "domcontentloaded",
        settle_ms: int = 3000,
    ) -> None:
        self.profile_dir = Path(profile_dir or current_profile())
        self.headless = headless
        self.wait_until = wait_until
        self.settle_ms = settle_ms
        self._pw = None
        self._ctx = None
        self._ctxs: dict = {}

    def available(self) -> bool:
        try:
            import patchright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _context(self, proxy: Optional["Proxy"] = None):
        # A browser proxy is fixed at launch, so each exit node needs its own
        # context and its own profile dir - sharing a profile across exit nodes
        # replays one IP's cookies from another, which is the anomaly we are
        # trying to avoid.
        key = proxy.label if proxy else "_direct"
        if key not in self._ctxs:
            from patchright.sync_api import sync_playwright
            if self._pw is None:
                self._pw = sync_playwright().start()
            d = self.profile_dir if key == "_direct" else proxy_profile(self.profile_dir, key)
            d.mkdir(parents=True, exist_ok=True)
            self._ctxs[key] = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(d), channel="chrome", headless=self.headless,
                no_viewport=True,
                proxy={"server": proxy.url} if proxy else None,
            )
        return self._ctxs[key]

    def fetch(self, url: str, budget: Budget) -> Result:
        t0 = time.monotonic()
        timeout_ms = int(min(30.0, max(2.0, budget.remaining_seconds)) * 1000)
        page = None
        try:
            dom = domain_of(url)
            ctx = self._context(self.proxies.for_domain(dom) if self.proxies else None)
            if self.sessions:
                stored = self.sessions.cookies(dom)
                if stored:
                    ctx.add_cookies(browser_cookies(dom, stored))
            page = ctx.new_page()
            resp = page.goto(url, wait_until=self.wait_until, timeout=timeout_ms)
            settle(page)
            html = page.content()
            status = resp.status if resp else None
            # Interstitials resolve a few seconds after the page goes quiet; give
            # them a beat before classifying, or every Cloudflare site reads as CHALLENGE.
            if classify(status, html) is BlockType.CHALLENGE:
                page.wait_for_timeout(self.settle_ms)
                settle(page)
                html = page.content()
            hdrs = resp.headers if resp else {}
            # Scope to this URL only. ctx.cookies() with no argument returns
            # every cookie the profile holds, for every site ever visited.
            cks = {c["name"]: c["value"] for c in ctx.cookies(page.url)}
            _blk = classify(status, html)
            if self.sessions and _blk is BlockType.OK:
                # The clearance cookie the browser just earned now belongs to
                # tier 0 as well. This is the whole point of a shared store.
                # Only on success, for the same reason as tier 0.
                self.sessions.update(dom, cks, url=page.url)
            return Result(
                waf=detect_waf(hdrs, cks, html, status),
                url=url,
                tier=self.tier,
                block=_blk,
                status=status,
                final_url=page.url,
                html=html,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:
            return Result(
                url=url,
                tier=self.tier,
                block=BlockType.TRANSPORT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self) -> None:
        for c in list(self._ctxs.values()):
            try:
                c.close()
            except Exception:
                pass
        self._ctxs = {}
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None


log = logging.getLogger("fetcher")

#: Called with the Result when a page needs a human (CAPTCHA / login).
#: Wire this to Telegram. Do NOT wire it to a CAPTCHA-solving service — that
#: turns "I couldn't read a page" into "I defeated an access control", which
#: is a different category of act with a different set of consequences.
HumanHandoff = Callable[[Result], None]


class SessionStore:
    """Per-domain anonymous session state, persisted to disk.

    At the top end of difficulty the thing being scored is session history, not
    fingerprint: some anti-bot deployments train a model on that specific
    site's own traffic, so a returning visitor with week-old cookies and a
    plausible arrival path scores differently from a first-touch stranger every
    single time.

    Two jobs:
      1. Survive process exit. Cookies that die when the script ends mean you
         arrive as a stranger forever, no matter how good the browser is.
      2. Bridge tiers. When the browser clears a challenge, the clearance
         cookie belongs to the cheap HTTP tier too — so you pay 3s once and
         50ms thereafter, instead of 3s every time.

    SCOPE: anonymous session cookies only. This is not a credential store, and
    nothing here logs in anywhere. Keeping your own __cf_bm is a different act
    from keeping someone's password, and only one of them changes what you are
    legally doing.
    """

    #: Never persisted. If a cookie name looks like authentication, it is not
    #: session continuity and it does not belong on disk.
    #: Substring match, deliberately over-broad. A live run stored GitHub's
    #: `_gh_sess` and `logged_in` because neither contained any earlier token —
    #: harmless while logged out, an authenticated session on disk the moment
    #: the browser profile is signed in anywhere. Losing some continuity is the
    #: cheaper error.
    AUTH_LIKE = ("sess", "auth", "token", "login", "logged", "sid", "li_at",
                 "remember", "passwd", "password", "credential", "csrf",
                 "identity", "account", "user_id", "uid=")

    def __init__(self, root: Path | str = Path.home() / ".fetcher" / "sessions") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, domain: str) -> Path:
        return self.root / (re.sub(r"[^a-z0-9.-]", "_", domain.lower()) + ".json")

    def load(self, domain: str) -> dict:
        f = self._path(domain)
        if not f.exists():
            return {"cookies": {}, "first_seen": None, "visits": 0, "last_url": None}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {"cookies": {}, "first_seen": None, "visits": 0, "last_url": None}

    def _clean(self, cookies: dict) -> dict:
        third = {t.lower() for t in self.THIRD_PARTY}
        return {
            k: v for k, v in (cookies or {}).items()
            if not any(a in k.lower() for a in self.AUTH_LIKE) and k.lower() not in third
        }

    def cookies(self, domain: str) -> dict:
        """Filtered on read, not just on write. A session file written by an
        older build still contains whatever that build allowed; re-filtering
        here means tightening a rule fixes existing data too."""
        return self._clean(self.load(domain).get("cookies", {}))

    def age_days(self, domain: str) -> float:
        first = self.load(domain).get("first_seen")
        return 0.0 if not first else (time.time() - first) / 86400

    def is_new(self, domain: str) -> bool:
        return self.load(domain).get("visits", 0) == 0

    def last_url(self, domain: str) -> Optional[str]:
        return self.load(domain).get("last_url")

    #: Cookie names belonging to third parties that ride along in a shared
    #: browser profile. Sending Facebook's `datr` to a real-estate site is an
    #: anomaly no genuine browser produces — it is worse than sending nothing.
    THIRD_PARTY = ("datr", "fr", "_fbp", "_fbc", "sb", "wd", "xs", "c_user",
                   "_ga", "_gid", "_gcl_au", "IDE", "test_cookie", "MUID",
                   "personalization_id", "guest_id", "li_sugr", "bcookie",
                   "bscookie", "UserMatchHistory", "AnalyticsSyncHistory")

    def update(self, domain: str, cookies: dict, url: Optional[str] = None) -> None:
        st = self.load(domain)
        keep = self._clean(cookies)
        st["cookies"] = self._clean(st.get("cookies", {}))
        st["cookies"].update(keep)
        st["first_seen"] = st.get("first_seen") or time.time()
        st["last_seen"] = time.time()
        st["visits"] = st.get("visits", 0) + 1
        if url:
            st["last_url"] = url
        try:
            self._path(domain).write_text(json.dumps(st), encoding="utf-8")
        except Exception as exc:
            log.debug("session for %s not saved: %s", domain, exc)

    def clear_all(self) -> int:
        n = 0
        for f in self.root.glob("*.json"):
            f.unlink(missing_ok=True); n += 1
        return n

    def forget(self, domain: str) -> None:
        self._path(domain).unlink(missing_ok=True)


@dataclass
class Proxy:
    """One exit node. `url` is the only required field."""
    url: str                      # http://user:pass@host:port
    label: str = ""
    country: str = ""             # "us", "gb", "bd" - for geo-gated targets
    kind: str = "residential"     # datacenter | isp | residential | mobile

    def __post_init__(self) -> None:
        self.label = self.label or urlsplit(self.url).hostname or self.url[:24]

    @property
    def dict(self) -> dict:
        return {"http": self.url, "https": self.url}


class ProxyPool:
    """Sticky per-domain proxy assignment.

    STICKY, NOT ROTATING - and that is the whole design. Rotating IPs on every
    request destroys the session continuity built in SessionStore: cookies
    issued to one IP, replayed from another, is a stronger anomaly than having
    no cookies at all. One domain keeps one exit node until that node gets
    blocked, then it advances and the session for that domain starts over.

    Assignment persists to disk so a restart does not reshuffle every domain.

    Empty pool is a no-op: everything below works unchanged with no proxies
    configured, which is why this seam exists before you need it.
    """

    def __init__(self, proxies: Optional[Iterable[Proxy]] = None,
                 state_path: Path | str = Path.home() / ".fetcher" / "proxy_map.json") -> None:
        self.proxies = list(proxies or [])
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._map: dict[str, int] = {}
        if self.state_path.exists():
            try:
                self._map = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self._map = {}

    @classmethod
    def from_env(cls) -> "ProxyPool":
        """FETCHER_PROXIES=http://u:p@h:1,http://u:p@h:2 (optionally host|country|kind)"""
        raw = os.environ.get("FETCHER_PROXIES", "").strip()
        out = []
        for chunk in (c.strip() for c in raw.split(",") if c.strip()):
            parts = chunk.split("|")
            out.append(Proxy(url=parts[0],
                             country=parts[1] if len(parts) > 1 else "",
                             kind=parts[2] if len(parts) > 2 else "residential"))
        return cls(out)

    def _save(self) -> None:
        try:
            self.state_path.write_text(json.dumps(self._map), encoding="utf-8")
        except Exception as exc:
            log.debug("proxy map not saved: %s", exc)

    def for_domain(self, domain: str, country: str = "") -> Optional[Proxy]:
        pool = [p for p in self.proxies if not country or p.country == country]
        if not pool:
            return None
        idx = self._map.get(domain)
        if idx is None or idx >= len(pool):
            idx = abs(hash(domain)) % len(pool)   # stable first assignment
            self._map[domain] = idx
            self._save()
        return pool[idx]

    def rotate(self, domain: str, country: str = "") -> Optional[Proxy]:
        """Called after a block. Advances this domain to the next exit node."""
        pool = [p for p in self.proxies if not country or p.country == country]
        if not pool:
            return None
        self._map[domain] = (self._map.get(domain, 0) + 1) % len(pool)
        self._save()
        log.warning("rotated %s to proxy %s", domain, pool[self._map[domain]].label)
        return pool[self._map[domain]]

    def __len__(self) -> int:
        return len(self.proxies)


class Pacer:
    """Per-domain request pacing with jitter.

    Rate is the single biggest determinant of whether an IP stays clean —
    bigger than fingerprint. Anti-bot systems count requests per IP against
    rolling windows before they look at anything else. A perfect fingerprint
    hammering one host at 10 req/s loses to a mediocre one at 1 req/6s.

    Jitter matters as much as the interval: perfectly regular timing is itself
    a signature. Real people do not arrive every 3.000 seconds.
    """

    def __init__(self, min_interval: float = 3.0, jitter: float = 0.4) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: dict[str, float] = {}
        self._overrides: dict[str, float] = {}

    def set_domain(self, domain: str, min_interval: float) -> None:
        """Slow down (or speed up) one host. Use after a RATE_LIMIT."""
        self._overrides[domain] = min_interval

    def wait(self, domain: str) -> float:
        base = self._overrides.get(domain, self.min_interval)
        gap = base * (1.0 + random.uniform(-self.jitter, self.jitter))
        last = self._last.get(domain)
        slept = 0.0
        if last is not None:
            slept = max(0.0, gap - (time.monotonic() - last))
            if slept:
                time.sleep(slept)
        self._last[domain] = time.monotonic()
        return slept


class CircuitBreaker:
    """Stop hammering a host that is blocking you.

    This is the piece whose absence quietly destroys an IP. Once a site starts
    refusing you, every further request deepens the reputation penalty — and
    that penalty outlives the run. The breaker trips after N consecutive
    failures on a domain and stays open for a cool-down, so a bad afternoon
    does not become a permanently burned address.

    State persists to disk: a restart must not reset the breaker, or you
    reopen the circuit by rerunning the script.
    """

    def __init__(self, telemetry: "Telemetry", threshold: int = 3,
                 cooldown_minutes: float = 60.0) -> None:
        self.t = telemetry
        self.threshold = threshold
        self.cooldown = cooldown_minutes * 60
        self.t.conn.executescript(
            "CREATE TABLE IF NOT EXISTS breaker ("
            " domain TEXT PRIMARY KEY, fails INTEGER NOT NULL, opened_at REAL)"
        )
        self.t.conn.commit()

    def _row(self, domain: str):
        return self.t.conn.execute(
            "SELECT fails, opened_at FROM breaker WHERE domain = ?", (domain,)
        ).fetchone()

    def allow(self, domain: str) -> bool:
        row = self._row(domain)
        if not row or not row[1]:
            return True
        if time.time() - row[1] < self.cooldown:
            return False
        self.reset(domain)          # cooled down; give it one more chance
        return True

    def record(self, domain: str, ok: bool) -> None:
        if ok:
            self.reset(domain)
            return
        row = self._row(domain)
        fails = (row[0] if row else 0) + 1
        opened = time.time() if fails >= self.threshold else (row[1] if row else None)
        self.t.conn.execute(
            "INSERT INTO breaker (domain, fails, opened_at) VALUES (?,?,?) "
            "ON CONFLICT(domain) DO UPDATE SET fails=?, opened_at=?",
            (domain, fails, opened, fails, opened),
        )
        self.t.conn.commit()

    def reset(self, domain: str) -> None:
        self.t.conn.execute("DELETE FROM breaker WHERE domain = ?", (domain,))
        self.t.conn.commit()

    def reset_all(self) -> None:
        self.t.conn.execute("DELETE FROM breaker"); self.t.conn.commit()

    def open_domains(self) -> list[str]:
        cutoff = time.time() - self.cooldown
        return [r[0] for r in self.t.conn.execute(
            "SELECT domain FROM breaker WHERE opened_at > ?", (cutoff,))]


class Ladder:
    def __init__(
        self,
        transports: Iterable[Transport],
        telemetry: Optional[Telemetry] = None,
        on_human_needed: Optional[HumanHandoff] = None,
        max_tier: Tier = Tier.BROWSER,
        pacer: Optional["Pacer"] = None,
        breaker: Optional["CircuitBreaker"] = None,
        sessions: Optional["SessionStore"] = None,
        proxies: Optional["ProxyPool"] = None,
        min_tiers: Optional[dict] = None,
    ) -> None:
        available = [t for t in transports if t.available()]
        dropped = [t.name for t in transports if not t.available()]
        if dropped:
            log.warning("transports unavailable, dropped: %s", ", ".join(dropped))
        self.transports = sorted(
            (t for t in available if t.tier <= max_tier), key=lambda t: t.tier
        )
        if not self.transports:
            raise RuntimeError("no available transports at or below max_tier")
        self.telemetry = telemetry
        self.on_human_needed = on_human_needed
        self.sessions = sessions or SessionStore()
        self.proxies = proxies if proxies is not None else ProxyPool.from_env()
        for _t in self.transports:
            _t.sessions = self.sessions
            _t.proxies = self.proxies if len(self.proxies) else None
        if len(self.proxies):
            log.info("proxy pool: %d exit nodes", len(self.proxies))
        self.min_tiers: dict = dict(min_tiers or {})
        self.pacer = pacer or Pacer()
        self.breaker = breaker or (CircuitBreaker(telemetry) if telemetry else None)

    def fetch(self, url: str, budget: Optional[Budget] = None,
              min_tier: Optional[Tier] = None) -> Result:
        budget = budget or Budget()
        dom = domain_of(url)
        floor = min_tier or self.min_tiers.get(dom)

        if self.breaker and not self.breaker.allow(dom):
            log.warning("circuit open for %s - refusing to make it worse", dom)
            return Result(url=url, tier=Tier.HTTP, block=BlockType.CIRCUIT_OPEN,
                          error=f"circuit breaker open for {dom}")

        start = self.telemetry.start_tier(url) if self.telemetry else None
        rungs = [t for t in self.transports if start is None or t.tier >= start]
        if floor is not None:
            # Skip everything below the floor entirely - not "try and fail",
            # skip. A failed cheap request is not free; it is a flagged request
            # from your IP that the expensive rung then inherits.
            rungs = [t for t in rungs if t.tier >= floor] or \
                    [t for t in self.transports if t.tier >= floor]
        if not rungs:                       # telemetry pointed above max_tier
            rungs = self.transports

        last: Optional[Result] = None
        for transport in rungs:
            try:
                budget.check()
            except BudgetExhausted as exc:
                log.warning("budget exhausted before %s: %s", transport.name, exc)
                break

            self.pacer.wait(dom)
            result = transport.fetch(url, budget)
            budget.spend(steps=1)
            if self.telemetry:
                self.telemetry.record(url, result.tier, result.block, result.elapsed_ms)
            log.info("%s %s -> %s (%s, %d bytes)", transport.name, url,
                     result.block.value, result.waf, len(result.html))
            if not result.ok:
                dump_failure(result)
            last = result

            if self.breaker:
                # NOT_FOUND is the site working correctly; don't trip on it.
                if result.block is not BlockType.NOT_FOUND:
                    self.breaker.record(dom, result.ok)

            if result.block in (BlockType.HARD_BLOCK, BlockType.CHALLENGE) and \
                    self.proxies and len(self.proxies) > 1:
                # This exit node is burned for this host. Move on, and drop the
                # session with it - those cookies belong to the old IP.
                self.proxies.rotate(dom)
                self.sessions.forget(dom)

            if result.block is BlockType.RATE_LIMIT:
                self.pacer.set_domain(dom, self.pacer._overrides.get(dom, 3.0) * 2)

            if result.ok:
                return result

            if result.block.needs_human:
                if self.on_human_needed:
                    self.on_human_needed(result)
                return result                     # never auto-solve. Stop here.

            if result.block is BlockType.NOT_FOUND:
                return result                     # escalating a 404 is a bug.

            if result.block is BlockType.RATE_LIMIT:
                return result                     # back off; a browser won't help.

            if result.block.is_boundary:
                return result                     # credentials, not a better browser.

            if result.block is BlockType.TRANSPORT_ERROR:
                continue                          # try the next rung, not a retry loop.

            if result.block.should_escalate and self.sessions:
                # Escalation is NOT free. Whatever state the blocked rung
                # accumulated is evidence against this domain; the next rung
                # should not inherit it.
                self.sessions.forget(dom)

            if not result.block.should_escalate:
                return result

        return last or Result(url=url, tier=Tier.HTTP, block=BlockType.TRANSPORT_ERROR,
                              error="no transport ran")

    def close(self) -> None:
        for t in self.transports:
            t.close()

    def __enter__(self) -> "Ladder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def default_ladder(max_tier: Tier = Tier.BROWSER, **kw) -> Ladder:
    return Ladder(
        transports=[HttpTransport(), PatchrightTransport()],
        telemetry=Telemetry(),
        max_tier=max_tier,
        **kw,
    )


#: fetch()'s shared ladders, one per allow_browser value (an HTTP-only ladder made first
#: must not serve a later browser fetch). close_ladder() shuts them.
_LADDERS: dict[bool, "Ladder"] = {}
_CACHE: Optional["Cache"] = None


class Cache:
    """Disk cache keyed on URL. A page you already fetched is free, invisible
    to the target site, and available offline while you iterate on parsing."""

    def __init__(self, root: Path | str = Path.home() / ".fetcher" / "cache",
                 ttl_hours: float = 24.0) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600

    def _path(self, url: str) -> Path:
        return self.root / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def get(self, url: str) -> Optional[Result]:
        f = self._path(url)
        if not f.exists() or (time.time() - f.stat().st_mtime) > self.ttl:
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return Result(url=d["url"], tier=Tier(d["tier"]), block=BlockType(d["block"]),
                          status=d["status"], final_url=d["final_url"], html=d["html"],
                          elapsed_ms=d["elapsed_ms"], error=d["error"],
                          waf=d.get("waf", "unknown"))
        except Exception:
            return None

    def put(self, r: Result) -> None:
        try:
            self._path(r.url).write_text(json.dumps({
                "url": r.url, "tier": int(r.tier), "block": r.block.value,
                "status": r.status, "final_url": r.final_url, "html": r.html,
                "elapsed_ms": r.elapsed_ms, "error": r.error, "waf": r.waf,
            }), encoding="utf-8")
        except Exception as exc:
            log.debug("cache write failed for %s: %s", r.url, exc)


def reset_state(browser_profiles: bool = True) -> None:
    """Wipe sessions, cache, breakers - and Chrome's own profile.

    The last part matters more than the rest. Chrome maintains its own cookie
    jar inside the profile directory, entirely separate from SessionStore. An
    anti-bot cookie stored there is replayed on every launch, and SessionStore
    reporting zero cookies tells you nothing about it.
    """
    import shutil
    n = SessionStore().clear_all()
    t = Telemetry(); CircuitBreaker(t).reset_all(); t.close()
    shutil.rmtree(Path.home() / ".fetcher" / "cache", ignore_errors=True)
    wiped = 0
    if browser_profiles:
        base = Path.home() / ".fetcher"
        for d in list(base.glob("chrome-profile*")) + list(base.glob("profile-*")):
            shutil.rmtree(d, ignore_errors=True); wiped += 1
    log.warning("reset: %d session files, cache, breakers, %d browser profiles",
                n, wiped)


def fetch(url: str, seconds: float = 45.0, allow_browser: bool = True,
          cache: bool = True) -> Result:
    """Fetch one URL. Returns a Result. Reuses one shared ladder and cache."""
    global _CACHE
    if cache:
        if _CACHE is None:
            _CACHE = Cache()
        hit = _CACHE.get(url)
        if hit is not None:
            log.info("cache %s -> %s", url, hit.block.value)
            return hit
    key = bool(allow_browser)
    if key not in _LADDERS:
        _LADDERS[key] = default_ladder(max_tier=Tier.BROWSER if allow_browser else Tier.HTTP)
    r = refuse_private(_LADDERS[key].fetch(url, Budget(max_seconds=seconds)))
    if cache and r.ok:
        _CACHE.put(r)
    return r


def close_ladder() -> None:
    """Close fetch()'s shared ladders and their Chrome. Chrome locks its profile while it
    runs, so a browser fetch() left open blocks the research tab pool on the same profile.
    Call it from the thread that called fetch() (Playwright's sync API is per thread)."""
    for ladder in list(_LADDERS.values()):
        try:
            ladder.close()
        except Exception:
            pass
    _LADDERS.clear()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    urls = sys.argv[1:] or ["https://github.com/Kaliiiiiiiiii-Vinyzu/patchright"]
    for u in urls:
        r = fetch(u)
        print(f"{r.block.value:<16} {r.tier.name:<8} {len(r.html):>8} bytes  {u}")
