"""
Fetch trending-search candidates from Google Trends RSS.

Helper for building the --genre trends input: it lists what is trending,
with approximate search volume and related news, so a human or Claude can
pick brand-safe, product-related keywords. Google Trends has no feed for
China, so "cn" candidates must be collected another way (web search).

Usage:
  python scripts/fetch_trends_rss.py                 # JP, US, GB
  python scripts/fetch_trends_rss.py --geo JP,US > candidates.json

Output (stdout, JSON):
  {"JP": [{"keyword", "approx_traffic", "pub_date",
           "news": [{"title", "url", "source"}]}], ...}
Exits with status 1 (without a traceback) if every feed fails.
"""

import argparse
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://trends.google.com/trending/rss?geo={geo}"
HT = "{https://trends.google.com/trending/rss}"
TIMEOUT = 20


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def fetch_geo(geo: str) -> list[dict]:
    req = urllib.request.Request(FEED_URL.format(geo=geo), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for item in root.iter("item"):
        items.append({
            "keyword": _text(item, "title"),
            "approx_traffic": _text(item, f"{HT}approx_traffic"),
            "pub_date": _text(item, "pubDate"),
            "news": [
                {
                    "title": _text(news, f"{HT}news_item_title"),
                    "url": _text(news, f"{HT}news_item_url"),
                    "source": _text(news, f"{HT}news_item_source"),
                }
                for news in item.findall(f"{HT}news_item")
            ],
        })
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Google Trends RSS candidates.")
    parser.add_argument("--geo", default="JP,US,GB", help="Comma-separated country codes (default: JP,US,GB)")
    args = parser.parse_args()

    result: dict[str, list[dict]] = {}
    failures = 0
    geos = [g.strip().upper() for g in args.geo.split(",") if g.strip()]
    for geo in geos:
        try:
            result[geo] = fetch_geo(geo)
        except Exception as exc:  # network / parse errors: report and continue
            failures += 1
            result[geo] = []
            print(f"Failed to fetch {geo}: {exc}", file=sys.stderr)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if geos and failures == len(geos):
        sys.exit(1)


if __name__ == "__main__":
    main()
