"""probe.py - point fetcher at your real targets and see which tier each needs.

Put the URLs you actually care about in TARGETS below, then:

    python probe.py

Each line tells you: what happened, which rung it took, how big the page was,
and how long it took. That table is your answer to "do I even need a browser".
"""

import time

from fetcher import Budget, default_ladder

TARGETS = [
    # Phase 1 list (PLAN.md §6), approved 2026-10-21. Deep pages, not homepages.
    # news
    "https://www.anthropic.com/news",
    "https://docs.claude.com/en/release-notes/overview",
    "https://github.com/n8n-io/n8n/releases",
    "https://docs.n8n.io/release-notes/",
    "https://community.n8n.io/c/announcements/",
    "https://hn.algolia.com/api/v1/search_by_date?query=anthropic",
    # decision research
    "https://www.rtings.com/headphones/reviews/best/by-feature/noise-cancelling",  # drafted URL 404d; this one is from rtings sitemap
    "https://www.nytimes.com/wirecutter/reviews/best-noise-cancelling-headphones/",
    "https://www.reddit.com/r/headphones/search/?q=noise+cancelling+under+300",
    "https://www.amazon.com/s?k=noise+cancelling+headphones",
    "https://www.bestbuy.com/site/searchpage.jsp?st=noise+cancelling+headphones",
    # pull data
    "https://www.zillow.com/austin-tx/rentals/",
    "https://www.apartments.com/austin-tx/2-bedrooms/",
    "https://www.redfin.com/city/30818/TX/Austin/apartments-for-rent",
    "https://austin.craigslist.org/search/apa?min_bedrooms=2&max_bedrooms=2",
    # people & companies
    "https://x.com/AnthropicAI",
    "https://www.linkedin.com/company/anthropicresearch/posts/",
    "https://www.reddit.com/r/ClaudeAI/new.json",
    "https://www.youtube.com/@anthropic-ai/videos",
]


def main() -> None:
    print(f"{'RESULT':<13} {'TIER':<8} {'BYTES':>9} {'MS':>7}  URL")
    print("-" * 78)
    counts: dict[str, int] = {}
    with default_ladder() as ladder:
        for url in TARGETS:
            t0 = time.monotonic()
            r = ladder.fetch(url, Budget(max_seconds=45))
            ms = int((time.monotonic() - t0) * 1000)
            counts[r.block.value] = counts.get(r.block.value, 0) + 1
            print(f"{r.block.value:<13} {r.tier.name:<8} {len(r.html):>9} {ms:>7}  {url}")

    print("-" * 78)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()
    print("ok at tier HTTP   -> no browser needed. Cheapest path works.")
    print("ok at tier BROWSER-> the browser earned its keep on this site.")
    print("challenge/hard_block at BROWSER -> this target needs a rung you")
    print("                     haven't built yet (tier 3+), or a different IP.")
    print("captcha           -> hand to a human. Do not automate past this.")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    main()
