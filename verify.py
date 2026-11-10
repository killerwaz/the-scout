"""verify.py - shows you what the fetcher actually got back.

`ok` means "this looked like content". It does not mean "this was the content
you wanted". Run this on anything the probe called ok, and read the text.

    python verify.py https://x.com/anthropicai
"""

import sys

from fetcher import Budget, default_ladder
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def to_text(html: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return _WS.sub(" ", _TAG.sub(" ", body)).strip()


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print(__doc__)
        return
    with default_ladder() as ladder:
        for url in urls:
            r = ladder.fetch(url, Budget(max_seconds=45))
            text = to_text(r.html)
            print("=" * 76)
            print(f"{url}")
            print(f"  verdict : {r.block.value}   tier={r.tier.name}   status={r.status}")
            print(f"  html    : {len(r.html):,} bytes")
            print(f"  text    : {len(text):,} chars of visible text")
            print("-" * 76)
            print(text[:900] if text else "(no visible text)")
            print()
            print("  ^ Is that the content you wanted, or a login pitch and a nav bar?")
            print()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    main()
