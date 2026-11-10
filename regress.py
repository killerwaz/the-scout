"""Regression fixtures built from the author's own live runs. Keep these forever."""
from fetcher import classify, BlockType

X_REAL = ("Anthropic (@AnthropicAI) / X Log in Sign up Anthropic 1,685 posts Anthropic @AnthropicAI "
 "We're an AI safety and research company. Talk to our AI assistant @ claudeai on claude.ai . "
 "anthropic.com Joined January 2021 2 Following 1.6M Followers Mention Follow Posts Posts Replies "
 "Reposts Media Log in or sign up for X See what's happening and join the conversation Continue with "
 "phone Continue with Apple Continue with Google or Log in with username or email Terms Privacy "
 "Cookies Accessibility Ads Info More 2026 X Corp. Anthropic @AnthropicAI Sep 1 New research: "
 "Training a Misaligned Reward Seeker What produces severe misalignment? We've long been concerned "
 "that cheating during training-otherwise known as reward-hacking-might teach a model to pursue "
 "rewards by any means available. To study this ") * 2

LI_REAL = ("Anthropic | LinkedIn Skip to main content LinkedIn Top Content People Learning Jobs Games "
 "Sign in Join now Anthropic Research Services Anthropic is an AI safety and research company working "
 "to build reliable, interpretable, and steerable AI systems. See jobs Follow Discover all 5,893 "
 "employees Report this company Overview Jobs About us Our first product is Claude, an AI assistant "
 "for tasks at any scale. Our research interests span natural language, human feedback, scaling laws, "
 "reinforcement learning, code generation, and interpretability. Company size 501-1,000 employees ") * 8

TRUE_WALL = "<html><body><h1>Sign in to continue</h1><p>New to LinkedIn? Join now.</p></body></html>"
SHELL     = '<html><body><div id="root"></div></body></html>'
ARTICLE   = "<html><body>" + "The central bank held rates steady today, citing inflation. " * 40 + "</body></html>"

# --- Phase 1 live runs, 2026-10-21 (PLAN.md §8, open question 6) ---
# docs.n8n.io: a real 270k-char docs page whose chat widget JSON says "just a moment".
N8N_DOCS = ("<html><body>" + "Release notes. n8n 2.45.0 adds node fixes and breaking changes. " * 600 +
 '<script>{"ai_chat_hold_message_1":"thinking it through…","ai_chat_hold_message_2":"just a moment…"}</script>'
 "</body></html>")
# rtings review: 200 OK, ~1,200 chars of nav, the review itself inside a 260KB script blob.
RTINGS_NAV = ("Logitech Zone Wired Review - RTINGS.com Best Headphones Noise Cancelling Wireless Earbuds "
 "Gaming Over-Ear Wireless Running Apple PC Gaming Tools Compare Results Table Tool Review List ") * 7
RTINGS = ("<html><body><nav>" + RTINGS_NAV + "</nav><script>window.__DATA__=" +
 '{"verdict":"Sound Profile Noise Isolation Frequency Response"}' * 4500 + "</script></body></html>")
# bestbuy.com from a Bangladesh IP: country picker, 760 chars, used to score as ok.
BESTBUY = ("<html><head><title>Best Buy International: Select your Country - Best Buy</title></head><body>"
 "English Français Español Hello! Choose a country. Canada United States Shopping in the U.S.? "
 "International customers can shop on www.bestbuy.com and have orders shipped to any U.S. address or "
 "U.S. store. See More Details Bonjour! Choisir un pays : Canada United States</body></html>")
# zillow PerimeterX challenge: real interstitials carry up to ~8.6k chars of text. Must stay a challenge.
ZILLOW_PX = ('<html><body><div id="px-captcha"></div>' + "Rental Listings in Austin TX. Press and hold. " * 180 + "</body></html>")

CASES = [
    (f"<html><body>{X_REAL}</body></html>",  BlockType.OK,         "x.com real posts"),
    (f"<html><body>{LI_REAL}</body></html>", BlockType.OK,         "linkedin real company page"),
    (TRUE_WALL,                              BlockType.LOGIN_WALL, "an actual auth wall"),
    (SHELL,                                  BlockType.SOFT_BLOCK, "a JS shell"),
    (ARTICLE,                                BlockType.OK,         "a plain news article"),
    (N8N_DOCS,                               BlockType.OK,         "n8n docs, 'just a moment' in JSON"),
    (RTINGS,                                 BlockType.SOFT_BLOCK, "rtings: nav only, review in JS"),
    (BESTBUY,                                BlockType.GEO_BLOCK,  "bestbuy country picker"),
    (ZILLOW_PX,                              BlockType.CHALLENGE,  "zillow PerimeterX, 8k text"),
]

fails = 0
for html, want, label in CASES:
    got = classify(200, html)
    ok = got is want
    fails += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<28} -> {got.value}")
print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
import sys
sys.exit(1 if fails else 0)
