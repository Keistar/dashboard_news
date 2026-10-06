"""
Local daily news pipeline (run by scripts/run_daily.sh, see README「ローカルでの毎日の更新」).

Network work is done here, by fixed code. Claude (claude -p) only picks stories and writes
Japanese titles/summaries into JSON files under work/{date}/, and may run this script.
It never fetches pages or runs other commands, so text on a news page cannot make it do so.

Steps (per genre; "trends" is the trend-word feed):
  collect  --date D                 Google News RSS (genres) and Google Trends RSS + Baidu (trends)
                                    -> work/D/candidates/{genre}.json and .txt (numbered list)
  resolve  --date D --genre G       read work/D/selection/G.json (picked numbers), resolve the
                                    Google News links to the original URL and read each page's
                                    description -> work/D/chosen/G.json and .txt
  build    --date D --genre G       read work/D/stories/G.json (Claude's titles/summaries, which
                                    refer to chosen items by "ref"), check it, fill in the source
                                    and URL -> work/D/input/G.json (the --input of generate_dashboard.py)

Selection formats (work/D/selection/G.json):
  genres: {"jp": [3, 7, 12], "us": [...], ...}                       numbers from candidates/G.txt
  trends: {"jp": [{"keyword": "...", "rss": 4}, {"keyword": "...", "query": "検索語"}], ...}
          "rss" = number of a Google Trends / Baidu candidate; "query" = search Google News instead

Stories formats (work/D/stories/G.json):
  genres: {"jp": [{"ref": 0, "title_ja", "summary_ja", "score", "commercial_score",
                   "product_keywords": [...]}, ...], ...}             ref = number in chosen/G.txt
  trends: {"jp": [{"ref": 0, "keyword", "reason_ja", "genre", "commercial_score", "brand_safe",
                   "product_keywords", "product_keywords_ja", "trend_source", "trend_source_type",
                   "articles": [{"ref": 1, "title_ja", "summary_ja"}]}], ...}
          ref = trend number in chosen/trends.txt; articles[].ref = article number under that trend
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from generate_dashboard import GENRES, TRENDS_CODE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
EDITIONS = {
    "jp": ("ja", "JP", "JP:ja"),
    "us": ("en-US", "US", "US:en"),
    "gb": ("en-GB", "GB", "GB:en"),
    "cn": ("zh-CN", "CN", "CN:zh-Hans"),
}
MAX_CANDIDATES = 30
MAX_TREND_ARTICLES = 3

# Google News search queries per genre and country (" when:1d" is added).
QUERIES = {
    "ai": {
        "jp": ["生成AI", "ChatGPT OR Gemini OR Claude OR OpenAI"],
        "us": ['OpenAI OR Anthropic OR "Google Gemini"', "artificial intelligence startup OR AI model"],
        "gb": ["AI UK OR artificial intelligence", "OpenAI OR DeepMind OR Anthropic"],
        "cn": ["人工智能 大模型", "DeepSeek OR 通义千问 OR 豆包 OR 文心"],
    },
    "gadget": {
        "jp": ["新製品 スマホ OR ガジェット", "ワイヤレスイヤホン OR スマートウォッチ OR ノートPC 発表"],
        "us": ["smartphone OR laptop OR gadget launch", "Apple OR Samsung OR Pixel new device"],
        "gb": ["gadget review OR smartphone UK", "new laptop OR headphones OR smartwatch"],
        "cn": ["手机 发布", "数码 新品 OR 智能手表 OR 平板"],
    },
    "kaji": {
        "jp": ["家電 新製品 OR 新発売 家電", "炊飯器 OR ロボット掃除機 OR 食洗機 OR ドラム式洗濯機", "時短 家事 OR 家事 グッズ OR 冷凍食品 新商品"],
        "us": ["kitchen appliance OR robot vacuum launch", "air fryer OR dishwasher OR washer dryer new"],
        "gb": ["kitchen appliance UK OR air fryer", "robot vacuum OR home appliance UK"],
        "cn": ["家电 新品 OR 扫地机器人", "小家电 OR 厨房电器"],
    },
    "beauty": {
        "jp": ["コスメ 新作", "スキンケア OR 化粧品"],
        "us": ["beauty brand OR skincare launch", "cosmetics OR makeup brand"],
        "gb": ["beauty UK skincare", "makeup OR cosmetics brand UK"],
        "cn": ["美妆 OR 化妆品", "护肤 品牌"],
    },
    "food": {
        "jp": ["新商品 食品 OR 新発売 グルメ", "お取り寄せ OR ふるさと納税 返礼品 OR コンビニ 新作"],
        "us": ["food brand launch OR new menu", "grocery OR restaurant chain"],
        "gb": ["food UK supermarket OR restaurant", "new menu OR food brand UK"],
        "cn": ["食品 新品 OR 餐饮", "茶饮 OR 咖啡 品牌"],
    },
    "health": {
        "jp": ["健康 OR 睡眠 OR 運動", "フィットネス OR 健康食品"],
        "us": ["health study OR fitness", "wellness OR sleep OR exercise study"],
        "gb": ["NHS health OR fitness", "health study UK"],
        "cn": ["健康 OR 健身", "运动 健康 OR 体重"],
    },
    "outdoor": {
        "jp": ["キャンプ OR アウトドア", "登山 OR 紅葉 OR キャンプ場"],
        "us": ["camping OR hiking OR outdoor gear", "national park OR outdoor brand"],
        "gb": ["camping OR hiking UK", "outdoor gear OR walking UK"],
        "cn": ["露营 OR 户外", "徒步 OR 登山 OR 户外 品牌"],
    },
    "sale": {
        "jp": ["楽天 セール OR お買い物マラソン OR スーパーSALE", "Amazon セール OR ポイント還元 キャンペーン OR ふるさと納税 期限"],
    },
    "interior": {
        "jp": ["収納 グッズ OR インテリア 新作 OR 家具 新作", "文房具 新製品 OR 文具", "ミニマリスト OR 片付け OR 北欧 雑貨"],
        "us": ["furniture OR interior design brand", "home organization OR stationery launch"],
        "gb": ["interior design UK OR furniture brand", "stationery OR home storage UK"],
        "cn": ["家居 新品 OR 收纳", "文具 OR 家具 品牌"],
    },
}

# Press-release wires, investment/finance sites and government pages are not news for readers.
SKIP_SOURCES = re.compile(
    r"PR TIMES|PRワイヤー|Newscast|@Press|ValuePress|Business Wire|PR Newswire|GlobeNewswire|EIN Presswire|openPR|"
    r"\.lg\.jp|pref\.|city\.|株式会社|Inc\.|美通社|news\.cn$|newsfile|Morningstar|Yahoo Finance|MarketBeat|Benzinga|"
    r"Zacks|Seeking Alpha|TipRanks|Investing\.com|Barchart|AInvest|Simply Wall|みんかぶ|株探|会社四季報",
    re.I,
)


def work(date: str, *parts: str) -> str:
    path = os.path.join(ROOT, "work", date, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def http_get(url: str, limit: int = 600_000, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8,zh;q=0.6"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit)


def google_news(country: str, query: str, window: str = "1d") -> list[dict]:
    hl, gl, ceid = EDITIONS[country]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:{window}", "hl": hl, "gl": gl, "ceid": ceid}
    )
    try:
        xml = http_get(url).decode("utf-8", "ignore")
    except Exception as exc:
        print(f"google news failed ({country}, {query}): {exc}", file=sys.stderr)
        return []
    items = []
    for raw in re.findall(r"<item>(.*?)</item>", xml, re.S)[:40]:
        def tag(name: str) -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", raw, re.S)
            return html.unescape(m.group(1)).strip() if m else ""
        items.append({"title": tag("title"), "source": tag("source"), "link": tag("link"), "date": tag("pubDate")})
    return items


# ---------------------------------------------------------------- collect

def collect_genre(genre: str) -> dict[str, list[dict]]:
    countries = [c["code"] for c in GENRES[genre]["countries"]]
    jobs = [(c, q) for c in countries for q in QUERIES[genre].get(c, [])]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda job: (job[0], google_news(*job)), jobs))
    out: dict[str, list[dict]] = {c: [] for c in countries}
    seen: dict[str, set] = {c: set() for c in countries}
    for country, items in results:
        for item in items:
            key = re.sub(r"\s+-\s+[^-]+$", "", item["title"])
            if key in seen[country] or SKIP_SOURCES.search(item["source"] or "") or SKIP_SOURCES.search(item["title"][-40:]):
                continue
            seen[country].add(key)
            out[country].append(item)
    return {c: items[:MAX_CANDIDATES] for c, items in out.items()}


def collect_trends() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for geo in ("JP", "US", "GB"):
        try:
            from fetch_trends_rss import fetch_geo
            items = fetch_geo(geo)
        except Exception as exc:
            print(f"google trends failed ({geo}): {exc}", file=sys.stderr)
            items = []
        out[geo.lower()] = [
            {"keyword": t["keyword"], "traffic": t["approx_traffic"], "source": "Google トレンド",
             "articles": [{"title": n["title"], "source": n["source"], "url": n["url"]} for n in t["news"] if n.get("url")]}
            for t in items
        ]
    try:
        page = http_get("https://top.baidu.com/board?tab=realtime").decode("utf-8", "ignore")
        data = json.loads(re.search(r"<!--s-data:(.*?)-->", page, re.S).group(1))
        cards = data["data"]["cards"][0]["content"]
        out["cn"] = [
            {"keyword": c.get("word", ""), "traffic": str(c.get("hotScore", "")), "source": "百度热搜",
             "desc": (c.get("desc") or "")[:120], "articles": []}
            for c in cards[:40]
        ]
    except Exception as exc:
        print(f"baidu failed: {exc}", file=sys.stderr)
        out["cn"] = []
    return out


def cmd_collect(date: str) -> None:
    for genre in GENRES:
        cands = collect_genre(genre)
        write_json(work(date, "candidates", f"{genre}.json"), cands)
        with open(work(date, "candidates", f"{genre}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {genre}（{GENRES[genre]['label_ja']}）の候補。番号を selection/{genre}.json に書く\n")
            for c, items in cands.items():
                f.write(f"\n## {c}\n")
                for i, x in enumerate(items):
                    f.write(f"{i} {x['title'][:140]}\n")
        print(genre, {c: len(v) for c, v in cands.items()})

    trends = collect_trends()
    write_json(work(date, "candidates", f"{TRENDS_CODE}.json"), trends)
    with open(work(date, "candidates", f"{TRENDS_CODE}.txt"), "w", encoding="utf-8") as f:
        f.write("# トレンドの候補（Google トレンド・百度热搜）。番号を selection/trends.json の rss に書く\n")
        for c, items in trends.items():
            f.write(f"\n## {c}\n")
            for i, t in enumerate(items):
                news = " / ".join(f"{a['title'][:70]}（{a['source']}）" for a in t["articles"][:2]) or t.get("desc", "")
                f.write(f"{i} {t['keyword']} | {t['traffic']} | {news}\n")
    print(TRENDS_CODE, {c: len(v) for c, v in trends.items()})


# ---------------------------------------------------------------- resolve

def decode_google_link(link: str) -> str | None:
    if "news.google.com" not in link:
        return link
    from googlenewsdecoder import gnewsdecoder
    for _ in range(3):
        try:
            r = gnewsdecoder(link, interval=1)
            if r.get("status") or r.get("success"):
                return r.get("decoded_url")
        except Exception:
            pass
        time.sleep(2)
    return None


def page_description(url: str) -> tuple[str, str]:
    """The page's meta description (og:description etc.) and published time, if any."""
    try:
        raw = http_get(url, timeout=15)
    except Exception as exc:
        return "", f"ERR {type(exc).__name__}"
    text = raw.decode("utf-8", "ignore")
    head = text[:3000].lower()
    if "charset=gb" in head or 'charset="gb' in head:
        text = raw.decode("gbk", "ignore")
    m = re.search(
        r"<meta[^>]+(?:property|name)=[\"'](?:og:description|description|twitter:description)[\"'][^>]*content=[\"']([^\"']+)",
        text, re.I,
    ) or re.search(
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"'](?:og:description|description)[\"']",
        text, re.I,
    )
    desc = html.unescape(m.group(1)).strip() if m else ""
    pm = re.search(r"(?:article:published_time|datePublished)[\"']?\s*[:=]?\s*(?:content=)?[\"']([0-9]{4}-[0-9]{2}-[0-9]{2}[^\"']*)", text)
    return desc[:400], (pm.group(1)[:16] if pm else "")


def clean_url(url: str) -> str:
    url = re.sub(r"(walkerplus\.com/article/\d+/)image\d+\.html", r"\1", url)
    url = re.sub(r"galleryimg\.html\?num=\d+$", "", url)
    url = re.sub(r"(sohu\.com/a/[0-9_]+)\?.*$", r"\1", url)
    url = re.sub(r"\?syn-[^&]+$", "", url)
    return url


def resolve_items(items: list[dict]) -> None:
    """Fill url / desc / pub on each item (in place)."""
    with ThreadPoolExecutor(4) as ex:
        urls = list(ex.map(lambda x: decode_google_link(x["link"]), items))
    for x, u in zip(items, urls):
        x["url"] = clean_url(u) if u else None
    with ThreadPoolExecutor(10) as ex:
        descs = list(ex.map(lambda x: page_description(x["url"]) if x["url"] else ("", "no url"), items))
    for x, (d, p) in zip(items, descs):
        x["desc"], x["pub"] = d, p


def cmd_resolve(date: str, genre: str) -> None:
    cands = read_json(work(date, "candidates", f"{genre}.json"))
    sel = read_json(work(date, "selection", f"{genre}.json"))
    lines = [f"# {genre} の選んだ記事。stories/{genre}.json の ref にこの番号を書く。D: は記事ページの説明文（中身の確認用）"]

    if genre == TRENDS_CODE:
        chosen: dict[str, list[dict]] = {}
        for c, picks in sel.items():
            chosen[c] = []
            for pick in picks:
                keyword = str(pick.get("keyword", "")).strip()
                cand = cands.get(c, [])[pick["rss"]] if isinstance(pick.get("rss"), int) else None
                if cand and cand["articles"]:
                    arts = [{"title": a["title"], "source": a["source"], "link": a["url"]} for a in cand["articles"][:MAX_TREND_ARTICLES]]
                else:
                    query = pick.get("query") or keyword
                    arts = [{"title": a["title"], "source": a["source"], "link": a["link"]} for a in google_news(c, query, "2d")[:MAX_TREND_ARTICLES]]
                resolve_items(arts)
                arts = [a for a in arts if a["url"]]
                chosen[c].append({"keyword": keyword or (cand or {}).get("keyword", ""), "observed": (cand or {}).get("source", "ニュース"),
                                  "traffic": (cand or {}).get("traffic", ""), "articles": arts})
        write_json(work(date, "chosen", f"{genre}.json"), chosen)
        for c, trends in chosen.items():
            lines.append(f"\n## {c}")
            for k, t in enumerate(trends):
                lines.append(f"[{k}] {t['keyword']}（観測: {t['observed']} {t['traffic']}）")
                for j, a in enumerate(t["articles"]):
                    lines.append(f"    ({j}) {a['title']}（{a['source']}） pub={a['pub']}\n        D: {a['desc']}")
                if not t["articles"]:
                    lines.append("    （記事が見つからなかった。このトレンドは使わない）")
    else:
        chosen = {}
        for c, idxs in sel.items():
            items = [dict(cands[c][i]) for i in idxs if isinstance(i, int) and 0 <= i < len(cands.get(c, []))]
            resolve_items(items)
            chosen[c] = items
        write_json(work(date, "chosen", f"{genre}.json"), chosen)
        for c, items in chosen.items():
            lines.append(f"\n## {c}")
            for k, x in enumerate(items):
                status = "" if x["url"] else "（元記事の URL が取れなかった。使わない）"
                lines.append(f"[{k}] {x['title']}{status}\n    source={x['source']} rss={x['date'][5:22]} pub={x['pub']}\n    D: {x['desc']}")

    with open(work(date, "chosen", f"{genre}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote work/{date}/chosen/{genre}.txt")


# ---------------------------------------------------------------- build

def _score(value, errors: list[str], where: str) -> int:
    if not isinstance(value, int) or not 1 <= value <= 100:
        errors.append(f"{where}: score は 1〜100 の整数")
        return 50
    return value


def cmd_build(date: str, genre: str) -> None:
    chosen = read_json(work(date, "chosen", f"{genre}.json"))
    stories = read_json(work(date, "stories", f"{genre}.json"))
    errors: list[str] = []
    out: dict[str, list[dict]] = {}

    for c, items in stories.items():
        if c not in chosen:
            errors.append(f"{c}: chosen にない国")
            continue
        out[c] = []
        for n, s in enumerate(items):
            where = f"{c}[{n}]"
            ref = s.get("ref")
            if not isinstance(ref, int) or not 0 <= ref < len(chosen[c]):
                errors.append(f"{where}: ref が chosen/{genre}.txt の番号にない")
                continue
            src = chosen[c][ref]
            if genre == TRENDS_CODE:
                arts = []
                for m, a in enumerate(s.get("articles", [])):
                    ar = a.get("ref")
                    if not isinstance(ar, int) or not 0 <= ar < len(src["articles"]):
                        errors.append(f"{where}.articles[{m}]: ref が記事の番号にない")
                        continue
                    if not a.get("title_ja") or not a.get("summary_ja"):
                        errors.append(f"{where}.articles[{m}]: title_ja と summary_ja が要る")
                    sa = src["articles"][ar]
                    arts.append({"title_ja": a.get("title_ja", ""), "summary_ja": a.get("summary_ja", ""),
                                 "source": sa["source"].strip(), "url": sa["url"]})
                if not arts:
                    errors.append(f"{where}: 記事が 1 本も無い")
                for key in ("keyword", "reason_ja", "trend_source"):
                    if not str(s.get(key, "")).strip():
                        errors.append(f"{where}: {key} が要る")
                if s.get("trend_source_type") not in ("search", "sns", "news", "ec_ranking", "other"):
                    errors.append(f"{where}: trend_source_type は search / sns / news / ec_ranking / other")
                if not isinstance(s.get("brand_safe"), bool):
                    errors.append(f"{where}: brand_safe は true / false")
                out[c].append({
                    "keyword": s.get("keyword", ""), "reason_ja": s.get("reason_ja", ""),
                    "genre": s.get("genre") if s.get("genre") in GENRES else "other",
                    "commercial_score": _score(s.get("commercial_score"), errors, where),
                    "brand_safe": s.get("brand_safe", False),
                    "product_keywords": s.get("product_keywords", [])[:3],
                    "product_keywords_ja": s.get("product_keywords_ja", [])[:3],
                    "trend_source": s.get("trend_source", ""), "trend_source_type": s.get("trend_source_type", "other"),
                    "articles": arts,
                })
            else:
                if not src.get("url"):
                    errors.append(f"{where}: 元記事の URL が無い記事は使えない")
                    continue
                title, summary = str(s.get("title_ja", "")).strip(), str(s.get("summary_ja", "")).strip()
                if not title or len(title) > 40:
                    errors.append(f"{where}: title_ja は 1〜40 文字（今 {len(title)}）")
                if not summary:
                    errors.append(f"{where}: summary_ja が要る")
                story = {"title_ja": title, "summary_ja": summary, "source": src["source"].strip(), "url": src["url"],
                         "score": _score(s.get("score"), errors, where),
                         "commercial_score": _score(s.get("commercial_score"), errors, where)}
                if s.get("product_keywords"):
                    story["product_keywords"] = s["product_keywords"][:3]
                out[c].append(story)
        if genre != TRENDS_CODE:
            out[c].sort(key=lambda x: -x["score"])

    if errors:
        print("直してから build し直してください:\n- " + "\n- ".join(errors))
        sys.exit(1)
    write_json(work(date, "input", f"{genre}.json"), out)
    print(f"wrote work/{date}/input/{genre}.json", {c: len(v) for c, v in out.items()})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["collect", "resolve", "build"])
    p.add_argument("--date", required=True, help="YYYY-MM-DD（日本時間）")
    p.add_argument("--genre", choices=[TRENDS_CODE, *GENRES])
    a = p.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.date):
        p.error("--date は YYYY-MM-DD")
    if a.command == "collect":
        cmd_collect(a.date)
    else:
        if not a.genre:
            p.error("--genre が要る")
        (cmd_resolve if a.command == "resolve" else cmd_build)(a.date, a.genre)


if __name__ == "__main__":
    main()
