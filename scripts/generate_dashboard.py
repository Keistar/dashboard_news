"""
Generates a multi-page AI news dashboard.

Page structure:
  docs/index.html              — top page: country list
  docs/{country}/index.html    — country page: date list (newest first)
  docs/{country}/{date}.html   — article page: daily stories

Run via GitHub Actions (see .github/workflows/daily-ai-news.yml).
Requires the ANTHROPIC_API_KEY environment variable.
"""

import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_STORIES = 8
MAX_TOKENS = 16000

JST = timezone(timedelta(hours=9))

COUNTRY_CODE = "us"
COUNTRY_LABEL_JA = "アメリカ"
COUNTRY_LABEL_EN = "US EDITION"

SYSTEM_PROMPT = f"""You are a news curator producing a daily briefing about \
the US AI industry for a Japanese software engineer who reads it every \
morning before work. Use the web_search tool to find the most significant \
AI-related news that broke in the United States in roughly the last 24-48 \
hours: model releases, funding rounds, major product launches, regulation, \
notable research, or key personnel moves at AI companies.

Pick the {MAX_STORIES} most important stories. For each one, write the \
title and summary in natural, concise Japanese, as if briefing a busy \
engineer who has two minutes to read.

Respond with ONLY valid JSON, no markdown code fences, no commentary \
before or after, matching exactly this schema:

{{
  "stories": [
    {{
      "title_ja": "string, <=40 characters, no trailing period",
      "summary_ja": "string, 2-3 sentences: what happened and why it matters",
      "source": "string, name of the original publication",
      "url": "string, direct URL to the original article"
    }}
  ]
}}
"""


def fetch_stories() -> list[dict]:
    client = anthropic.Anthropic()

    today_jst = datetime.now(JST).strftime("%Y年%m月%d日")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 6,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"今日は{today_jst}（日本時間）です。"
                    "アメリカのAI業界に関する直近のビッグニュースを調べて、"
                    "指定したJSON形式で返してください。"
                ),
            }
        ],
    )

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
    raw = json_match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error ({exc}); raw (first 300 chars): {raw[:300]!r}") from exc
    stories = data.get("stories", [])[:MAX_STORIES]
    if not stories:
        raise ValueError("No stories returned by the model")
    return stories


# ─── shared CSS ───────────────────────────────────────────────────────────────

_CSS = """
  :root {
    --bg: #07101e;
    --line: #102840;
    --text: #c8dff0;
    --text-dim: #527898;
    --amber: #2ecfba;
    --signal: #00b8e6;
  }
  * { box-sizing: border-box; }
  body {
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
    text-decoration: none; margin: 12px 0 20px; letter-spacing: 0.05em;
  }
  .back-link:hover { color: var(--amber); }
  .breadcrumb {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; color: var(--text-dim); margin: 12px 0 20px; letter-spacing: 0.05em;
  }
  .breadcrumb a { color: var(--text-dim); text-decoration: none; }
  .breadcrumb a:hover { color: var(--amber); }
  .breadcrumb-sep { color: var(--line); }
  .section-label { font-size: 11px; letter-spacing: 0.1em; color: var(--text-dim); margin-bottom: 4px; }
  .meta-line { font-size: 12px; color: var(--text-dim); margin-bottom: 28px; }
  /* article list */
  ul.entries { list-style: none; margin: 0; padding: 0; }
  .entry { display: flex; gap: 16px; padding: 20px 0; border-bottom: 1px solid var(--line); }
  .entry:last-child { border-bottom: none; }
  .entry-index { flex: 0 0 auto; color: var(--amber); font-size: 13px; padding-top: 3px; }
  .entry-title { font-size: 16px; font-weight: 600; margin: 0 0 8px; letter-spacing: 0.01em; }
  .entry-summary { font-size: 14px; color: var(--text-dim); margin: 0 0 10px; }
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


def _head(title: str) -> str:
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f'<title>{title}</title>\n'
        + _FONTS + '\n'
        '<style>\n' + _CSS + '</style>\n'
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

def render_article_page(stories: list[dict], date_str: str, time_str: str) -> str:
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
        _head(f"AI WIRE — {date_str} — {COUNTRY_LABEL_EN}")
        + '    <div class="masthead">\n'
        + f'      <span class="masthead-title mono">AI WIRE &#9656; {COUNTRY_LABEL_EN}</span>\n'
        + '      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>\n'
        + '    </div>\n'
        + '    <nav class="breadcrumb mono">\n'
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


def render_country_index(dates: list[str]) -> str:
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
        _head(f"AI WIRE — {COUNTRY_LABEL_EN}")
        + '    <div class="masthead">\n'
        + f'      <span class="masthead-title mono">AI WIRE &#9656; {COUNTRY_LABEL_EN}</span>\n'
        + '    </div>\n'
        + '    <a href="../index.html" class="back-link mono">&#8592; 国一覧</a>\n'
        + '    <p class="section-label mono">ARCHIVE</p>\n'
        + '    <ul class="nav-list">\n'
        + items
        + '    </ul>\n'
        + _FOOT
    )


def render_top_index() -> str:
    # Add more countries here as needed in the future.
    countries = [
        {"code": COUNTRY_CODE, "label_ja": COUNTRY_LABEL_JA, "label_en": COUNTRY_LABEL_EN},
    ]

    items = ""
    for c in countries:
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
        _head("AI WIRE")
        + '    <div class="masthead">\n'
        + '      <span class="masthead-title mono">AI WIRE</span>\n'
        + '      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>\n'
        + '    </div>\n'
        + '    <p class="section-label mono">EDITION</p>\n'
        + '    <ul class="nav-list">\n'
        + items
        + '    </ul>\n'
        + _FOOT
    )


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        stories = fetch_stories()
    except Exception as exc:
        print(f"Failed to fetch stories: {exc}", file=sys.stderr)
        sys.exit(1)

    now_jst = datetime.now(JST)
    date_str = now_jst.strftime("%Y-%m-%d")
    time_str = now_jst.strftime("%H:%M JST")

    country_dir = f"docs/{COUNTRY_CODE}"
    os.makedirs(country_dir, exist_ok=True)

    # Save article page for today.
    article_path = f"{country_dir}/{date_str}.html"
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(render_article_page(stories, date_str, time_str))
    print(f"Wrote {len(stories)} stories to {article_path}")

    # Rebuild country index from all date files (newest first).
    date_files = sorted(
        glob.glob(f"{country_dir}/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"),
        reverse=True,
    )
    dates = [os.path.basename(p).replace(".html", "") for p in date_files]
    country_index_path = f"{country_dir}/index.html"
    with open(country_index_path, "w", encoding="utf-8") as f:
        f.write(render_country_index(dates))
    print(f"Wrote country index ({len(dates)} dates) to {country_index_path}")

    # Rebuild top index.
    top_index_path = "docs/index.html"
    with open(top_index_path, "w", encoding="utf-8") as f:
        f.write(render_top_index())
    print(f"Wrote top index to {top_index_path}")


if __name__ == "__main__":
    main()
