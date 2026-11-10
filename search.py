"""search.py - Phase 2 discovery: find WHICH pages to read. Free sources only.

    from search import search
    res = search("n8n release notes", recency="w")
    for h in res.hits: print(h.source, h.url)
    print(res.notes)          # e.g. "mojeek unavailable (3 failures, skipped until 08:15)"

Sources are swappable: each is one function in SOURCES taking (query, recency,
limit) and returning Hits, or raising SourceError when the source refused us.
Web engines are scraped over HTTP (curl_cffi, TLS-impersonated); open APIs are
called directly. Nothing here pays, logs in, or solves a CAPTCHA (PLAN §5).

Google results pages are never fetched: under /goto they cost 500+ requests a
page (AUDIT §3). Any google.com/search URL another source hands back is dropped.

Health: a source that fails 3 times in a row is skipped for an hour, and the
answer says so. State lives in ~/.fetcher/search_health.json.

L0 helpers (no page render): read_feed, discover_feeds, sitemap_urls, jsonld.
Archive fallback: wayback(url), fetch_or_archive(url).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from fetcher import BlockType, Pacer, classify, domain_of

#: Declared, honest UA for open APIs. SEC asks for a contact address as well;
#: without one the ticker list 403s, but the endpoints used below answer.
API_UA = "TheScout/0.1 (personal research tool)"

HEALTH_PATH = Path.home() / ".fetcher" / "search_health.json"
FAILS_TO_SKIP = 3
SKIP_SECONDS = 3600

_pacer = Pacer(min_interval=1.5)
_pacer_lock = threading.Lock()


class SourceError(RuntimeError):
    """The source refused us (block, challenge, bad status). Counts toward health."""


@dataclass
class Hit:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""
    date: Optional[str] = None    # ISO date when the source gives one


@dataclass
class SearchResult:
    query: str
    hits: list[Hit] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- HTTP

def _get(url: str, *, api: bool = False, timeout: float = 25.0):
    from curl_cffi import requests as cr
    dom = domain_of(url)
    with _pacer_lock:
        _pacer.wait(dom)
    try:
        if api:
            return cr.get(url, headers={"User-Agent": API_UA}, timeout=timeout)
        return cr.get(url, impersonate="chrome", timeout=timeout,
                      headers={"Accept-Language": "en-US,en;q=0.9"})
    except Exception as exc:
        raise SourceError(f"{type(exc).__name__}: {str(exc)[:120]}") from exc


def _html(url: str) -> tuple[str, BlockType]:
    """Engine page + how fetcher would classify it. Engines judge by parsed
    results first: Bing's normal results page embeds a `cf-turnstile` hook,
    which classify() alone reads as a CAPTCHA."""
    r = _get(url)
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    return r.text, classify(r.status_code, r.text)


_STOP = {"the", "a", "an", "of", "in", "on", "for", "and", "or", "to", "with", "what",
         "who", "is", "are", "best", "this", "that", "under", "from", "by", "how"}


def _terms(query: str) -> set[str]:
    """Content words, numbers dropped (a shared "2026" proves nothing), plural
    -s folded so "keyboards" matches "keyboard"."""
    words = re.findall(r"[a-z0-9]+", query.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in words if len(w) > 1 and w not in _STOP and not w.isdigit()}


def _relevant(query: str, h: Hit) -> bool:
    """Does this hit share enough words with the query? site: operators are
    ignored. Queries of 3+ content words need 2 matches; shorter need 1."""
    terms = _terms(re.sub(r"\bsite:\S+", " ", query))
    if not terms:
        return True
    need = 2 if len(terms) >= 3 else 1
    return len(terms & _terms(f"{h.title} {h.url} {h.snippet}")) >= need


_browser = None


def _browser_html(url: str) -> tuple[str, BlockType]:
    """The same page through fetcher's Tier.BROWSER (headed Chrome). Engines that
    refuse curl_cffi often answer a real browser: on 2026-10-21 DDG challenged
    HTTP (202) but gave 10 results in Chrome, and Bing gave unrelated results to
    HTTP but the right ones in Chrome. Main thread only: Playwright's sync API
    can't be shared across threads, so search() calls this sequentially."""
    global _browser
    from fetcher import Budget, PatchrightTransport
    if _browser is None:
        import atexit
        _browser = PatchrightTransport()
        atexit.register(_browser.close)
    r = _browser.fetch(url, Budget(max_seconds=40))
    if r.error:
        raise SourceError(f"browser: {r.error[:100]}")
    return r.html, r.block


def close_browser() -> None:
    """Close the engines' browser. Chrome locks a profile while it's open, so
    call this before anything else (tabpool.py) opens fetcher's profile."""
    global _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None


def _engine_done(hits: list[Hit], block: BlockType, query: str) -> list[Hit]:
    """No results AND a blocked-looking page = refused. No results on a clean
    page = an honest empty answer. Results that share no word with the query
    = poisoned: an engine that suspects a bot can answer with unrelated pages
    (Bing gave Reddit threads about Wikipedia for "n8n release notes",
    2026-10-21). That is a refusal too, just a quiet one."""
    if not hits:
        if block is not BlockType.OK:
            raise SourceError(f"{block.value}, no results parsed")
        return hits
    hits = [h for h in hits if h.url.startswith(("http://", "https://"))]
    # Per link, not per list: Bing in the browser mixes good results with
    # unrelated ones (NYT bestsellers, travel guides; 2026-10-21).
    kept = [h for h in hits if _relevant(query, h)]
    if hits and not kept:
        raise SourceError("results don't match the query (poisoned)")
    return kept


def _json(url: str):
    r = _get(url, api=True)
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    try:
        return r.json()
    except Exception as exc:
        raise SourceError("response was not JSON") from exc


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _since(recency: Optional[str]) -> Optional[datetime]:
    days = {"d": 1, "w": 7, "m": 31, "y": 366}.get(recency or "")
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


# ------------------------------------------------------------- web engines

def _brave_key() -> Optional[str]:
    """BRAVE_API_KEY from the environment or .env (the user adds it; never logged)."""
    import os
    if os.environ.get("BRAVE_API_KEY"):
        return os.environ["BRAVE_API_KEY"]
    from scout import load_dotenv
    return load_dotenv().get("BRAVE_API_KEY") or None


def _brave_get(url: str, headers: dict):
    from curl_cffi import requests as cr
    try:
        return cr.get(url, headers=headers, timeout=20)
    except Exception as exc:
        raise SourceError(f"{type(exc).__name__}: {str(exc)[:120]}") from exc


def brave(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    """Brave Search API: clean JSON results without a browser. Added because
    DDG, Bing and Mojeek all refuse or poison plain HTTP from this IP (2026-10-21)."""
    key = _brave_key()
    if not key:
        raise SourceError("no BRAVE_API_KEY in .env")
    url = f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(query)}&count={min(limit, 20)}"
    if recency in ("d", "w", "m", "y"):
        url += f"&freshness=p{recency}"
    r = _brave_get(url, {"Accept": "application/json", "X-Subscription-Token": key})
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    try:
        results = (r.json().get("web") or {}).get("results") or []
    except Exception as exc:
        raise SourceError("response was not JSON") from exc
    return [Hit(x["url"], _text(x.get("title")), _text(x.get("description")), "brave",
                (x.get("page_age") or "")[:10] or None)
            for x in results if x.get("url")]


def ddg(query: str, recency: Optional[str] = None, limit: int = 10,
        browser: bool = False) -> list[Hit]:
    df = f"&df={recency}" if recency in ("d", "w", "m", "y") else ""
    if browser:
        html, block = _browser_html(f"https://duckduckgo.com/?q={quote_plus(query)}&ia=web{df}")
        hits = []
        for a in re.finditer(r'<a href="([^"]+)"[^>]*data-testid="result-title-a"[^>]*>(.*?)</a>', html, re.S):
            hits.append(Hit(unescape(a.group(1)), _text(a.group(2)), "", "ddg"))
            if len(hits) >= limit:
                break
        return _engine_done(hits, block, query)
    html, block = _html(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}{df}")
    hits = []
    for item in re.split(r'class="[^"]*result__body', html)[1:]:
        a = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not a:
            continue
        href = unescape(a.group(1))
        real = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in real:          # ad
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', item, re.S)
        hits.append(Hit(real, _text(a.group(2)), _text(sn.group(1)) if sn else "", "ddg"))
        if len(hits) >= limit:
            break
    return _engine_done(hits, block, query)


def _bing_real(href: str) -> str:
    """Bing wraps results as /ck/a?...&u=a1<base64url>. Unwrap to the real URL."""
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:]
        try:
            return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
        except Exception:
            return href
    return href


def bing(query: str, recency: Optional[str] = None, limit: int = 10,
         browser: bool = False) -> list[Hit]:
    url = (f"https://www.bing.com/search?q={quote_plus(query)}"
           "&setlang=en-US&cc=US&mkt=en-US")
    ez = {"d": "ez1", "w": "ez2", "m": "ez3"}.get(recency or "")
    if ez:
        url += f'&filters=ex1%3a%22{ez}%22'
    html, block = (_browser_html if browser else _html)(url)
    hits = []
    for item in re.split(r'class="[^"]*b_algo', html)[1:]:
        a = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not a:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", item, re.S)
        hits.append(Hit(_bing_real(unescape(a.group(1))), _text(a.group(2)),
                        _text(p.group(1)) if p else "", "bing"))
        if len(hits) >= limit:
            break
    return _engine_done(hits, block, query)


def mojeek(query: str, recency: Optional[str] = None, limit: int = 10,
           browser: bool = False) -> list[Hit]:
    url = f"https://www.mojeek.com/search?q={quote_plus(query)}"
    html, block = (_browser_html if browser else _html)(url)
    hits = []
    for a in re.finditer(r'<a[^>]*class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        hits.append(Hit(unescape(a.group(1)), _text(a.group(2)), "", "mojeek"))
        if len(hits) >= limit:
            break
    return _engine_done(hits, block, query)


# ---------------------------------------------------------------- open APIs

def wikipedia(query: str, recency: Optional[str] = None, limit: int = 5) -> list[Hit]:
    d = _json("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
              f"&srlimit={limit}&srsearch={quote_plus(query)}")
    return [Hit("https://en.wikipedia.org/wiki/" + quote_plus(x["title"].replace(" ", "_")),
                x["title"], _text(x.get("snippet", "")), "wikipedia",
                (x.get("timestamp") or "")[:10] or None)
            for x in d.get("query", {}).get("search", [])]


def hn(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    url = (f"https://hn.algolia.com/api/v1/search?query={quote_plus(query)}"
           f"&tags=story&hitsPerPage={limit}")
    since = _since(recency)
    if since:
        url += f"&numericFilters=created_at_i>{int(since.timestamp())}"
    d = _json(url)
    out = []
    for x in d.get("hits", []):
        item = f"https://news.ycombinator.com/item?id={x['objectID']}"
        out.append(Hit(x.get("url") or item, x.get("title") or "",
                       f"HN discussion: {item} ({x.get('points', 0)} points)", "hn",
                       (x.get("created_at") or "")[:10] or None))
    return out


def reddit(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    t = {"d": "day", "w": "week", "m": "month", "y": "year"}.get(recency or "", "all")
    d = _json(f"https://www.reddit.com/search.json?q={quote_plus(query)}&t={t}&limit={limit}")
    return [Hit("https://www.reddit.com" + c["data"]["permalink"], c["data"].get("title", ""),
                c["data"].get("selftext", "")[:200], "reddit",
                datetime.fromtimestamp(c["data"].get("created_utc", 0), timezone.utc).date().isoformat())
            for c in d.get("data", {}).get("children", [])]


def github(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    q = query
    since = _since(recency)
    if since:
        q += f" pushed:>{since.date().isoformat()}"
    d = _json(f"https://api.github.com/search/repositories?q={quote_plus(q)}"
              f"&sort=stars&order=desc&per_page={limit}")
    return [Hit(x["html_url"], x["full_name"], (x.get("description") or "")[:200], "github",
                (x.get("pushed_at") or "")[:10] or None)
            for x in d.get("items", [])]


def arxiv(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    terms = "+AND+".join(f"all:{quote_plus(w)}" for w in query.split())
    r = _get(f"https://export.arxiv.org/api/query?search_query={terms}"
             f"&sortBy=submittedDate&sortOrder=descending&max_results={limit}", api=True)
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    hits = read_feed_xml(r.text, "arxiv")
    since = _since(recency)
    return [h for h in hits if not since or not h.date or h.date >= since.date().isoformat()]


def openalex(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    url = (f"https://api.openalex.org/works?search={quote_plus(query)}"
           f"&per-page={limit}&sort=relevance_score:desc")
    since = _since(recency)
    if since:
        url += f"&filter=from_publication_date:{since.date().isoformat()}"
    d = _json(url)
    out = []
    for x in d.get("results", []):
        link = (x.get("primary_location") or {}).get("landing_page_url") or x.get("doi") or x.get("id")
        out.append(Hit(link, x.get("display_name") or "", "", "openalex", x.get("publication_date")))
    return out


def edgar(query: str, recency: Optional[str] = None, limit: int = 10) -> list[Hit]:
    """Latest SEC filings for the company the query names (e.g. "NVIDIA")."""
    s = _json(f"https://efts.sec.gov/LATEST/search-index?keysTyped={quote_plus(query)}")
    found = (s.get("hits") or {}).get("hits") or []
    if not found:
        return []
    cik = str(found[0]["_id"]).zfill(10)
    name = (found[0].get("_source") or {}).get("entity", "")
    d = _json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rec = d.get("filings", {}).get("recent", {})
    out = []
    for form, date, acc, doc in zip(rec.get("form", []), rec.get("filingDate", []),
                                    rec.get("accessionNumber", []), rec.get("primaryDocument", [])):
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{acc.replace('-', '')}/{doc}")
        out.append(Hit(url, f"{name} {form} ({date})", "", "edgar", date))
        if len(out) >= limit:
            break
    return out


SOURCES: dict[str, Callable[..., list[Hit]]] = {
    "brave": brave, "ddg": ddg, "bing": bing, "mojeek": mojeek, "wikipedia": wikipedia,
    "hn": hn, "reddit": reddit, "github": github, "arxiv": arxiv,
    "openalex": openalex, "edgar": edgar,
}
WEB = ["brave", "ddg", "bing", "mojeek"]
#: Engines retried once through the browser when HTTP refuses them.
BROWSER_FALLBACK = {"ddg", "bing", "mojeek"}


# ------------------------------------------------------------------ health

class Health:
    def __init__(self, path: Path | str = HEALTH_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.state = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state), encoding="utf-8")
        except Exception:
            pass

    def skipped_until(self, name: str, now: Optional[float] = None) -> Optional[float]:
        until = self.state.get(name, {}).get("skip_until", 0)
        return until if until > (now or time.time()) else None

    def record(self, name: str, ok: bool, why: str = "", now: Optional[float] = None) -> None:
        with self._lock:
            s = self.state.setdefault(name, {"fails": 0, "skip_until": 0})
            if ok:
                s["fails"], s["skip_until"] = 0, 0
            else:
                s["fails"] += 1
                s["last_error"] = why
                if s["fails"] >= FAILS_TO_SKIP:
                    s["skip_until"] = (now or time.time()) + SKIP_SECONDS
            self._save()


# ------------------------------------------------------------------ search

def _norm(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/")
    if p.netloc.endswith("arxiv.org"):          # abs/…v1 == pdf/… == html/…v1
        path = re.sub(r"^/(?:abs|pdf|html)/([\d.]+)(?:v\d+)?(?:\.pdf)?$", r"/abs/\1", path)
    return (p.netloc.lower().removeprefix("www.") + path).lower()


_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
#: "8 Best Budget Mechanical Keyboards for Coding (September 2026)": the shape of
#: every SEO-farm link in the 2026-10-21 marks. Real review sites (rtings,
#: Wirecutter) title theirs "The 4 Best ... of 2026".
_LISTICLE = re.compile(rf"^\s*\d+\s+best\b.*\((?:{_MONTHS})\s+\d{{4}}\)", re.I)


def demote_listicles(hits: list[Hit]) -> list[Hit]:
    """Move SEO-style best-of lists to the end. Demoted, not dropped: a few
    legit sites use the same title style. Order is otherwise kept."""
    keep = [h for h in hits if not _LISTICLE.search(h.title)]
    return keep + [h for h in hits if _LISTICLE.search(h.title)]


def _is_google_serp(url: str) -> bool:
    p = urlparse(url)
    return "google." in p.netloc and p.path.startswith("/search")


def search(query: str, sources: Optional[list[str]] = None, recency: Optional[str] = None,
           limit: int = 10, health: Optional[Health] = None,
           browser_fallback: bool = True) -> SearchResult:
    """Run the query on each healthy source in parallel and merge.

    Hits are interleaved round-robin across sources (each source's own ranking
    kept), de-duplicated by URL. `recency` is d/w/m/y or None."""
    health = health or Health()
    res = SearchResult(query)
    names = list(sources or WEB + ["wikipedia"])
    if WEB[0] not in names and any(n in WEB for n in names):
        names.insert(0, WEB[0])           # the planner names ddg/bing; Brave always leads
    run = []
    for n in names:
        until = health.skipped_until(n)
        if until:
            res.notes.append(f"{n} unavailable (skipped until "
                             f"{datetime.fromtimestamp(until).strftime('%H:%M')})")
        else:
            run.append(n)

    def one(n: str):
        try:
            return n, SOURCES[n](query, recency, limit), None
        except Exception as exc:          # SourceError, or a source's parser broke
            return n, [], f"{type(exc).__name__}: {exc}"

    per: dict[str, list[Hit]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(run))) as pool:
        results = list(pool.map(one, run))
    # A browser load per refused engine is slow and needs the profile; skip it
    # when an HTTP web engine (Brave) already answered.
    web_ok = any(n in WEB and not err and hits for n, hits, err in results)
    for n, hits, err in results:
        if err and browser_fallback and n in BROWSER_FALLBACK and not web_ok:
            try:
                hits, err = SOURCES[n](query, recency, limit, browser=True), None
                res.notes.append(f"{n} refused HTTP, answered in the browser")
            except Exception as exc:
                err = f"{err[:60]}; browser: {type(exc).__name__}: {exc}"
        health.record(n, err is None, err or "")
        if err:
            res.notes.append(f"{n} failed: {err[:140]}")
        else:
            per[n] = hits
            res.used.append(n)

    seen: set[str] = set()
    lists = [per[n] for n in run if n in per]
    for i in range(max((len(l) for l in lists), default=0)):
        for l in lists:
            if i < len(l):
                h = l[i]
                if not h.url or _is_google_serp(h.url):
                    continue
                k = _norm(h.url)
                if k not in seen:
                    seen.add(k)
                    res.hits.append(h)
    res.hits = demote_listicles(res.hits)
    return res


#: Crude routing until Phase 5's planner replaces it: which open APIs fit.
_ROUTES = [
    (r"\b(paper|papers|arxiv|research|study|studies)\b", ["arxiv", "openalex"]),
    (r"\b(sec|filing|filings|10-k|10-q|8-k|annual report)\b", ["edgar"]),
    (r"\b(github|repo|repos|open[- ]source|release|releases|library)\b", ["github"]),
    (r"\b(best|recommend|vs|review|reviews|worth)\b", ["reddit"]),
    (r"\b(ship|shipped|launch|launched|announce|announced|news|this week)\b", ["hn"]),
]


def pick_sources(question: str) -> list[str]:
    q = question.lower()
    extra = [s for rx, srcs in _ROUTES if re.search(rx, q) for s in srcs]
    return list(dict.fromkeys(extra + WEB + ["wikipedia"]))


# ---------------------------------------------------------------- L0 / feeds

_ATOM = "{http://www.w3.org/2005/Atom}"


def read_feed_xml(xml: str, source: str = "feed") -> list[Hit]:
    """RSS 2.0 or Atom -> Hits, newest first as the feed orders them."""
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError:
        return []
    hits = []
    for it in root.iter("item"):                                  # RSS
        hits.append(Hit((it.findtext("link") or "").strip(), _text(it.findtext("title") or ""),
                        _text(it.findtext("description") or "")[:200], source,
                        _date(it.findtext("pubDate"))))
    for e in root.iter(_ATOM + "entry"):                          # Atom
        link = ""
        for l in e.findall(_ATOM + "link"):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href", ""); break
        hits.append(Hit(link, _text(e.findtext(_ATOM + "title") or ""),
                        _text(e.findtext(_ATOM + "summary") or e.findtext(_ATOM + "content") or "")[:200],
                        source, _date(e.findtext(_ATOM + "published") or e.findtext(_ATOM + "updated"))))
    return hits


def _date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return None


def read_feed(url: str, source: str = "feed") -> list[Hit]:
    r = _get(url)
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    return read_feed_xml(r.text, source)


_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/blog/rss.xml")


def discover_feeds(site_url: str) -> list[str]:
    """Feeds a site advertises in <link rel=alternate>, else common paths that answer."""
    r = _get(site_url)
    found = [urljoin(site_url, unescape(m.group(1))) for m in re.finditer(
        r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*href="([^"]+)"', r.text or "")]
    found += [urljoin(site_url, unescape(m.group(1))) for m in re.finditer(
        r'<link[^>]+href="([^"]+)"[^>]*type="application/(?:rss|atom)\+xml"', r.text or "")]
    if found:
        return list(dict.fromkeys(found))
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    for p in _FEED_PATHS:
        try:
            rr = _get(base + p)
        except SourceError:
            continue
        if rr.status_code == 200 and read_feed_xml(rr.text):
            return [base + p]
    return []


def sitemap_urls(site_url: str, contains: str = "", limit: int = 200) -> list[str]:
    """URLs from robots.txt's sitemaps (one level of sitemap index followed)."""
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    robots = _get(base + "/robots.txt")
    maps = re.findall(r"(?im)^sitemap:\s*(\S+)", robots.text or "") or [base + "/sitemap.xml"]
    out: list[str] = []
    for sm in maps:
        try:
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", _get(sm).text or "")
        except SourceError:
            continue
        subs = [l for l in locs if l.endswith(".xml")]
        pages = [l for l in locs if not l.endswith(".xml")]
        for s in subs[:10]:
            if len(pages) >= limit * 3:
                break
            try:
                pages += [l for l in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", _get(s).text or "")
                          if not l.endswith(".xml")]
            except SourceError:
                continue
        out += [unescape(p) for p in pages if contains in p]
        if len(out) >= limit:
            break
    return out[:limit]


def jsonld(html: str) -> list[dict]:
    """Every JSON-LD object on a page, @graph flattened. Bad blocks are skipped."""
    out: list[dict] = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html or "", re.S | re.I):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and isinstance(x.get("@graph"), list):
                out += [g for g in x["@graph"] if isinstance(g, dict)]
            elif isinstance(x, dict):
                out.append(x)
    return out


# ---------------------------------------------------------- archive fallback

def wayback(url: str) -> Optional[str]:
    """Closest Wayback Machine snapshot URL, or None."""
    try:
        d = _json(f"https://archive.org/wayback/available?url={quote_plus(url)}")
    except SourceError:
        return None
    snap = (d.get("archived_snapshots") or {}).get("closest") or {}
    return snap.get("url") if snap.get("available") else None


def fetch_or_archive(url: str, allow_browser: bool = True):
    """fetcher.fetch, and if the live page is blocked, the archived copy.

    Returns (Result, archived_url_or_None). A 404 is not retried from the
    archive: the URL is wrong, not blocked."""
    from fetcher import fetch
    r = fetch(url, allow_browser=allow_browser)
    if r.ok or r.block is BlockType.NOT_FOUND:
        return r, None
    snap = wayback(url)
    if not snap:
        return r, None
    a = fetch(snap, allow_browser=False)
    return (a, snap) if a.ok else (r, None)


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "n8n release notes"
    res = search(q, pick_sources(q))
    for h in res.hits:
        print(f"{h.source:<10} {h.date or '':<10}  {h.url}\n{'':<22}{h.title[:90]}")
    print("used:", ", ".join(res.used))
    for n in res.notes:
        print("note:", n)
