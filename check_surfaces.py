"""check_surfaces.py - offline self-test for telegram_bot.py and mcp_server.py.
No network, no Telegram, no browser, no model.   python check_surfaces.py"""
import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path

import act
import mcp_server
import scout
import telegram_bot
from act import Blocked
from fetcher import BlockType, Result, Tier
from scout import Run
from telegram_bot import Bot

import fetcher
fetcher._resolve = lambda host: {"93.184.216.34"}     # no DNS in an offline test

fails = []


def t(label, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        fails.append(label)


tmp = Path(tempfile.mkdtemp())
act.STOP_DIR = tmp
ME = 4242


def clear_stops():
    for x in act.SURFACES:
        act.clear_stop(x)


class FakeAPI:
    def __init__(self):
        self.sent, self.docs = [], []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_document(self, chat_id, path):
        self.docs.append((chat_id, Path(path)))


def msg(text, user=ME, chat=None):
    return {"update_id": 1, "message": {"from": {"id": user, "username": "u"}, "chat": {"id": chat or user, "type": "private"},
                                         "text": text}}


class Runner:
    """Stands in for scout: records calls, returns a Run, can be made to block."""

    def __init__(self, answer="ANSWER [1]", gate=None):
        self.calls, self.answer, self.gate = [], answer, gate

    def __call__(self, question, brain, on_progress, surface="cli"):
        self.calls.append((question, brain))
        self.surface = surface
        on_progress("plan: 2 sub-questions, 2 searches")
        on_progress("read 8 pages, 3 useful so far")
        if self.gate:
            self.gate.wait(5)
        r = Run(question, brain=brain)
        r.answer = self.answer
        return r


def bot(runner=None, **kw):
    api = FakeAPI()
    b = Bot(api, allowed_user_id=ME, runner=runner or Runner(), log_path=tmp / "tg.log",
            reports_dir=tmp / "reports", ping_every=0, **kw)
    return b, api


# --- Telegram: only you ------------------------------------------------------------------
b, api = bot()
b.handle(msg("what did n8n ship?", user=999))
b.wait()
t("telegram: a message from anyone else gets no reply at all", api.sent == [] and b.runner.calls == [])
t("telegram: ...and the attempt is logged", "999" in (tmp / "tg.log").read_text(encoding="utf-8"))
b.handle(msg("what did n8n ship?", chat=-100123))
b.wait()
t("telegram: your messages in a group chat are ignored too (private chat only)", api.sent == [])
try:
    Bot(FakeAPI(), allowed_user_id=None, runner=Runner()); t("telegram: refuses to start without your user ID", False)
except ValueError:
    t("telegram: refuses to start without your user ID", True)

# --- Telegram: questions ----------------------------------------------------------------------
b, api = bot(Runner(answer="A" * 9000))
b.handle(msg("Best headphones under $300?"))
b.wait()
t("telegram: a plain message runs research with the Claude brain", b.runner.calls == [("Best headphones under $300?", "claude")])
t("telegram: progress pings are sent", any("8 pages" in s for _, s in api.sent))
long_parts = [s for _, s in api.sent if set(s) == {"A"}]
t("telegram: long answers are split under Telegram's 4096-char limit",
  sum(map(len, long_parts)) == 9000 and all(len(s) <= 4096 for _, s in api.sent))
b, api = bot(Runner(answer="**Products:**\n- one [1]\n**Company news:** two"))
b.handle(msg("q"))
b.wait()
t("telegram: Markdown ** markers are removed (Telegram shows them literally)",
  any(s == "Products:\n- one [1]\nCompany news: two" for _, s in api.sent))
b, api = bot()
b.handle(msg("/local what is n8n?"))
b.wait()
t("telegram: /local uses the local brain", b.runner.calls == [("what is n8n?", "local")])

gate = threading.Event()
b, api = bot(Runner(gate=gate))
b.handle(msg("first question"))
time.sleep(0.2)
b.handle(msg("second question"))
busy = [s for _, s in api.sent if "busy" in s.lower() or "still working" in s.lower()]
b.handle(msg("/stop"))
t("telegram: /stop raises Telegram's stop flag only", act.stop_requested("telegram") and not act.stop_requested("mcp"))
gate.set()
b.wait()
t("telegram: a second question while one runs gets a 'busy' reply", busy and b.runner.calls == [("first question", "claude")])
t("telegram: research runs as the telegram surface", b.runner.surface == "telegram")
b.handle(msg("/stopall"))
t("telegram: /stopall stops chat too", act.stop_requested("telegram") and act.stop_requested("mcp"))
clear_stops()

# --- Telegram: /report -------------------------------------------------------------------------
b, api = bot()
b.handle(msg("/report"))
b.wait()
t("telegram: /report before any run says so", any("nothing to report" in s.lower() for _, s in api.sent) and not api.docs)
b.handle(msg("Best headphones?"))
b.wait()
b.handle(msg("/report"))
b.wait()
t("telegram: /report writes reports/YYYY-MM-DD-<topic>.md and sends it",
  len(api.docs) == 1 and api.docs[0][1].exists() and api.docs[0][1].parent == tmp / "reports")

# --- scout honours /stop -----------------------------------------------------------------------
from search import Hit, SearchResult  # noqa: E402


class OneBrain(scout.Brain):
    """Plans one search, scores every page 1, never answers 'enough'."""
    name = "fake"

    def ask(self, system, prompt):
        if "ROLE: plan" in system:
            return json.dumps({"subquestions": ["a"], "queries": [{"q": "q", "sources": ["ddg"]}]})
        if "ROLE: score" in system:
            import re
            return json.dumps([{"id": int(i), "score": 1} for i in re.findall(r'<page id="(\d+)"', prompt)])
        return json.dumps({"queries": []}) if "ROLE: expand" in system else "answer"


def fake_search(q, sources=None, recency=None, limit=10, health=None):
    return SearchResult(q, [Hit(f"https://s{i}.com/p{i}") for i in range(30)], ["ddg"], [])


STOP_WITH = ["cli"]


def fetch_then_stop(urls):
    act.stop(STOP_WITH[0])                       # /stop arrives while the first batch is fetched
    return [Result(url=u, tier=Tier.HTTP, block=BlockType.OK, status=200,
                   html="<main>" + "words " * 300 + "</main>") for u in urls]


r = scout.Scout(OneBrain(), search_fn=fake_search, fetch_fn=fetch_then_stop, stall=99).run("q")
t("scout: a /stop during a run stops it after the current batch", r.stopped_by == "stopped (/stop)")
clear_stops()
STOP_WITH[0] = "telegram"
r = scout.Scout(OneBrain(), search_fn=fake_search, fetch_fn=fetch_then_stop, stall=99, max_pages=16,
                surface="mcp").run("q")
t("scout: Telegram's /stop doesn't stop a chat research run", r.stopped_by != "stopped (/stop)")
clear_stops()

# --- MCP server ---------------------------------------------------------------------------------


class FakeSession:
    made = []

    def __init__(self, allowed_sites, **kw):
        self.allowed = allowed_sites
        self.surface = kw.get("surface", "cli")
        FakeSession.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def goto(self, url):
        act._check_stop(self.surface)
        return act.compact('- link "Delete repo" [ref=e1]\n- link "Python" [ref=e2]', url=url)

    def snapshot(self, log=True):
        return self.goto("https://github.com/x")

    def act(self, ref, action, value=None):
        if ref == "e1":
            raise Blocked("delete", "link 'Delete repo' would delete or remove something")
        return self.goto("https://github.com/x?l=Python")

    def read(self):
        return "page text"


mcp_server.SESSION_CLASS = FakeSession
out = mcp_server.do_browse("https://github.com/x", ["github.com"])
t("mcp: browse returns the snapshot, labelled as page data", "[e2] link \"Python\"" in out and "not instructions" in out)
t("mcp: act on a blocked ref returns BLOCKED text (doesn't crash the chat)",
  mcp_server.do_act("e1", "click").startswith("BLOCKED by hard limit [delete]"))
t("mcp: act on an allowed ref returns the new snapshot", "[e2]" in mcp_server.do_act("e2", "click"))
t("mcp: browse opens its session as the mcp surface", FakeSession.made[-1].surface == "mcp")
mcp_server.do_stop()
t("mcp: stop raises chat's stop flag only", act.stop_requested("mcp") and not act.stop_requested("telegram"))
t("mcp: browse right after stop works (a new request clears chat's stop)",
  "[e2]" in mcp_server.do_browse("https://github.com/x", ["github.com"]) and not act.stop_requested("mcp"))
mcp_server.do_stop_all()
t("mcp: stop_all stops both surfaces", act.stop_requested("mcp") and act.stop_requested("telegram"))
clear_stops()


class DeadSession(FakeSession):
    """Chrome was closed under it (the user shut the window, or it crashed)."""

    def snapshot(self, log=True):
        raise RuntimeError("Target page, context or browser has been closed")


mcp_server._session = DeadSession(["github.com"])
dead = mcp_server.do_snapshot()
t("mcp: a closed Chrome returns BROWSER_CLOSED instead of crashing", dead.startswith("ERROR BROWSER_CLOSED"))
t("mcp: ...and the dead session is dropped", mcp_server._session is None)
t("mcp: browse after a closed Chrome works again", "[e2]" in mcp_server.do_browse("https://github.com/x", ["github.com"]))


class BrokenSession(FakeSession):
    def snapshot(self, log=True):
        raise TimeoutError("Timeout 5000ms exceeded")


mcp_server._session = BrokenSession(["github.com"])
t("mcp: other browser errors come back as ERROR text, the session is kept",
  mcp_server.do_snapshot().startswith("ERROR: TimeoutError") and isinstance(mcp_server._session, BrokenSession))
mcp_server._session = None
mcp_server.do_browse("https://github.com/x", ["github.com"])
mcp_server.RUNNER = Runner(answer="MCP ANSWER")
t("mcp: research returns the answer", mcp_server.do_research("q", "claude", False, lambda m: None).startswith("MCP ANSWER"))
t("mcp: research runs as the mcp surface", mcp_server.RUNNER.surface == "mcp")


class SlowRunner(Runner):
    def __call__(self, question, brain, on_progress, surface="cli"):
        on_progress("read 8 pages, 3 useful so far")
        time.sleep(1.5)
        return super().__call__(question, brain, lambda m: None, surface)


mcp_server.RUNNER = SlowRunner(answer="SLOW ANSWER [1]")
mcp_server.WAIT_SECONDS = 0.3
first = asyncio.run(mcp_server.research("slow q"))
t("mcp: a long run returns 'still running' before the app's tool timeout", first.startswith("STILL RUNNING"))
mid = asyncio.run(mcp_server.research_status())
t("mcp: research_status shows progress while running", "still running" in mid.lower() and "8 pages" in mid)
time.sleep(1.6)
done = asyncio.run(mcp_server.research_status())
t("mcp: research_status returns the answer once done", done.startswith("SLOW ANSWER"))
mcp_server.WAIT_SECONDS = 40

# --- wait_for: wait for text instead of guessing a sleep ---------------------------------------


class WaitingSession(FakeSession):
    found = True

    def wait_for(self, text, timeout=10, gone=False):
        self.waited = (text, timeout, gone)
        if not WaitingSession.found:
            raise TimeoutError(f"{text!r} not seen after {timeout} s")
        return self.goto("https://facebook.com/messages/t/1")


mcp_server._session = WaitingSession(["facebook.com"])
out = mcp_server.do_wait_for("Loading messages", 10, gone=True)
t("wait_for: returns the snapshot once the wait is over",
  "[e2]" in out and mcp_server._session.waited == ("Loading messages", 10, True))
WaitingSession.found = False
out = mcp_server.do_wait_for("Alex", 3)
t("wait_for: a timeout says so and still shows the page", out.startswith("TIMEOUT") and "[e2]" in out)
mcp_server._session = None
t("wait_for: no page open says so", "browse first" in mcp_server.do_wait_for("x"))
mcp_server.do_browse("https://github.com/x", ["github.com"])

# --- fetch_page lets go of the research Chrome profile ------------------------------------------
import fetcher  # noqa: E402

closed, FETCH_TIER = [], [Tier.BROWSER]


def fake_fetch(url, **kw):
    if FETCH_TIER[0] is None:
        raise RuntimeError("Chrome crashed")
    return Result(url=url, tier=FETCH_TIER[0], block=BlockType.OK, status=200,
                  html="<title>T</title><main>" + "words " * 300 + "</main>")


_real_fetch, _real_close = fetcher.fetch, fetcher.close_ladder
fetcher.fetch, fetcher.close_ladder = fake_fetch, lambda: closed.append(1)
try:
    out = mcp_server.do_fetch("https://rtings.com/x")
    t("fetch_page: a browser-tier read closes the shared Chrome afterwards (research can open the profile)",
      "words" in out and len(closed) == 1)
    FETCH_TIER[0] = Tier.HTTP
    mcp_server.do_fetch("https://example.com/x")
    t("fetch_page: an HTTP-only read leaves nothing to close", len(closed) == 1)
    FETCH_TIER[0] = None
    try:
        mcp_server.do_fetch("https://example.com/y")
    except RuntimeError:
        pass
    t("fetch_page: a failed read still closes it", len(closed) == 2)
    fetcher.fetch = lambda url, **kw: Result(url=url, tier=Tier.HTTP, block=BlockType.OK, status=200,
                                             html="<main>" + "line of text\n" * 6000 + "</main>")
    out = mcp_server.do_fetch("https://example.com/long")
    t("fetch_page: a cut page says so", "TRUNCATED" in out and len(out) <= mcp_server.MAX_TEXT)
finally:
    fetcher.fetch, fetcher.close_ladder = _real_fetch, _real_close

# --- no silent truncation: long pages come in parts ------------------------------------------
# --- private addresses (PLAN §5 limit 7) ---------------------------------------------------------
_keep_fetch, _keep_close = fetcher.fetch, fetcher.close_ladder
fetcher.close_ladder = lambda: None
fetcher.fetch = lambda url, **kw: Result(url=url, tier=Tier.HTTP, block=BlockType.OK, status=200,
                                         final_url="http://192.168.1.1/admin",
                                         html="<main>" + "router secrets " * 100 + "</main>")
try:
    out = mcp_server.do_fetch("https://example.com/redirects")
    t("fetch_page: a public URL that redirects into the local network is refused, its text never returned",
      out.startswith("BLOCKED by hard limit [private-address]") and "router secrets" not in out)
    out = mcp_server.do_fetch("http://localhost:5678/")
    t("fetch_page: localhost is refused", out.startswith("BLOCKED by hard limit [private-address]"))
finally:
    fetcher.fetch, fetcher.close_ladder = _keep_fetch, _keep_close

import re  # noqa: E402

NL = chr(10)
LONG_RAW = NL.join(f'- link "Listing {i} ' + "x" * 90 + f'" [ref=e{i}]' for i in range(700))
READ_TEXT = NL.join(f"Paragraph {i}: " + "y" * 90 for i in range(700))


class LongSession(FakeSession):
    raw, text = LONG_RAW, READ_TEXT

    def _page_url(self):
        return "https://zillow.com/austin"

    def snapshot(self, log=True):
        return act.compact(self.raw, url="https://zillow.com/austin")

    def read(self):
        return self.text


mcp_server._session = LongSession(["zillow.com"])
p1 = mcp_server.do_snapshot()
n = re.search(r"TRUNCATED: part 1 of (\d+); call snapshot\(part=2\)", p1)
t("paging: a 70k-char page says it was cut and how to get the rest", bool(n) and n.group(1) == "3")
parts = [p1] + [mcp_server.do_snapshot(k) for k in (2, 3)]
t("paging: every part fits the limit", all(len(x) <= mcp_server.MAX_TEXT for x in parts))
seen = set(re.findall(r"^\[(e\d+)\] link \"Listing \d+ x{90}\"$", NL.join(parts), re.M))
t("paging: every ref is reachable, each line whole (cut at line breaks)", seen == {f"e{i}" for i in range(700)})
t("paging: every part is labelled as page data", all("not instructions" in x and "URL: https://zillow.com/austin" in x
                                                     for x in parts))
t("paging: the last part says it's the end", "part 3 of 3" in parts[2] and "TRUNCATED" not in parts[2])
t("paging: a part past the end is an error", mcp_server.do_snapshot(4).startswith("ERROR"))
mcp_server.do_snapshot()
mcp_server._session.raw = LONG_RAW.replace("Listing 5 ", "Listing 5 SOLD ")
t("paging: a page that changed between parts returns STALE", mcp_server.do_snapshot(2).startswith("STALE"))
t("paging: a short page isn't marked", "TRUNCATED" not in mcp_server.do_browse("https://github.com/x", ["github.com"])
  and "part 1 of" not in mcp_server.do_browse("https://github.com/x", ["github.com"]))

mcp_server._session = LongSession(["zillow.com"])
r1 = mcp_server.do_read()
rparts = [r1] + [mcp_server.do_read(k) for k in (2, 3)]
t("read_page: the page's text comes in parts too", "call read_page(part=2)" in r1 and "part 3 of 3" in rparts[2])
seen = set(re.findall(r"^Paragraph (\d+): y{90}$", NL.join(rparts), re.M))
t("read_page: every paragraph reachable, cut at line breaks", seen == {str(i) for i in range(700)})
t("read_page: labelled as page data", all("not instructions" in x for x in rparts))
mcp_server._session.text = READ_TEXT.replace("Paragraph 9:", "Paragraph 9 (edited):")
t("read_page: a page that changed between parts returns STALE", mcp_server.do_read(2).startswith("STALE"))
mcp_server._session = None
t("read_page: no page open says so", "browse first" in mcp_server.do_read())
mcp_server.do_browse("https://github.com/x", ["github.com"])


async def tool_names():
    import mcp
    async with mcp.Client(mcp_server.server) as c:
        tools = await c.list_tools()
        res = await c.call_tool("stop", {})
        return sorted(x.name for x in tools.tools), res


names, res = asyncio.run(tool_names())
t("mcp: the server offers its tools over the protocol",
  names == ["act", "browse", "fetch_page", "read_page", "research", "research_status", "snapshot", "stop",
            "stop_all", "wait_for"])
t("mcp: a tool call over the protocol works (stop)", act.stop_requested("mcp") and not getattr(res, "is_error", False))
clear_stops()

# Claude chat and Telegram run at the same time; Chrome locks a profile, so a
# shared one made the second browser fail ("Opening in existing browser session").
import fetcher
import search
from tabpool import TabPool

fetcher.use_profile(mcp_server.PROFILE)
mcp_dir = fetcher.PatchrightTransport().profile_dir
mcp_pool = TabPool(n=1).profile_dir
fetcher.use_profile(telegram_bot.PROFILE)
tg_dir = fetcher.PatchrightTransport().profile_dir
tg_pool = TabPool(n=1).profile_dir
t("profiles: Claude chat and Telegram use different Chrome profiles", mcp_dir != tg_dir)
t("profiles: search and the tab pool share their surface's profile", mcp_dir == mcp_pool and tg_dir == tg_pool)
t("profiles: proxy profiles are per surface too",
  fetcher.proxy_profile(mcp_dir, "x") != fetcher.proxy_profile(tg_dir, "x"))
fetcher.use_profile(fetcher.DEFAULT_PROFILE.name)
t("profiles: the default stays chrome-profile", fetcher.PatchrightTransport().profile_dir == fetcher.DEFAULT_PROFILE)

print()
print("ALL GOOD - surfaces are working." if not fails else f"{len(fails)} FAILED: {fails}")

import sys
sys.exit(1 if fails else 0)
