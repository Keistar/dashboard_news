"""
Generates docs/index.html: a daily AI-industry news dashboard (US edition),
built by asking Claude to search the web and summarize the day's top stories.

Run via GitHub Actions (see .github/workflows/daily-ai-news.yml).
Requires the ANTHROPIC_API_KEY environment variable.
"""

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
OUTPUT_PATH = "docs/index.html"

JST = timezone(timedelta(hours=9))

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
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    today_jst = datetime.now(JST).strftime("%Y年%m月%d日")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 6,  # caps search cost per run
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

    # Strip code fences the model may add despite being told not to.
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    # Extract the outermost JSON object in case the model adds surrounding text.
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


ENTRY_TEMPLATE = """
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

PAGE_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI WIRE — US Edition</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=IBM+Plex+Sans+JP:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #1c1b17;
    --line: #3a3727;
    --text: #f1ecdd;
    --text-dim: #b7ae96;
    --amber: #f2a93b;
    --signal: #6fbe8f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans JP', -apple-system, sans-serif;
    line-height: 1.7;
  }}
  .mono {{ font-family: 'JetBrains Mono', monospace; }}
  .wrap {{
    max-width: 720px;
    margin: 0 auto;
    padding: 32px 20px 64px;
  }}
  .masthead {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid var(--line);
    padding-bottom: 16px;
    margin-bottom: 8px;
  }}
  .masthead-title {{
    font-size: 14px;
    letter-spacing: 0.12em;
    color: var(--amber);
    font-weight: 700;
  }}
  .live-tag {{
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--signal);
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }}
  .live-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--signal);
    animation: pulse 2.4s ease-in-out infinite;
  }}
  @media (prefers-reduced-motion: reduce) {{
    .live-dot {{ animation: none; }}
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.25; }}
  }}
  .meta-line {{
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 28px;
  }}
  ul.entries {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}
  .entry {{
    display: flex;
    gap: 16px;
    padding: 20px 0;
    border-bottom: 1px solid var(--line);
  }}
  .entry:last-child {{ border-bottom: none; }}
  .entry-index {{
    flex: 0 0 auto;
    color: var(--amber);
    font-size: 13px;
    padding-top: 3px;
  }}
  .entry-title {{
    font-size: 16px;
    font-weight: 600;
    margin: 0 0 8px;
    letter-spacing: 0.01em;
  }}
  .entry-summary {{
    font-size: 14px;
    color: var(--text-dim);
    margin: 0 0 10px;
  }}
  .entry-source {{
    font-size: 12px;
    color: var(--amber);
    text-decoration: none;
    border-bottom: 1px solid transparent;
  }}
  .entry-source:hover {{ border-bottom-color: var(--amber); }}
  footer {{
    margin-top: 40px;
    font-size: 11px;
    color: var(--text-dim);
    opacity: 0.7;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="masthead">
      <span class="masthead-title mono">AI WIRE &#9656; US EDITION</span>
      <span class="live-tag mono"><span class="live-dot"></span>LIVE</span>
    </div>
    <div class="meta-line mono">{date_label} &middot; {time_label} &middot; {count} dispatches</div>
    <ul class="entries">
{entries}
    </ul>
    <footer class="mono">Generated daily by Claude &middot; claude-sonnet-4-6</footer>
  </div>
</body>
</html>
"""


def render_html(stories: list[dict]) -> str:
    now_jst = datetime.now(JST)
    date_label = now_jst.strftime("%Y-%m-%d")
    time_label = now_jst.strftime("%H:%M JST")

    entries_html = ""
    for i, story in enumerate(stories, start=1):
        title = html.escape(story.get("title_ja", ""))
        summary = html.escape(story.get("summary_ja", ""))
        source = html.escape(story.get("source", ""))
        url = html.escape(story.get("url", "#"), quote=True)
        entries_html += ENTRY_TEMPLATE.format(
            index=f"{i:02d}", title=title, summary=summary, source=source, url=url
        )

    return PAGE_TEMPLATE.format(
        date_label=date_label,
        time_label=time_label,
        count=len(stories),
        entries=entries_html,
    )


def main() -> None:
    try:
        stories = fetch_stories()
    except Exception as exc:  # noqa: BLE001 - want to fail the job loudly either way
        print(f"Failed to fetch stories: {exc}", file=sys.stderr)
        sys.exit(1)

    page = render_html(stories)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Wrote {len(stories)} stories to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
