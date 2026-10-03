"""
Build a genre's daily dashboard from RSS feeds instead of web search.

  1. fetch every feed in scripts/feeds.json for the genre (dead feeds are skipped)
  2. keep items from the last --hours hours
  3. ask Claude (no tools) to pick the best items and write Japanese titles/summaries
  4. write the --input JSON and run generate_dashboard.py on it

Only URLs that appeared in a feed are accepted, so the model cannot invent links.

Usage:
  python scripts/rss_news.py --genre pet [--date YYYY-MM-DD] [--hours 72] [--dry-run]
Requires ANTHROPIC_API_KEY (not needed with --dry-run, which prints candidate counts).
"""

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; dashboard-news-bot/1.0)"
TIMEOUT = 20
MODEL = "claude-sonnet-4-6"
PER_COUNTRY_CANDIDATES = 40
PICK = 8
COUNTRIES = ("jp", "us", "gb", "cn")
GNEWS = {
    "jp": ("ja", "JP", "JP:ja"), "us": ("en-US", "US", "US:en"),
    "gb": ("en-GB", "GB", "GB:en"), "cn": ("zh-CN", "CN", "CN:zh-Hans"),
}


def feed_url(entry: str, country: str) -> str:
    if not entry.startswith("gnews:"):
        return entry
    hl, gl, ceid = GNEWS[country]
    q = urllib.parse.quote(entry[len("gnews:"):] + " when:3d")
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _parse_date(s: str):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _child(el, "title")
        link = _child(el, "link")
        url = ""
        if link is not None:
            url = (link.get("href") or link.text or "").strip()
        date = next((_child(el, n) for n in ("pubDate", "published", "updated", "date") if _child(el, n) is not None), None)
        desc = next((_child(el, n) for n in ("description", "summary", "content") if _child(el, n) is not None), None)
        src = _child(el, "source")
        text = re.sub(r"<[^>]+>", " ", (desc.text or "") if desc is not None else "")
        out.append({
            "title": (title.text or "").strip() if title is not None else "",
            "url": url,
            "published": _parse_date((date.text or "").strip()) if date is not None else None,
            "summary": re.sub(r"\s+", " ", text).strip()[:300],
            "source": (src.text or "").strip() if src is not None else "",
        })
    return [i for i in out if i["title"] and i["url"]]


def collect(genre: str, hours: int) -> dict[str, list[dict]]:
    with open(os.path.join(HERE, "feeds.json"), encoding="utf-8") as f:
        cfg = json.load(f)[genre]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = {}
    for country in COUNTRIES:
        seen, items = set(), []
        for entry in cfg.get(country, []):
            url = feed_url(entry, country)
            host = urllib.parse.urlparse(url).netloc
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    parsed = parse_feed(r.read())
            except Exception as exc:  # dead/blocked feed: skip, keep going
                print(f"  [{country}] skip {host}: {exc}", file=sys.stderr)
                continue
            fresh = [i for i in parsed if i["published"] is None or i["published"] >= cutoff]
            print(f"  [{country}] {host}: {len(fresh)}/{len(parsed)} fresh", file=sys.stderr)
            for i in fresh:
                if i["url"] not in seen:
                    seen.add(i["url"])
                    i["source"] = i["source"] or host
                    items.append(i)
        items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        result[country] = items[:PER_COUNTRY_CANDIDATES]
    return result


PROMPT = """あなたはニュース編集者です。ジャンル「{label}」について、国ごとの候補記事（RSSから取得）から、
重要度が高く商品・生活に関係する記事を最大{pick}件ずつ選び、日本語で書き直してください。
- 候補にない記事・URLは絶対に使わない。urlは候補のidで指定する。
- 古い・重複・広告色の強い記事は避ける。候補が少なければ件数を減らしてよい（無理に埋めない）。
- JSONのみを出力（説明やコードフェンスなし）:
{{"jp":[{{"id":0,"title_ja":"40字以内","summary_ja":"2〜3文","score":1-100,"commercial_score":1-100,"product_keywords":["日本語の商品検索語0〜3"]}}],"us":[],"gb":[],"cn":[]}}

候補:
{candidates}
"""


def pick_with_claude(genre: str, label: str, cands: dict[str, list[dict]]) -> dict:
    import anthropic
    from json_repair import repair_json

    blob = {
        c: [{"id": n, "title": i["title"], "source": i["source"], "summary": i["summary"],
             "published": i["published"].isoformat() if i["published"] else None}
            for n, i in enumerate(items)]
        for c, items in cands.items()
    }
    msg = PROMPT.format(label=label, pick=PICK, candidates=json.dumps(blob, ensure_ascii=False))
    client = anthropic.Anthropic()
    resp = client.messages.create(model=MODEL, max_tokens=16000, messages=[{"role": "user", "content": msg}])
    raw = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in response: {raw[:200]!r}")
    picked = repair_json(m.group(0), return_objects=True)
    out = {}
    for c in COUNTRIES:
        rows = []
        for p in picked.get(c, []) if isinstance(picked, dict) else []:
            try:
                src = cands[c][int(p["id"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue  # unknown id: never emit an invented link
            row = {k: p[k] for k in ("title_ja", "summary_ja", "score", "commercial_score", "product_keywords") if k in p}
            row.update(source=src["source"], url=src["url"])
            rows.append(row)
        out[c] = rows
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--genre", required=True)
    ap.add_argument("--date")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, HERE)
    import generate_dashboard as gd
    if args.genre not in gd.GENRES:
        ap.error(f"unknown genre {args.genre}")

    print(f"== {args.genre}", file=sys.stderr)
    cands = collect(args.genre, args.hours)
    if args.dry_run:
        print({c: len(v) for c, v in cands.items()})
        return
    if not any(cands.values()):
        print("no candidates; skipping", file=sys.stderr)
        sys.exit(0)
    stories = pick_with_claude(args.genre, gd.GENRES[args.genre]["label_ja"], cands)
    if not any(stories.values()):
        print("nothing selected; skipping", file=sys.stderr)
        sys.exit(0)
    path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), f"stories_{args.genre}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stories, f, ensure_ascii=False)
    cmd = [sys.executable, os.path.join(HERE, "generate_dashboard.py"), "--genre", args.genre, "--input", path]
    if args.date:
        cmd += ["--date", args.date]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
