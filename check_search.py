"""check_search.py - offline self-test for search.py. No network.  python check_search.py"""
import tempfile
from pathlib import Path

import search
from fetcher import BlockType
from search import Health, Hit, SourceError

fails = []


def t(label, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        fails.append(label)


# 1. Parsers on markup shaped like the real pages (captured 2026-10-21).
DDG = '''<div class="result results_links web-result "><div class="links_main links_deep result__body">
<h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.n8n.io%2Fchangelog%2Frelease%2Dnotes&amp;rut=x">Release notes | n8n Docs</a></h2>
<a class="result__snippet" href="#">Latest <b>n8n</b> release notes</a></div></div>''' + "filler text " * 100
search._html = lambda url: (DDG, BlockType.OK)
h = search.ddg("n8n release notes")
t("ddg: unwraps the redirect to the real URL", h and h[0].url == "https://docs.n8n.io/changelog/release-notes")

BING = '''<li class="b_algo" data-id><h2><a href="https://www.bing.com/ck/a?!&amp;u=a1aHR0cHM6Ly9naXRodWIuY29tL24
4bi1pby9uOG4&amp;ntb=1">n8n-io/n8n</a></h2><p class="b_lineclamp2">n8n workflow automation</p></li>'''.replace("\n", "")
search._html = lambda url: (BING, BlockType.CAPTCHA)   # real Bing pages embed a cf-turnstile hook
h = search.bing("n8n github")
t("bing: results count even when the page also trips the captcha marker", h and h[0].url == "https://github.com/n8n-io/n8n")

search._html = lambda url: ("<html>JavaScript is required to complete this challenge</html>", BlockType.SOFT_BLOCK)
try:
    search.mojeek("anything"); t("mojeek: a challenge page with no results is a failure", False)
except SourceError:
    t("mojeek: a challenge page with no results is a failure", True)

POISON = ('<li class="b_algo"><h2><a href="https://www.reddit.com/r/wikipedia/comments/gcpj4/">'
          'Wikipedia vandalism</a></h2><p>reddit thread</p></li>')
search._html = lambda url: (POISON, BlockType.OK)
try:
    search.bing("n8n release notes"); t("bing: unrelated results are treated as poisoned", False)
except SourceError:
    t("bing: unrelated results are treated as poisoned", True)

MIXED = ('<li class="b_algo"><h2><a href="https://www.rtings.com/keyboard/reviews/best/mechanical">'
         'Best mechanical keyboards</a></h2><p>budget picks</p></li>'
         '<li class="b_algo"><h2><a href="https://www.nytimes.com/books/best-sellers/">'
         'Best Sellers - Books</a></h2><p>weekly list</p></li>'
         '<li class="b_algo"><h2><a href="/videos/riverview/relatedvideo?q=mechanical+keyboard">'
         'mechanical keyboard video</a></h2></li>')
search._html = lambda url: (MIXED, BlockType.OK)
h = [x.url for x in search.bing("budget mechanical keyboard")]
t("bing: drops single unrelated links from an otherwise good list", "https://www.nytimes.com/books/best-sellers/" not in h)
t("bing: drops relative links (/videos/...)", h == ["https://www.rtings.com/keyboard/reviews/best/mechanical"])

rel = search._relevant
t("relevance: a shared year alone doesn't count",
  not rel("arxiv web agent bot detection 2026", Hit("https://travel.com/x", "20 Best Summer Vacation Destinations 2026")))
t("relevance: site: operator parts don't count",
  not rel("site:x.com AnthropicAI tweet", Hit("https://howtogeek.com/dark-mode", "How to enable dark mode", "site com")))
t("relevance: plurals match", rel("budget mechanical keyboard", Hit("https://r.com/k", "Best mechanical keyboards")))
t("relevance: long queries need two matching words",
  not rel("best budget mechanical keyboard programming", Hit("https://cnet.com/best-phone/", "Best Phones in 2026, budget picks")))
t("relevance: short queries need one", rel("n8n release", Hit("https://lowcode.agency/n8n-features", "n8n Features")))

L = [Hit("https://seo.com/a", "8 Best Budget Mechanical Keyboards for Coding (September 2026) Quality ..."),
     Hit("https://www.rtings.com/k", "The 4 Best Budget Mechanical Keyboards of 2026 - RTINGS.com"),
     Hit("https://seo2.com/b", "12 Best Noise Cancelling Headphones Under 300 (October 2026) Tested Reviews"),
     Hit("https://www.reddit.com/r/MechanicalKeyboards/comments/x/", "Budget keyboard for programming")]
d = [x.url for x in search.demote_listicles(L)]
t("listicles: '(Month YYYY)' best-of lists move to the end, order otherwise kept",
  d == ["https://www.rtings.com/k", "https://www.reddit.com/r/MechanicalKeyboards/comments/x/",
        "https://seo.com/a", "https://seo2.com/b"])

# 2. Feeds and JSON-LD.
RSS = """<?xml version="1.0"?><rss><channel><item><title>v2.45</title><link>https://ex.com/a</link>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>n8n@2.45.0</title>
<link rel="alternate" href="https://github.com/n8n-io/n8n/releases/tag/n8n%402.45.0"/>
<updated>2026-10-19T12:00:00Z</updated></entry></feed>"""
r, a = search.read_feed_xml(RSS), search.read_feed_xml(ATOM)
t("rss: link + date", r and r[0].url == "https://ex.com/a" and r[0].date == "2026-10-20")
t("atom: link + date", a and a[0].url.endswith("n8n%402.45.0") and a[0].date == "2026-10-19")
LD = '''<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","headline":"H","datePublished":"2026-10-20"}]}</script>
<script type="application/ld+json">{not json</script>'''
j = search.jsonld(LD)
t("json-ld: @graph flattened, broken block skipped", len(j) == 1 and j[0]["headline"] == "H")

# 3. Merge: round-robin, de-duplicated, Google results pages dropped.
search.SOURCES["fa"] = lambda q, r, l: [Hit("https://a.com/1", source="fa"), Hit("https://www.google.com/search?q=x", source="fa")]
search.SOURCES["fb"] = lambda q, r, l: [Hit("https://www.a.com/1/", source="fb"), Hit("https://b.com/2", source="fb")]
tmp = Path(tempfile.mkdtemp())
res = search.search("q", ["fa", "fb"], health=Health(tmp / "h.json"))
t("merge: dedupes a.com/1 across sources", [x.url for x in res.hits] == ["https://a.com/1", "https://b.com/2"])
t("merge: never returns a Google results page", not any("google." in x.url for x in res.hits))

# 4. Health: 3 failures in a row -> skipped for an hour, and the answer says so.
def broken(q, r, l):
    raise SourceError("captcha")
search.SOURCES["fc"] = broken
hp = Health(tmp / "h2.json")
for _ in range(3):
    search.search("q", ["fc", "fa"], health=hp)
res = search.search("q", ["fc", "fa"], health=hp)
t("health: skipped after 3 failures, and noted", "fc" not in res.used and any("fc unavailable" in n for n in res.notes))
t("health: survives a restart (persisted)", Health(tmp / "h2.json").skipped_until("fc") is not None)
hp.record("fc", True)
t("health: one success clears it", hp.skipped_until("fc") is None)

# 4b. An engine that refuses HTTP gets one retry in the browser, and counts as healthy if that works.
def picky(q, r, l, browser=False):
    if not browser:
        raise SourceError("HTTP 202 challenge")
    return [Hit("https://c.com/3", source="fd")]
search.SOURCES["fd"] = picky
search.BROWSER_FALLBACK.add("fd")
hp3 = Health(tmp / "h3.json")
res = search.search("q", ["fd"], health=hp3)
t("browser fallback: refused over HTTP, answered in the browser",
  [x.url for x in res.hits] == ["https://c.com/3"] and any("answered in the browser" in n for n in res.notes))
t("browser fallback: a browser answer keeps the source healthy", hp3.state["fd"]["fails"] == 0)
res = search.search("q", ["fd"], health=hp3, browser_fallback=False)
t("browser fallback: can be switched off", res.hits == [] and hp3.state["fd"]["fails"] == 1)

# 4c. arXiv: abs/…v1, abs/… and pdf/… are one paper; different papers stay apart.
n = search._norm
t("dedupe: arXiv versions and PDF collapse to one paper",
  n("https://arxiv.org/abs/2606.30119v1") == n("https://arxiv.org/pdf/2606.30119") == n("https://arxiv.org/abs/2606.30119")
  == n("https://arxiv.org/html/2606.30119v1"))
t("dedupe: different arXiv papers stay separate", n("https://arxiv.org/abs/2606.30119") != n("https://arxiv.org/abs/2607.26935"))

# 5. Routing (stand-in until Phase 5's planner).
t("routing: papers -> arxiv/openalex", {"arxiv", "openalex"} <= set(search.pick_sources("Recent arXiv papers on browser agents")))
t("routing: SEC filings -> edgar", "edgar" in search.pick_sources("latest SEC filings for NVIDIA"))

# --- Brave Search API (open question 9: engines unreliable from this IP) ------------
BRAVE_JSON = {"web": {"results": [
    {"url": "https://www.rtings.com/x", "title": "<strong>Best</strong> headphones", "description": "Tested <b>30</b> pairs",
     "page_age": "2026-10-01T00:00:00"},
    {"url": "https://example.com/y", "title": "Y", "description": ""}]}}
got = {}


class _R:
    status_code = 200

    def json(self):
        return BRAVE_JSON


def fake_brave_get(url, headers):
    got["url"], got["headers"] = url, headers
    return _R()


real_get, real_key = search._brave_get, search._brave_key
search._brave_get, search._brave_key = fake_brave_get, lambda: "k123"
hs = search.brave("best headphones", "w", 10)
t("brave: parses url, title, snippet (tags stripped) and date",
  [(h.url, h.title, h.snippet, h.date, h.source) for h in hs][0]
  == ("https://www.rtings.com/x", "Best headphones", "Tested 30 pairs", "2026-10-01", "brave"))
t("brave: sends the key in the header, never the URL",
  got["headers"].get("X-Subscription-Token") == "k123" and "k123" not in got["url"])
t("brave: recency maps to freshness", "freshness=pw" in got["url"])
search._brave_key = lambda: None
try:
    search.brave("q", None, 10); ok = False
except SourceError as e:
    ok = "BRAVE_API_KEY" in str(e)
t("brave: a missing key is a clear SourceError", ok)
search._brave_key = lambda: "k123"

calls = []
search.SOURCES["brave"] = lambda q, r, l: [Hit("https://brave.com/r", source="brave")]
search.SOURCES["fw"] = lambda q, r, l, browser=False: calls.append(browser) or (_ for _ in ()).throw(SourceError("HTTP 202"))
saved_web = list(search.WEB)
search.WEB[:] = ["brave", "fw"]
search.BROWSER_FALLBACK.add("fw")
res = search.search("q", ["fw"], health=Health(tmp / "h5.json"))
t("brave: added first whenever a web engine is asked for", res.used[:1] == ["brave"])
t("brave: when it answered, a refused engine doesn't open the browser", calls == [False])
search.SOURCES["brave"] = lambda q, r, l: (_ for _ in ()).throw(SourceError("HTTP 429"))
calls.clear()
search.search("q", ["fw"], health=Health(tmp / "h6.json"))
t("brave: when it fails, the browser fallback still runs", calls == [False, True])
search.WEB[:] = saved_web
search.SOURCES["brave"] = search.brave
search._brave_get, search._brave_key = real_get, real_key
t("brave: is the first web engine", search.WEB[0] == "brave")

print()
print("ALL GOOD - search.py is working." if not fails else f"{len(fails)} FAILED: {fails}")

import sys
sys.exit(1 if fails else 0)
