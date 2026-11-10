"""Run the 10 Phase 2 questions through search.py and write a results file to judge.

    python tests/run_questions.py        -> tests/results-YYYY-MM-DD.md

The query plans below are hand-written: turning a question into queries is the
Phase 5 planner's job. This tests discovery (which pages come back), not planning.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import search  # noqa: E402
from search import Hit  # noqa: E402

WEB = ["ddg", "bing", "mojeek"]

# (question, [(query, sources, recency)], [feed urls], [(site, sitemap filter)])
PLANS = [
    ("What did n8n ship in the last 7 days?",
     [("n8n release", WEB + ["hn"], "w"), ("n8n new features", WEB, "w")],
     ["https://github.com/n8n-io/n8n/releases.atom", "https://blog.n8n.io/rss/"], []),
    ("What did Anthropic announce this week?",
     [("Anthropic announces", WEB + ["hn"], "w"), ("site:anthropic.com/news", ["ddg", "bing"], "w")],
     [], []),
    ("Best noise-cancelling headphones under $300, with reasons.",
     [("best noise cancelling headphones under $300", WEB, "y"),
      ("site:reddit.com noise cancelling headphones under 300", ["ddg", "bing"], "y")],
     [], [("https://www.rtings.com/", "/headphones/reviews/best/")]),
    ("Best budget mechanical keyboard for programming in 2026.",
     [("best budget mechanical keyboard for programming 2026", WEB, "y"),
      # no recency: the good r/MechanicalKeyboards threads are older than a year
      ("site:reddit.com/r/MechanicalKeyboards budget keyboard programming", ["ddg", "bing"], None)],
     [], [("https://www.rtings.com/", "/keyboard/reviews/best/")]),
    ("Every 2-bed rental in Austin listed on Zillow.",
     [("site:zillow.com Austin TX 2 bedroom apartments for rent", ["ddg", "bing"], None),
      ("2 bedroom apartments for rent Austin TX", WEB, None)],
     [], []),
    ("Current open-source LLMs under 15B parameters with their licenses.",
     [("open weight LLM under 15B parameters license 2026", WEB, "y"),
      ("site:huggingface.co 8B instruct model license", ["ddg", "bing"], "y"),
      ("open source llm 7b", ["github"], "m")],
     [], []),
    ("What has Anthropic posted on X this month?",
     [("site:x.com AnthropicAI", ["ddg", "bing"], "m"), ("AnthropicAI tweet", WEB + ["hn"], "m")],
     [], []),
    ("Who are n8n's founders and what have they said publicly in 2026?",
     [("n8n founder", ["wikipedia"], None), ("n8n founder CEO interview 2026", WEB, "y"),
      ("n8n Jan Oberhauser", WEB + ["hn"], "y")],
     [], []),
    ("What are the latest SEC filings for NVIDIA?",
     [("NVIDIA", ["edgar"], None), ("NVIDIA 10-Q filing 2026", ["ddg", "bing"], "m")],
     [], []),
    ("Recent arXiv papers on browser agents and bot detection.",
     [("browser agents bot detection", ["arxiv", "openalex"], "y"),
      ("arxiv web agent bot detection 2026", ["ddg", "bing"], "y")],
     [], []),
]


def run(question, queries, feeds, sitemaps, health):
    lists, used, notes = [], set(), []
    for q, srcs, rec in queries:
        res = search.search(q, srcs, recency=rec, limit=10, health=health)
        lists.append(res.hits)
        used.update(res.used)
        notes += [f"`{q}`: {n}" for n in res.notes]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat()
    for f in feeds:
        try:
            hits = [h for h in search.read_feed(f, "feed") if not h.date or h.date >= cutoff]
            lists.append(hits); used.add("feed")
        except Exception as exc:
            notes.append(f"feed {f}: {exc}")
    for site, filt in sitemaps:
        try:
            urls = search.sitemap_urls(site, contains=filt, limit=10)
            lists.append([Hit(u, source="sitemap") for u in urls]); used.add("sitemap")
        except Exception as exc:
            notes.append(f"sitemap {site}: {exc}")
    seen, merged = set(), []
    for i in range(max((len(l) for l in lists), default=0)):
        for l in lists:
            if i < len(l) and search._norm(l[i].url) not in seen:
                seen.add(search._norm(l[i].url)); merged.append(l[i])
    return search.demote_listicles(merged), sorted(used), notes


def main():
    # --only 4,7 reruns just those questions into results-<date>-q4-7.md, so a
    # marked full-run file is never overwritten.
    only = set()
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    health = search.Health()
    out = [f"# Phase 2 run — {date.today().isoformat()}", "",
           "Top 10 links per question (merged across sources, each source's own order kept). "
           "Mark each `y`/`n` in the **Rel?** column: y = you'd click it (on topic, right time "
           "window, not spam/SEO). Target: ≥5 `y` per question.", ""]
    for n, (question, queries, feeds, sitemaps) in enumerate(PLANS, 1):
        if only and n not in only:
            continue
        hits, used, notes = run(question, queries, feeds, sitemaps, health)
        print(f"Q{n}: {len(hits)} links from {', '.join(used)}", flush=True)
        out += [f"## Q{n}. {question}", "", f"Sources that answered: {', '.join(used) or 'none'}  ",
                f"Links found: {len(hits)}", "", "| # | Rel? | Source | Date | Link |", "|---|---|---|---|---|"]
        for i, h in enumerate(hits[:10], 1):
            title = (h.title or h.url).replace("|", "/")[:80]
            out.append(f"| {i} |  | {h.source} | {h.date or ''} | [{title}]({h.url}) |")
        if notes:
            out += ["", "<details><summary>Source problems</summary>", ""] + [f"- {x}" for x in notes] + ["", "</details>"]
        out.append("")
    tag = ("-q" + "-".join(str(n) for n in sorted(only))) if only else ""
    path = ROOT / "tests" / f"results-{date.today().isoformat()}{tag}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
