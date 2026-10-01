"""
Generates a multi-page news dashboard.

Page structure:
  docs/index.html                           — top page: genre list
  docs/{genre}/index.html                   — genre page: country list
  docs/{genre}/{country}/index.html         — country page: date list (newest first)
  docs/{genre}/{country}/{date}.html        — article page: daily stories
  docs/{genre}/{country}/{date}.json        — same stories as JSON
  docs/{genre}/latest.json                  — latest run, all countries, score-sorted
  docs/index.json                           — catalog: genres, countries, available dates
  docs/latest.json                          — every genre's latest stories + trends combined
  docs/trends/...                           — trend-word feed (same layout as a genre)

Usage (Claude-dispatch mode — no API key required):
  python scripts/generate_dashboard.py --genre tech --input stories.json
  python scripts/generate_dashboard.py --genre economy --input stories.json
  python scripts/generate_dashboard.py --genre beauty --input stories.json

  Genres: ai, gadget, kosodate, beauty, food, health, pet, outdoor, bousai
  Countries (all genres): jp, us, gb, cn

  The --input JSON must follow the schema:
  {
    "<country_code>": [
      {
        "title_ja": "string, <=40 chars",
        "summary_ja": "string, 2-3 sentences",
        "source": "publication name",
        "url": "article URL",
        "score": integer 1-100 (optional, relative impact for the day)
      },
      ...
    ],
    ...
  }

Trends feed (--genre trends, --input required):
  {
    "<country_code>": [
      {
        "keyword": "trending search term",
        "reason_ja": "why it is trending (1 sentence)",
        "genre": "one of the genre codes above, or \"other\"",
        "commercial_score": integer 1-100 (how easily it maps to products),
        "brand_safe": true | false (false = dropped: deaths, crime, disasters, politics),
        "product_keywords": ["product search term", ...] (1-3),
        "product_keywords_ja": ["Japanese search term for the same products on Rakuten", ...] (0-3, optional),
        "trend_source": "where the trend was observed",
        "trend_source_type": "search | sns | news | ec_ranking | other" (kind of trend_source),
        "articles": [{"title_ja", "summary_ja", "source", "url"}, ...] (1-3)
      }
    ]
  }
  Output: docs/trends/{country}/{date}.html|json, docs/trends/latest.json

Legacy (API mode — requires ANTHROPIC_API_KEY):
  python scripts/generate_dashboard.py --genre tech
"""

import argparse
import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

MAX_STORIES = 8
MAX_RETRIES = 3

JST = timezone(timedelta(hours=9))

# Every genre (and the trends feed) covers the same four countries.
# Japan is the primary market; US/UK/China act as leading indicators.
COUNTRIES = [
    {"code": "jp", "label_ja": "日本",     "label_en": "JP EDITION", "name_en": "Japan"},
    {"code": "us", "label_ja": "アメリカ", "label_en": "US EDITION", "name_en": "United States"},
    {"code": "gb", "label_ja": "イギリス", "label_en": "GB EDITION", "name_en": "United Kingdom"},
    {"code": "cn", "label_ja": "中国",     "label_en": "CN EDITION", "name_en": "China"},
]

GENRES = {
    "ai": {
        "label_ja": "AI",
        "label_en": "ARTIFICIAL INTELLIGENCE",
        # theme[0]=genre page, theme[1]=country page, theme[2]=article page (muted→vivid)
        "bg": "#061614",
        "theme": [
            {"line": "#0b2035", "amber": "#1b9688", "signal": "#007fb8"},
            {"line": "#102840", "amber": "#2ecfba", "signal": "#00b8e6"},
            {"line": "#133858", "amber": "#50e4d0", "signal": "#33ccf5"},
        ],
        "system_prompt_intro": (
            "You are a global news curator producing a daily AI industry briefing "
            "for a Japanese software engineer who reads it every morning. Use the web_search tool to "
            "find the most significant AI news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "model releases, funding rounds, major AI product launches, "
            "AI regulation, notable research papers, key personnel moves"
        ),
        "user_message_ja": "AI業界の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "gadget": {
        "label_ja": "ガジェット",
        "label_en": "GADGETS & DEVICES",
        "bg": "#0f0a04",
        "theme": [
            {"line": "#241400", "amber": "#a05c10", "signal": "#c07020"},
            {"line": "#301c00", "amber": "#e07c20", "signal": "#f09030"},
            {"line": "#3c2400", "amber": "#f59840", "signal": "#ffb050"},
        ],
        "system_prompt_intro": (
            "You are a global gadget and consumer electronics news curator producing a daily briefing "
            "for a Japanese tech enthusiast. Use the web_search tool to "
            "find the most significant gadget and device news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "smartphones, wearables, laptops, home appliances, cameras, "
            "audio gear, gaming hardware, drones, EV and mobility gadgets"
        ),
        "user_message_ja": "ガジェット・家電の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "kosodate": {
        "label_ja": "子育て",
        "label_en": "PARENTING & CHILDCARE",
        "bg": "#060f08",
        "theme": [
            {"line": "#0a2010", "amber": "#2a8042", "signal": "#1a9050"},
            {"line": "#0e2c16", "amber": "#40aa5a", "signal": "#30c068"},
            {"line": "#123820", "amber": "#58c870", "signal": "#48d880"},
        ],
        "system_prompt_intro": (
            "You are a global parenting and childcare news curator producing a daily briefing "
            "for Japanese parents and caregivers. Use the web_search tool to "
            "find the most significant parenting, childcare, and child education news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "childcare policy, education reform, parenting trends, child health and nutrition, "
            "family-friendly products, school systems, birth rate issues, child development research"
        ),
        "user_message_ja": "子育て・教育分野の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "beauty": {
        "label_ja": "美容・コスメ",
        "label_en": "BEAUTY & COSMETICS",
        "bg": "#140810",
        "theme": [
            {"line": "#2a1020", "amber": "#b0507a", "signal": "#c0608a"},
            {"line": "#361428", "amber": "#e0709f", "signal": "#f080b0"},
            {"line": "#421a32", "amber": "#f590ba", "signal": "#ffa0c8"},
        ],
        "system_prompt_intro": (
            "You are a beauty and cosmetics news curator producing a daily briefing "
            "for Japanese beauty shoppers. Use the web_search tool to "
            "find the most significant beauty and cosmetics news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "new cosmetics and skincare launches, K-beauty and J-beauty trends, ingredient research, "
            "beauty devices, brand and retail news, seasonal best-seller rankings"
        ),
        "user_message_ja": "美容・コスメ分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "food": {
        "label_ja": "食品・グルメ",
        "label_en": "FOOD & GOURMET",
        "bg": "#140c04",
        "theme": [
            {"line": "#2a1a08", "amber": "#b08030", "signal": "#c09040"},
            {"line": "#36220a", "amber": "#e0a840", "signal": "#f0b850"},
            {"line": "#422a0e", "amber": "#f5c060", "signal": "#ffd070"},
        ],
        "system_prompt_intro": (
            "You are a food and gourmet news curator producing a daily briefing "
            "for Japanese food lovers who shop online. Use the web_search tool to "
            "find the most significant food and gourmet news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "trending foods and sweets, new product launches, seasonal ingredients, "
            "furusato nozei (hometown tax) return-gift trends, food prices and supply, food safety recalls"
        ),
        "user_message_ja": "食品・グルメ分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "health": {
        "label_ja": "健康・フィットネス",
        "label_en": "HEALTH & FITNESS",
        "bg": "#040f12",
        "theme": [
            {"line": "#0a2028", "amber": "#2a90a8", "signal": "#30a0c0"},
            {"line": "#0e2c36", "amber": "#40c0dc", "signal": "#50d0f0"},
            {"line": "#123842", "amber": "#60d8f0", "signal": "#70e4ff"},
        ],
        "system_prompt_intro": (
            "You are a health and fitness news curator producing a daily briefing "
            "for health-conscious Japanese readers. Use the web_search tool to "
            "find the most significant health, wellness and fitness news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "sleep, nutrition and gut-health research, supplements, fitness trends, "
            "wearable health tech, public health advisories, diet and exercise studies"
        ),
        "user_message_ja": "健康・フィットネス分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "pet": {
        "label_ja": "ペット",
        "label_en": "PETS",
        "bg": "#100c06",
        "theme": [
            {"line": "#241c10", "amber": "#a08050", "signal": "#b09060"},
            {"line": "#302414", "amber": "#d0a870", "signal": "#e0b880"},
            {"line": "#3c2c18", "amber": "#e8c090", "signal": "#f0d0a0"},
        ],
        "system_prompt_intro": (
            "You are a pet news curator producing a daily briefing "
            "for Japanese dog and cat owners. Use the web_search tool to "
            "find the most significant pet-related news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "pet food launches and recalls, pet tech and gadgets, veterinary research, "
            "pet insurance, animal welfare law, pet-friendly services and trends"
        ),
        "user_message_ja": "ペット分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "outdoor": {
        "label_ja": "アウトドア",
        "label_en": "OUTDOOR & CAMPING",
        "bg": "#080e04",
        "theme": [
            {"line": "#18220a", "amber": "#7a9030", "signal": "#8aa040"},
            {"line": "#1e2c0e", "amber": "#a8c040", "signal": "#b8d050"},
            {"line": "#263612", "amber": "#c0d860", "signal": "#d0e870"},
        ],
        "system_prompt_intro": (
            "You are an outdoor and camping news curator producing a daily briefing "
            "for Japanese outdoor enthusiasts. Use the web_search tool to "
            "find the most significant outdoor, camping and hiking news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "new camping and hiking gear, outdoor brand news, portable power stations, "
            "seasonal outdoor trends, campsite and trail news, outdoor safety"
        ),
        "user_message_ja": "アウトドア・キャンプ分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
    "bousai": {
        "label_ja": "防災",
        "label_en": "DISASTER PREPAREDNESS",
        "bg": "#120606",
        "theme": [
            {"line": "#2a0e0e", "amber": "#b04040", "signal": "#c05050"},
            {"line": "#361212", "amber": "#e06050", "signal": "#f07060"},
            {"line": "#421818", "amber": "#f08070", "signal": "#ff9080"},
        ],
        "system_prompt_intro": (
            "You are a disaster preparedness news curator producing a daily briefing "
            "for Japanese households. Use the web_search tool to "
            "find the most significant disaster and preparedness news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "earthquakes, typhoons and extreme weather, government preparedness guidance, "
            "emergency supplies and stockpiling, evacuation and shelter news, disaster tech"
        ),
        "user_message_ja": "防災分野の直近ニュースを調べて、指定したJSON形式で返してください。",
        "countries": COUNTRIES,
    },
}

# Trend-word feed: each item is a trending keyword with related articles.
# Built only from an --input file (collected manually via web search).
TRENDS_CODE = "trends"
MAX_TRENDS = 10
MAX_TREND_ARTICLES = 3
MAX_PRODUCT_KEYWORDS = 3
TRENDS = {
    "label_ja": "トレンド",
    "label_en": "TRENDING NOW",
    "bg": "#0c0614",
    "theme": [
        {"line": "#201030", "amber": "#9060d0", "signal": "#a070e0"},
        {"line": "#2a1440", "amber": "#b080f0", "signal": "#c090ff"},
        {"line": "#341a50", "amber": "#c8a0ff", "signal": "#d8b0ff"},
    ],
    "countries": COUNTRIES,
}


def build_system_prompt(genre_cfg: dict) -> str:
    countries = genre_cfg["countries"]
    country_list = "\n".join(f'  - "{c["code"]}": {c["name_en"]}' for c in countries)

    first = countries[0]["code"]
    rest_lines = "\n".join(
        f'    "{c["code"]}": {{ "stories": [ ... ] }},'
        for c in countries[1:]
    )

    return f"""{genre_cfg["system_prompt_intro"]} in each of these {len(countries)} regions:

{country_list}

For each country, pick the {MAX_STORIES} most important stories ({genre_cfg["story_types"]}). \
For each story, write the title and summary in natural, concise Japanese.

Respond with ONLY valid JSON — no markdown fences, no commentary — matching exactly \
this schema:

{{
  "countries": {{
    "{first}": {{
      "stories": [
        {{
          "title_ja": "string, <=40 characters, no trailing period",
          "summary_ja": "string, 2-3 sentences: what happened and why it matters",
          "source": "string, name of the original publication",
          "url": "string, direct URL to the original article",
          "score": "integer 1-100, relative impact among today's stories",
          "commercial_score": "integer 1-100, how directly the story leads to products Japanese shoppers can buy online",
          "product_keywords": ["1-3 short Japanese product search terms related to the story; empty list if none fit"]
        }}
      ]
    }},
{rest_lines}
  }}
}}
"""


def build_user_message(genre_cfg: dict, today_jst: str) -> str:
    countries_ja = "・".join(c["label_ja"] for c in genre_cfg["countries"])
    return (
        f"今日は{today_jst}（日本時間）です。"
        f"{countries_ja}それぞれの"
        f"{genre_cfg['user_message_ja']}"
    )



def load_stories_from_file(path: str, genre_cfg: dict) -> dict[str, list[dict]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    result: dict[str, list[dict]] = {}
    for c in genre_cfg["countries"]:
        code = c["code"]
        stories = data.get(code, [])[:MAX_STORIES]
        if not stories:
            print(f"Warning: no stories for {code} in {path}", file=sys.stderr)
        result[code] = stories
    return result


def _clamp_score(value) -> int | None:
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def _clean_keywords(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [k.strip() for k in value if isinstance(k, str) and k.strip()][:MAX_PRODUCT_KEYWORDS]


TREND_SOURCE_TYPES = ("search", "sns", "news", "ec_ranking", "other")


def _clean_source_type(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in TREND_SOURCE_TYPES else "other"


def load_trends_from_file(path: str) -> dict[str, list[dict]]:
    """Read the trends input, dropping brand-unsafe items and normalising fields."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    result: dict[str, list[dict]] = {}
    for c in TRENDS["countries"]:
        code = c["code"]
        trends = []
        for item in data.get(code, []):
            keyword = str(item.get("keyword", "")).strip()
            if not keyword or item.get("brand_safe") is False:
                continue
            articles = [
                _clean_story(a, ARTICLE_FIELDS)
                for a in item.get("articles", [])[:MAX_TREND_ARTICLES]
                if a.get("url")
            ]
            genre = item.get("genre")
            trends.append({
                "keyword": keyword,
                "reason_ja": str(item.get("reason_ja", "")).strip(),
                "genre": genre if genre in GENRES else "other",
                "commercial_score": _clamp_score(item.get("commercial_score")),
                "product_keywords": _clean_keywords(item.get("product_keywords")),
                "product_keywords_ja": _clean_keywords(item.get("product_keywords_ja")),
                "trend_source": str(item.get("trend_source", "")).strip(),
                "trend_source_type": _clean_source_type(item.get("trend_source_type")),
                "articles": articles,
            })
        trends.sort(key=lambda t: t["commercial_score"] or 0, reverse=True)
        result[code] = trends[:MAX_TRENDS]
        if not result[code]:
            print(f"Warning: no trends for {code} in {path}", file=sys.stderr)
    return result


def fetch_all_stories(genre_cfg: dict) -> dict[str, list[dict]]:
    try:
        import anthropic
        from json_repair import repair_json
    except ImportError as exc:
        raise ImportError(
            "anthropic / json_repair packages are required for API mode. "
            "Use --input to provide stories from a JSON file instead."
        ) from exc

    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 32000
    WEB_SEARCH_MAX_USES = 24

    client = anthropic.Anthropic()

    today_jst = datetime.now(JST).strftime("%Y年%m月%d日")
    system_prompt = build_system_prompt(genre_cfg)
    user_message = build_user_message(genre_cfg, today_jst)

    def _call_api(system_prompt: str, user_message: str) -> str:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            response = stream.get_final_message()

        text_blocks = [block.text for block in response.content if block.type == "text"]
        raw = "".join(text_blocks).strip()
        if not raw:
            raise ValueError(f"Model returned no text (stop_reason={response.stop_reason!r})")
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON found in response: {raw[:300]!r}")
        return json_match.group(0)

    def _parse_json(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"JSON parse failed ({exc}); attempting repair…", file=sys.stderr)
            repaired = repair_json(raw, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
            raise ValueError(f"JSON repair failed; raw: {raw[:300]!r}") from exc

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = _call_api(system_prompt, user_message)
            data = _parse_json(raw)
            break
        except Exception as exc:
            last_exc = exc
            print(f"Attempt {attempt}/{MAX_RETRIES} failed: {exc}", file=sys.stderr)
            if attempt == MAX_RETRIES:
                raise ValueError(f"All {MAX_RETRIES} attempts failed") from last_exc
    else:
        raise ValueError(f"All {MAX_RETRIES} attempts failed") from last_exc

    countries_data = data.get("countries", {})
    result: dict[str, list[dict]] = {}
    for c in genre_cfg["countries"]:
        code = c["code"]
        stories = countries_data.get(code, {}).get("stories", [])[:MAX_STORIES]
        if not stories:
            print(f"Warning: no stories returned for {code}", file=sys.stderr)
        result[code] = stories
    return result


# ─── shared CSS ───────────────────────────────────────────────────────────────

_CSS_BODY = """  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans JP', -apple-system, sans-serif;
    line-height: 1.7;
  }
  .mono { font-family: 'JetBrains Mono', monospace; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; }
  .masthead {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid var(--line);
    padding-bottom: 16px;
    margin-bottom: 8px;
  }
  .masthead-title { font-size: 14px; letter-spacing: 0.12em; color: var(--amber); font-weight: 700; }
  .live-tag {
    font-size: 11px; letter-spacing: 0.08em; color: var(--signal);
    display: inline-flex; align-items: center; gap: 6px;
  }
  .live-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--signal); animation: pulse 2.4s ease-in-out infinite;
  }
  @media (prefers-reduced-motion: reduce) { .live-dot { animation: none; } }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
  .back-link {
    display: inline-block; font-size: 12px; color: var(--text-dim);
    text-decoration: none; margin: 12px 0 16px; letter-spacing: 0.05em;
  }
  .back-link:hover { color: var(--amber); }
  .genre-hero { margin: 0 0 24px; }
  .genre-hero-title { font-size: 36px; font-weight: 700; color: var(--amber); letter-spacing: 0.03em; margin: 0 0 4px; line-height: 1.2; }
  .genre-hero-sub { font-size: 13px; letter-spacing: 0.18em; color: var(--text-dim); margin: 0; }
  .breadcrumb {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; color: var(--text-dim); margin: 12px 0 20px; letter-spacing: 0.05em;
  }
  .breadcrumb a { color: var(--text-dim); text-decoration: none; }
  .breadcrumb a:hover { color: var(--amber); }
  .breadcrumb-sep { color: var(--line); }
  .section-label { font-size: 12px; letter-spacing: 0.1em; color: var(--text-dim); margin-bottom: 4px; }
  .meta-line { font-size: 12px; color: var(--text-dim); margin-bottom: 28px; }
  /* article list */
  ul.entries { list-style: none; margin: 0; padding: 0; }
  .entry { display: flex; gap: 16px; padding: 20px 0; border-bottom: 1px solid var(--line); }
  .entry:last-child { border-bottom: none; }
  .entry-index { flex: 0 0 auto; color: var(--amber); font-size: 13px; padding-top: 3px; }
  .entry-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin: 0 0 8px; }
  .entry-title { font-size: 16px; font-weight: 600; margin: 0; letter-spacing: 0.01em; flex: 1; }
  .entry-score {
    flex: 0 0 auto; font-family: 'JetBrains Mono', monospace;
    font-size: 11px; font-weight: 700; letter-spacing: 0.04em;
    padding: 3px 7px; border-radius: 3px; white-space: nowrap;
    align-self: center; margin-top: 1px;
  }
  .entry-summary { font-size: 15px; color: #a8c8e0; margin: 0 0 10px; line-height: 1.75; }
  .entry-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .entry-source { font-size: 12px; color: var(--amber); text-decoration: none; border-bottom: 1px solid transparent; }
  .entry-source:hover { border-bottom-color: var(--amber); }
  .entry-more { list-style: none; margin: 10px 0 0; padding: 0; font-size: 13px; }
  .entry-more li { margin: 4px 0; }
  .entry-more a { color: var(--text-dim); text-decoration: none; }
  .entry-more a:hover { color: var(--amber); }
  .entry-meta { font-size: 11px; color: var(--text-dim); letter-spacing: 0.05em; }
  /* nav list */
  ul.nav-list { list-style: none; margin: 0; padding: 0; }
  .nav-item { border-bottom: 1px solid var(--line); }
  .nav-item:last-child { border-bottom: none; }
  .nav-link {
    display: flex; justify-content: space-between; align-items: center;
    padding: 18px 0; color: var(--text); text-decoration: none;
  }
  .nav-link:hover .nav-label-main,
  .nav-link:hover .nav-date { color: var(--amber); }
  .nav-label { display: flex; flex-direction: column; gap: 2px; }
  .nav-label-main { font-size: 16px; font-weight: 600; letter-spacing: 0.01em; }
  .nav-label-sub { font-size: 12px; color: var(--text-dim); letter-spacing: 0.05em; }
  .nav-date { font-size: 15px; letter-spacing: 0.05em; }
  .nav-arrow { color: var(--amber); font-size: 13px; }
  footer { margin-top: 40px; font-size: 11px; color: var(--text-dim); opacity: 0.7; }
"""

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono'
    ':wght@400;500;700&family=IBM+Plex+Sans+JP:wght@400;500;600&display=swap" rel="stylesheet">'
)

_DEFAULT_THEME = {"line": "#102840", "amber": "#2ecfba", "signal": "#00b8e6"}


def _css(theme: dict, bg: str = "#07101e") -> str:
    root = (
        "  :root {\n"
        f"    --bg: {bg};\n"
        f"    --line: {theme.get('line', '#102840')};\n"
        "    --text: #c8dff0;\n"
        "    --text-dim: #7298b8;\n"
        f"    --amber: {theme['amber']};\n"
        f"    --signal: {theme['signal']};\n"
        "  }\n"
        "  * { box-sizing: border-box; }\n"
    )
    return root + _CSS_BODY


def _resolve_theme(genre_cfg: dict | None, depth: int) -> dict:
    """depth: 0=top, 1=genre, 2=country, 3=article"""
    if not genre_cfg or "theme" not in genre_cfg:
        return _DEFAULT_THEME
    themes = genre_cfg["theme"]
    idx = max(0, min(depth - 1, len(themes) - 1))
    return themes[idx]


def _head(title: str, genre_cfg: dict | None = None, depth: int = 0) -> str:
    theme = _resolve_theme(genre_cfg, depth)
    bg = genre_cfg["bg"] if genre_cfg and "bg" in genre_cfg else "#07101e"
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f'<title>{title}</title>\n'
        + _FONTS + '\n'
        '<style>\n' + _css(theme, bg) + '</style>\n'
        '</head>\n<body>\n  <div class="wrap">\n'
    )


_FOOT = '    <footer class="mono">Generated daily by Claude &middot; claude-sonnet-4-6</footer>\n  </div>\n</body>\n</html>\n'


def _score_badge(score: int | None) -> str:
    if not score:
        return ""
    if score >= 80:
        bg, fg = "#2a0c0c", "#ff5555"
    elif score >= 60:
        bg, fg = "#201408", "#e89030"
    elif score >= 40:
        bg, fg = "#181808", "#c8b830"
    else:
        bg, fg = "#0c1420", "#5f7a96"
    return (
        f'<span class="entry-score" style="background:{bg};color:{fg}">'
        f'{score}<span style="opacity:0.55;font-size:9px">pt</span></span>'
    )


_ENTRY = """\
        <li class="entry">
          <span class="entry-index">{index}</span>
          <div class="entry-body">
            <div class="entry-title-row">
              <h2 class="entry-title">{title}</h2>
              {score_badge}
            </div>
            <p class="entry-summary">{summary}</p>
            <div class="entry-footer">
              <a class="entry-source" href="{url}" target="_blank" rel="noopener noreferrer">
                {source} <span aria-hidden="true">&#8599;</span>
              </a>
              {meta}
            </div>
            {extra}
          </div>
        </li>
"""


# ─── render functions ─────────────────────────────────────────────────────────

def render_article_page(
    stories: list[dict], date_str: str, time_str: str,
    country: dict, genre_cfg: dict,
) -> str:
    entries = ""
    for i, story in enumerate(stories, start=1):
        entries += _ENTRY.format(
            index=f"{i:02d}",
            title=html.escape(story.get("title_ja", "")),
            score_badge=_score_badge(story.get("score")),
            summary=html.escape(story.get("summary_ja", "")),
            source=html.escape(story.get("source", "")),
            url=html.escape(story.get("url", "#"), quote=True),
            meta="",
            extra="",
        )
    return _article_shell(entries, len(stories), date_str, time_str, country, genre_cfg)


def _article_shell(
    entries: str, count: int, date_str: str, time_str: str,
    country: dict, genre_cfg: dict,
) -> str:
    label_en = country["label_en"]
    label_en_genre = genre_cfg["label_en"]
    return (
        _head(f"AI WIRE — {date_str} — {label_en}", genre_cfg, depth=3)
        + '    <div class="masthead">\n'
        + f'      <span class="masthead-title mono">AI WIRE &#9656; {label_en_genre} &#9656; {label_en}</span>\n'
        + '      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>\n'
        + '    </div>\n'
        + '    <nav class="breadcrumb mono">\n'
        + '      <a href="../../index.html">ジャンル一覧</a>\n'
        + '      <span class="breadcrumb-sep">/</span>\n'
        + '      <a href="../index.html">国一覧</a>\n'
        + '      <span class="breadcrumb-sep">/</span>\n'
        + '      <a href="index.html">日付一覧</a>\n'
        + '    </nav>\n'
        + f'    <div class="meta-line mono">{date_str} &middot; {time_str} &middot; {count} dispatches</div>\n'
        + '    <ul class="entries">\n'
        + entries
        + '    </ul>\n'
        + _FOOT
    )


def render_country_index(dates: list[str], country: dict, genre_cfg: dict) -> str:
    label_en = country["label_en"]
    label_en_genre = genre_cfg["label_en"]

    items = ""
    for date in dates:
        items += (
            '      <li class="nav-item">\n'
            f'        <a href="{date}.html" class="nav-link mono">\n'
            f'          <span class="nav-date">{date}</span>\n'
            '          <span class="nav-arrow">&#8599;</span>\n'
            '        </a>\n'
            '      </li>\n'
        )

    return (
        _head(f"AI WIRE — {label_en}", genre_cfg, depth=2)
        + '    <div class="masthead">\n'
        + f'      <span class="masthead-title mono">AI WIRE &#9656; {label_en_genre} &#9656; {label_en}</span>\n'
        + '    </div>\n'
        + '    <nav class="breadcrumb mono">\n'
        + '      <a href="../../index.html">ジャンル一覧</a>\n'
        + '      <span class="breadcrumb-sep">/</span>\n'
        + '      <a href="../index.html">国一覧</a>\n'
        + '    </nav>\n'
        + '    <p class="section-label mono">ARCHIVE</p>\n'
        + '    <ul class="nav-list">\n'
        + items
        + '    </ul>\n'
        + _FOOT
    )


def render_genre_page(
    flat_stories: list[dict], date_str: str, time_str: str, genre_cfg: dict
) -> str:
    entries = ""
    for i, story in enumerate(flat_stories, start=1):
        country_label = story.get("_country_label_ja", "")
        story_date = story.get("_date_str", date_str)
        meta = f'<span class="entry-meta mono">{country_label} &middot; {story_date}</span>'
        entries += _ENTRY.format(
            index=f"{i:02d}",
            title=html.escape(story.get("title_ja", "")),
            score_badge=_score_badge(story.get("score")),
            summary=html.escape(story.get("summary_ja", "")),
            source=html.escape(story.get("source", "")),
            url=html.escape(story.get("url", "#"), quote=True),
            meta=meta,
            extra="",
        )
    return _genre_shell(entries, len(flat_stories), date_str, time_str, genre_cfg)


def _genre_shell(entries: str, count: int, date_str: str, time_str: str, genre_cfg: dict) -> str:
    label_ja = genre_cfg["label_ja"]
    label_en = genre_cfg["label_en"]
    return (
        _head(f"AI WIRE — {label_ja}", genre_cfg, depth=1)
        + '    <div class="masthead">\n'
        + f'      <span class="masthead-title mono">AI WIRE &#9656; {label_en}</span>\n'
        + '      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>\n'
        + '    </div>\n'
        + '    <a href="../index.html" class="back-link mono">&#8592; ジャンル一覧</a>\n'
        + '    <div class="genre-hero">\n'
        + f'      <h1 class="genre-hero-title">{label_ja}</h1>\n'
        + f'      <p class="genre-hero-sub mono">{label_en}</p>\n'
        + '    </div>\n'
        + f'    <div class="meta-line mono">{date_str} &middot; {time_str} &middot; {count} dispatches</div>\n'
        + '    <ul class="entries">\n'
        + entries
        + '    </ul>\n'
        + _FOOT
    )


def _trend_entry(i: int, trend: dict, meta: str) -> str:
    articles = trend["articles"]
    first = articles[0] if articles else {}
    summary = " ".join(x for x in (trend["reason_ja"], first.get("summary_ja", "")) if x)
    extra = ""
    if len(articles) > 1:
        extra = '<ul class="entry-more">' + "".join(
            f'<li><a href="{html.escape(a["url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(a.get("title_ja") or a["url"])}'
            f'{" — " + html.escape(a["source"]) if a.get("source") else ""}</a></li>'
            for a in articles[1:]
        ) + "</ul>"
    genre_label = GENRES[trend["genre"]]["label_ja"] if trend["genre"] in GENRES else "その他"
    meta_html = f'<span class="entry-meta mono">{" &middot; ".join(x for x in (meta, genre_label) if x)}</span>'
    return _ENTRY.format(
        index=f"{i:02d}",
        title=html.escape(trend["keyword"]),
        score_badge=_score_badge(trend["commercial_score"]),
        summary=html.escape(summary),
        source=html.escape(first.get("source", "")),
        url=html.escape(first.get("url", "#"), quote=True),
        meta=meta_html,
        extra=extra,
    )


def render_trends_article_page(
    trends: list[dict], date_str: str, time_str: str, country: dict,
) -> str:
    entries = "".join(_trend_entry(i, t, "") for i, t in enumerate(trends, start=1))
    return _article_shell(entries, len(trends), date_str, time_str, country, TRENDS)


def render_trends_page(flat_trends: list[dict], date_str: str, time_str: str) -> str:
    entries = "".join(
        _trend_entry(i, t, t["_country_label_ja"]) for i, t in enumerate(flat_trends, start=1)
    )
    return _genre_shell(entries, len(flat_trends), date_str, time_str, TRENDS)


def render_top_index() -> str:
    items = ""
    for gcode, g in {TRENDS_CODE: TRENDS, **GENRES}.items():
        color = g["theme"][0]["amber"]
        items += (
            '      <li class="nav-item">\n'
            f'        <a href="{gcode}/index.html" class="nav-link">\n'
            '          <span class="nav-label">\n'
            f'            <span class="nav-label-main" style="color:{color}">{g["label_ja"]}</span>\n'
            f'            <span class="nav-label-sub mono" style="color:{color};opacity:0.65">{g["label_en"]}</span>\n'
            '          </span>\n'
            f'          <span class="nav-arrow mono" style="color:{color}">&#8599;</span>\n'
            '        </a>\n'
            '      </li>\n'
        )

    return (
        _head("AI WIRE")
        + '    <div class="masthead">\n'
        + '      <span class="masthead-title mono">AI WIRE</span>\n'
        + '      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>\n'
        + '    </div>\n'
        + '    <p class="section-label mono">GENRE</p>\n'
        + '    <ul class="nav-list">\n'
        + items
        + '    </ul>\n'
        + _FOOT
    )


def _dated_files(directory: str, ext: str) -> list[str]:
    """YYYY-MM-DD stems of the dated files in a directory, newest first."""
    pattern = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}\.{ext}")
    return sorted(
        (
            os.path.basename(p)[: -len(ext) - 1]
            for p in glob.glob(f"{directory}/*.{ext}")
            if pattern.fullmatch(os.path.basename(p))
        ),
        reverse=True,
    )


def _rebuild_country_index(country_dir: str, country: dict, cfg: dict) -> None:
    with open(f"{country_dir}/index.html", "w", encoding="utf-8") as f:
        f.write(render_country_index(_dated_files(country_dir, "html"), country, cfg))


# ─── JSON export ──────────────────────────────────────────────────────────────

STORY_FIELDS = (
    "title_ja", "summary_ja", "source", "url", "score",
    "commercial_score", "product_keywords",
)
ARTICLE_FIELDS = ("title_ja", "summary_ja", "source", "url")


def _clean_story(story: dict, fields: tuple[str, ...] = STORY_FIELDS) -> dict:
    return {k: story[k] for k in fields if story.get(k) not in (None, "", [])}


def _write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_top_index_json(generated_at: str) -> None:
    """docs/index.json (genre catalog) and docs/latest.json (all genres' latest stories)."""
    genres = []
    all_stories = []
    for gcode, g in GENRES.items():
        latest = _read_json(f"docs/{gcode}/latest.json")
        countries = []
        for c in g["countries"]:
            dates = _dated_files(f"docs/{gcode}/{c['code']}", "json")
            countries.append({
                "code": c["code"],
                "label_ja": c["label_ja"],
                "dates": dates,
            })
        genres.append({
            "code": gcode,
            "label_ja": g["label_ja"],
            "label_en": g["label_en"],
            "latest_date": latest.get("date") if latest else None,
            "latest_story_count": len(latest.get("stories", [])) if latest else 0,
            "latest_path": f"{gcode}/latest.json" if latest else None,
            "countries": countries,
        })
        if latest:
            for story in latest.get("stories", []):
                all_stories.append({
                    **story,
                    "genre": gcode,
                    "genre_label_ja": g["label_ja"],
                    "date": latest.get("date"),
                })

    trends_latest = _read_json(f"docs/{TRENDS_CODE}/latest.json")
    trends_entry = {
        "code": TRENDS_CODE,
        "label_ja": TRENDS["label_ja"],
        "label_en": TRENDS["label_en"],
        "latest_date": trends_latest.get("date") if trends_latest else None,
        "latest_trend_count": len(trends_latest.get("trends", [])) if trends_latest else 0,
        "latest_path": f"{TRENDS_CODE}/latest.json" if trends_latest else None,
        "countries": [
            {"code": c["code"], "label_ja": c["label_ja"],
             "dates": _dated_files(f"docs/{TRENDS_CODE}/{c['code']}", "json")}
            for c in TRENDS["countries"]
        ],
    }
    all_trends = [
        {**t, "date": trends_latest.get("date")}
        for t in (trends_latest.get("trends", []) if trends_latest else [])
    ]

    _write_json("docs/index.json", {
        "generated_at": generated_at, "trends": trends_entry, "genres": genres,
    })
    _write_json("docs/latest.json", {
        "generated_at": generated_at, "trends": all_trends, "stories": all_stories,
    })
    print("Wrote docs/index.json and docs/latest.json")


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate news dashboard for a genre.")
    parser.add_argument("--genre", required=True, choices=[TRENDS_CODE, *GENRES])
    parser.add_argument(
        "--input", metavar="FILE",
        help="JSON file with stories (Claude-dispatch mode). "
             "When omitted, stories are fetched via the Anthropic API.",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Edition date for the article page (default: today in JST).",
    )
    args = parser.parse_args()
    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        parser.error("--date must be YYYY-MM-DD")

    if args.genre == TRENDS_CODE:
        if not args.input:
            parser.error("--genre trends requires --input")
        run_trends(args.input, args.date)
        return

    genre_code = args.genre
    genre_cfg = GENRES[genre_code]

    try:
        if args.input:
            all_stories = load_stories_from_file(args.input, genre_cfg)
        else:
            all_stories = fetch_all_stories(genre_cfg)
    except Exception as exc:
        print(f"Failed to load stories: {exc}", file=sys.stderr)
        sys.exit(1)

    now_jst = datetime.now(JST)
    date_str = args.date or now_jst.strftime("%Y-%m-%d")
    time_str = now_jst.strftime("%H:%M JST")
    generated_at = now_jst.isoformat(timespec="seconds")

    # Flatten stories from all countries, attaching country label and date for display
    flat_stories: list[dict] = []
    for country in genre_cfg["countries"]:
        code = country["code"]
        stories = all_stories.get(code, [])
        if not stories:
            print(f"Skipping {code}: no stories", file=sys.stderr)
            continue

        # Write per-country/date archive page
        country_dir = f"docs/{genre_code}/{code}"
        os.makedirs(country_dir, exist_ok=True)
        article_path = f"{country_dir}/{date_str}.html"
        with open(article_path, "w", encoding="utf-8") as f:
            f.write(render_article_page(stories, date_str, time_str, country, genre_cfg))
        print(f"Wrote {len(stories)} stories to {article_path}")

        json_path = f"{country_dir}/{date_str}.json"
        _write_json(json_path, {
            "genre": genre_code,
            "genre_label_ja": genre_cfg["label_ja"],
            "country": code,
            "country_label_ja": country["label_ja"],
            "date": date_str,
            "generated_at": generated_at,
            "stories": [_clean_story(s) for s in stories],
        })
        print(f"Wrote {json_path}")

        _rebuild_country_index(country_dir, country, genre_cfg)

        for story in stories:
            enriched = dict(story)
            enriched["_country_code"] = code
            enriched["_country_label_ja"] = country["label_ja"]
            enriched["_date_str"] = date_str
            flat_stories.append(enriched)

    # Sort by score descending; stories without a score go to the end
    flat_stories.sort(key=lambda s: s.get("score") or 0, reverse=True)

    genre_index_path = f"docs/{genre_code}/index.html"
    os.makedirs(f"docs/{genre_code}", exist_ok=True)
    with open(genre_index_path, "w", encoding="utf-8") as f:
        f.write(render_genre_page(flat_stories, date_str, time_str, genre_cfg))
    print(f"Wrote genre index ({len(flat_stories)} stories) to {genre_index_path}")

    latest_path = f"docs/{genre_code}/latest.json"
    _write_json(latest_path, {
        "genre": genre_code,
        "genre_label_ja": genre_cfg["label_ja"],
        "date": date_str,
        "generated_at": generated_at,
        "stories": [
            {**_clean_story(s), "country": s["_country_code"], "country_label_ja": s["_country_label_ja"]}
            for s in flat_stories
        ],
    })
    print(f"Wrote {latest_path}")

    write_top_indexes(generated_at)


def write_top_indexes(generated_at: str) -> None:
    top_index_path = "docs/index.html"
    with open(top_index_path, "w", encoding="utf-8") as f:
        f.write(render_top_index())
    print(f"Wrote top index to {top_index_path}")
    write_top_index_json(generated_at)


def run_trends(input_path: str, date_arg: str | None) -> None:
    try:
        all_trends = load_trends_from_file(input_path)
    except Exception as exc:
        print(f"Failed to load trends: {exc}", file=sys.stderr)
        sys.exit(1)

    now_jst = datetime.now(JST)
    date_str = date_arg or now_jst.strftime("%Y-%m-%d")
    time_str = now_jst.strftime("%H:%M JST")
    generated_at = now_jst.isoformat(timespec="seconds")

    flat_trends: list[dict] = []
    for country in TRENDS["countries"]:
        code = country["code"]
        trends = all_trends.get(code, [])
        if not trends:
            print(f"Skipping {code}: no trends", file=sys.stderr)
            continue

        country_dir = f"docs/{TRENDS_CODE}/{code}"
        os.makedirs(country_dir, exist_ok=True)
        with open(f"{country_dir}/{date_str}.html", "w", encoding="utf-8") as f:
            f.write(render_trends_article_page(trends, date_str, time_str, country))
        _write_json(f"{country_dir}/{date_str}.json", {
            "feed": TRENDS_CODE,
            "country": code,
            "country_label_ja": country["label_ja"],
            "date": date_str,
            "generated_at": generated_at,
            "trends": trends,
        })
        print(f"Wrote {len(trends)} trends to {country_dir}/{date_str}.html|json")
        _rebuild_country_index(country_dir, country, TRENDS)

        for t in trends:
            flat_trends.append({**t, "_country_code": code, "_country_label_ja": country["label_ja"]})

    flat_trends.sort(key=lambda t: t["commercial_score"] or 0, reverse=True)

    with open(f"docs/{TRENDS_CODE}/index.html", "w", encoding="utf-8") as f:
        f.write(render_trends_page(flat_trends, date_str, time_str))
    _write_json(f"docs/{TRENDS_CODE}/latest.json", {
        "feed": TRENDS_CODE,
        "date": date_str,
        "generated_at": generated_at,
        "trends": [
            {**{k: v for k, v in t.items() if not k.startswith("_")},
             "country": t["_country_code"], "country_label_ja": t["_country_label_ja"]}
            for t in flat_trends
        ],
    })
    print(f"Wrote trends index ({len(flat_trends)} trends) and docs/{TRENDS_CODE}/latest.json")

    write_top_indexes(generated_at)


if __name__ == "__main__":
    main()
