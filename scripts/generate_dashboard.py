"""
Generates a multi-page news dashboard.

Page structure:
  docs/index.html                           — top page: genre list
  docs/{genre}/index.html                   — genre page: country list
  docs/{genre}/{country}/index.html         — country page: date list (newest first)
  docs/{genre}/{country}/{date}.html        — article page: daily stories

Usage:
  python scripts/generate_dashboard.py --genre tech
  python scripts/generate_dashboard.py --genre economy
  python scripts/generate_dashboard.py --genre entertainment

Run via GitHub Actions (see .github/workflows/).
Requires the ANTHROPIC_API_KEY environment variable.
"""

import argparse
import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import anthropic
from json_repair import repair_json

MODEL = "claude-sonnet-4-6"
MAX_STORIES = 8
MAX_TOKENS = 32000
WEB_SEARCH_MAX_USES = 24  # ~4 searches per country × 6 countries
MAX_RETRIES = 3

JST = timezone(timedelta(hours=9))

GENRES = {
    "tech": {
        "label_ja": "IT・科学",
        "label_en": "TECH & SCIENCE",
        # theme[0]=genre page, theme[1]=country page, theme[2]=article page (muted→vivid)
        "bg": "#061614",
        "theme": [
            {"line": "#0b2035", "amber": "#1b9688", "signal": "#007fb8"},
            {"line": "#102840", "amber": "#2ecfba", "signal": "#00b8e6"},
            {"line": "#133858", "amber": "#50e4d0", "signal": "#33ccf5"},
        ],
        "system_prompt_intro": (
            "You are a global news curator producing a daily AI and tech briefing "
            "for a Japanese software engineer who reads it every morning. Use the web_search tool to "
            "find the most significant AI and tech news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "model releases, funding rounds, major product launches, "
            "regulation, notable research, key personnel moves"
        ),
        "user_message_ja": "AI・テック業界の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": [
            {"code": "us", "label_ja": "アメリカ",   "label_en": "US EDITION", "name_en": "United States"},
            {"code": "il", "label_ja": "イスラエル", "label_en": "IL EDITION", "name_en": "Israel"},
            {"code": "cn", "label_ja": "中国",       "label_en": "CN EDITION", "name_en": "China"},
            {"code": "in", "label_ja": "インド",     "label_en": "IN EDITION", "name_en": "India"},
            {"code": "gb", "label_ja": "イギリス",   "label_en": "GB EDITION", "name_en": "United Kingdom"},
            {"code": "ee", "label_ja": "エストニア", "label_en": "EE EDITION", "name_en": "Estonia"},
        ],
    },
    "economy": {
        "label_ja": "経済",
        "label_en": "ECONOMY",
        "bg": "#0f0d07",
        "theme": [
            {"line": "#1e1a06", "amber": "#a07d1e", "signal": "#b09012"},
            {"line": "#2a2208", "amber": "#e8b84b", "signal": "#f5c518"},
            {"line": "#342a08", "amber": "#f5cc70", "signal": "#ffd94a"},
        ],
        "system_prompt_intro": (
            "You are a global news curator producing a daily economics and finance briefing "
            "for a Japanese reader who follows global markets. Use the web_search tool to "
            "find the most significant economic and financial news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "stock markets, trade policy, corporate earnings, GDP data, "
            "central bank decisions, employment, major M&A"
        ),
        "user_message_ja": "経済・金融業界の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": [
            {"code": "us", "label_ja": "アメリカ",   "label_en": "US EDITION", "name_en": "United States"},
            {"code": "cn", "label_ja": "中国",       "label_en": "CN EDITION", "name_en": "China"},
            {"code": "jp", "label_ja": "日本",       "label_en": "JP EDITION", "name_en": "Japan"},
            {"code": "de", "label_ja": "ドイツ",     "label_en": "DE EDITION", "name_en": "Germany"},
            {"code": "gb", "label_ja": "イギリス",   "label_en": "GB EDITION", "name_en": "United Kingdom"},
            {"code": "in", "label_ja": "インド",     "label_en": "IN EDITION", "name_en": "India"},
        ],
    },
    "entertainment": {
        "label_ja": "エンタメ・芸能",
        "label_en": "ENTERTAINMENT",
        "bg": "#0d0712",
        "theme": [
            {"line": "#1c0828", "amber": "#8e24b4", "signal": "#cc3570"},
            {"line": "#220a32", "amber": "#e040fb", "signal": "#ff6b9d"},
            {"line": "#2a0c3c", "amber": "#ef72ff", "signal": "#ff9ec0"},
        ],
        "system_prompt_intro": (
            "You are a global news curator producing a daily entertainment and celebrity briefing "
            "for a Japanese reader interested in pop culture worldwide. Use the web_search tool to "
            "find the most significant entertainment and celebrity news that broke in the last 24-48 hours"
        ),
        "story_types": (
            "movies, music releases, TV and streaming, celebrities, "
            "award shows, concerts, pop culture trends"
        ),
        "user_message_ja": "エンタメ・芸能界の直近ビッグニュースを調べて、指定したJSON形式で返してください。",
        "countries": [
            {"code": "jp", "label_ja": "日本",       "label_en": "JP EDITION", "name_en": "Japan"},
            {"code": "us", "label_ja": "アメリカ",   "label_en": "US EDITION", "name_en": "United States"},
            {"code": "kr", "label_ja": "韓国",       "label_en": "KR EDITION", "name_en": "South Korea"},
            {"code": "in", "label_ja": "インド",     "label_en": "IN EDITION", "name_en": "India"},
            {"code": "gb", "label_ja": "イギリス",   "label_en": "GB EDITION", "name_en": "United Kingdom"},
            {"code": "cn", "label_ja": "中国",       "label_en": "CN EDITION", "name_en": "China"},
        ],
    },
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
          "url": "string, direct URL to the original article"
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


def _call_api(client: anthropic.Anthropic, system_prompt: str, user_message: str) -> str:
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": WEB_SEARCH_MAX_USES,
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        response = stream.get_final_message()

    text_blocks = [block.text for block in response.content if block.type == "text"]
    raw = "".join(text_blocks).strip()

    if not raw:
        block_types = [block.type for block in response.content]
        raise ValueError(
            f"Model returned no text blocks (stop_reason={response.stop_reason!r}, "
            f"block types={block_types})"
        )

    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON object found in response (first 300 chars): {raw[:300]!r}")
    return json_match.group(0)


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON parse failed ({exc}); attempting repair…", file=sys.stderr)
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
        raise ValueError(
            f"JSON repair also failed; raw (first 300 chars): {raw[:300]!r}"
        ) from exc


def fetch_all_stories(genre_cfg: dict) -> dict[str, list[dict]]:
    client = anthropic.Anthropic()

    today_jst = datetime.now(JST).strftime("%Y年%m月%d日")
    system_prompt = build_system_prompt(genre_cfg)
    user_message = build_user_message(genre_cfg, today_jst)

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = _call_api(client, system_prompt, user_message)
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
  .entry-title { font-size: 16px; font-weight: 600; margin: 0 0 8px; letter-spacing: 0.01em; }
  .entry-summary { font-size: 15px; color: #a8c8e0; margin: 0 0 10px; line-height: 1.75; }
  .entry-source { font-size: 12px; color: var(--amber); text-decoration: none; border-bottom: 1px solid transparent; }
  .entry-source:hover { border-bottom-color: var(--amber); }
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

_ENTRY = """\
        <li class="entry">
          <span class="entry-index">{index}</span>
          <div class="entry-body">
            <h2 class="entry-title">{title}</h2>
            <p class="entry-summary">{summary}</p>
            <a class="entry-source" href="{url}" target="_blank" rel="noopener noreferrer">
              {source} <span aria-hidden="true">&#8599;</span>
            </a>
          </div>
        </li>
"""


# ─── render functions ─────────────────────────────────────────────────────────

def render_article_page(
    stories: list[dict], date_str: str, time_str: str,
    country: dict, genre_cfg: dict,
) -> str:
    label_en = country["label_en"]
    label_en_genre = genre_cfg["label_en"]

    entries = ""
    for i, story in enumerate(stories, start=1):
        entries += _ENTRY.format(
            index=f"{i:02d}",
            title=html.escape(story.get("title_ja", "")),
            summary=html.escape(story.get("summary_ja", "")),
            source=html.escape(story.get("source", "")),
            url=html.escape(story.get("url", "#"), quote=True),
        )

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
        + f'    <div class="meta-line mono">{date_str} &middot; {time_str} &middot; {len(stories)} dispatches</div>\n'
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


def render_genre_index(genre_cfg: dict) -> str:
    label_ja = genre_cfg["label_ja"]
    label_en = genre_cfg["label_en"]

    items = ""
    for c in genre_cfg["countries"]:
        items += (
            '      <li class="nav-item">\n'
            f'        <a href="{c["code"]}/index.html" class="nav-link">\n'
            '          <span class="nav-label">\n'
            f'            <span class="nav-label-main">{c["label_ja"]}</span>\n'
            f'            <span class="nav-label-sub mono">{c["label_en"]}</span>\n'
            '          </span>\n'
            '          <span class="nav-arrow mono">&#8599;</span>\n'
            '        </a>\n'
            '      </li>\n'
        )

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
        + '    <p class="section-label mono">EDITION</p>\n'
        + '    <ul class="nav-list">\n'
        + items
        + '    </ul>\n'
        + _FOOT
    )


def render_top_index() -> str:
    items = ""
    for gcode, g in GENRES.items():
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


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate news dashboard for a genre.")
    parser.add_argument("--genre", required=True, choices=list(GENRES.keys()))
    args = parser.parse_args()

    genre_code = args.genre
    genre_cfg = GENRES[genre_code]

    try:
        all_stories = fetch_all_stories(genre_cfg)
    except Exception as exc:
        print(f"Failed to fetch stories: {exc}", file=sys.stderr)
        sys.exit(1)

    now_jst = datetime.now(JST)
    date_str = now_jst.strftime("%Y-%m-%d")
    time_str = now_jst.strftime("%H:%M JST")

    for country in genre_cfg["countries"]:
        code = country["code"]
        stories = all_stories.get(code, [])
        if not stories:
            print(f"Skipping {code}: no stories", file=sys.stderr)
            continue

        country_dir = f"docs/{genre_code}/{code}"
        os.makedirs(country_dir, exist_ok=True)

        article_path = f"{country_dir}/{date_str}.html"
        with open(article_path, "w", encoding="utf-8") as f:
            f.write(render_article_page(stories, date_str, time_str, country, genre_cfg))
        print(f"Wrote {len(stories)} stories to {article_path}")

        date_files = sorted(
            glob.glob(f"{country_dir}/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"),
            reverse=True,
        )
        dates = [os.path.basename(p).replace(".html", "") for p in date_files]
        country_index_path = f"{country_dir}/index.html"
        with open(country_index_path, "w", encoding="utf-8") as f:
            f.write(render_country_index(dates, country, genre_cfg))
        print(f"Wrote country index ({len(dates)} dates) to {country_index_path}")

    genre_index_path = f"docs/{genre_code}/index.html"
    os.makedirs(f"docs/{genre_code}", exist_ok=True)
    with open(genre_index_path, "w", encoding="utf-8") as f:
        f.write(render_genre_index(genre_cfg))
    print(f"Wrote genre index to {genre_index_path}")

    top_index_path = "docs/index.html"
    with open(top_index_path, "w", encoding="utf-8") as f:
        f.write(render_top_index())
    print(f"Wrote top index to {top_index_path}")


if __name__ == "__main__":
    main()
