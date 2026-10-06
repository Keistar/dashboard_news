# AI News Dashboard (US Edition)

毎朝6:00（JST）に、ClaudeがWeb検索でアメリカ発のAI業界ニュースをピックアップし、
`docs/index.html` のダッシュボードを自動更新します。

## セットアップ

1. GitHubに新しいリポジトリを作成し、ここにあるファイル一式を追加する
2. Anthropic Consoleで新しいAPIキーを発行する
   （例: `ai-news-dashboard-us`。チャットなど平文の場所には貼らないこと）
3. リポジトリの **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY_NEWS_US`
   - Value: 発行したキー
4. **Settings → Actions → General → Workflow permissions** で
   「Read and write permissions」を選択して保存
   （ワークフローが自動コミットするために必要）
5. **Settings → Pages**
   - Source: `Deploy from a branch`
   - Branch: `main` ／ フォルダ: `/docs`
6. **Actions** タブ → `Daily AI News Dashboard` → `Run workflow` で試し実行

成功すると数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で見られます。

## 仕組み

| ファイル | 役割 |
|---|---|
| `.github/workflows/daily-ai-news.yml` | 毎日21:00 UTC（=6:00 JST）に起動するスケジューラ |
| `scripts/generate_dashboard.py` | Claudeにweb_searchツールで検索させ、JSONで受け取ってHTML化 |
| `docs/index.html` | 生成されたダッシュボード本体（GitHub Pagesで公開） |
| `requirements.txt` | 必要なPythonパッケージ（`anthropic` SDK） |

## ジャンル

対象国は **日本・アメリカ・イギリス・中国（jp / us / gb / cn）** です。セール・キャンペーンだけは日本のみです。

ジャンルは LogiK2 のキャラの発信テーマに合わせています（2026-10-07。割り当ては logik2-app-central の `docs/design/x-accounts.md` の「発信テーマ」）。

| コード | ジャンル | キャラ |
|---|---|---|
| `ai` | AI | レン |
| `gadget` | ガジェット | レン |
| `kaji` | 時短家電・家事 | シホ |
| `beauty` | 美容・コスメ | アイリス |
| `food` | 食品・グルメ | カオル |
| `health` | 健康・フィットネス | アイリス |
| `outdoor` | アウトドア | ゴウ |
| `sale` | セール・キャンペーン（日本のみ） | ルビー |
| `interior` | 収納・インテリア・文具 | シオン |
| `trends`（下の「トレンド枠」） | トレンド | プロト |

2026-10-07 に、子育て（`kosodate`）を時短家電・家事（`kaji`）に替え、ペット（`pet`）と防災（`bousai`）をやめました。
これらと、以前の対象国（韓国・ドイツ・台湾など）の過去ページは `docs/` に残っていますが、新しくは生成されません。

手動生成: `python scripts/generate_dashboard.py --genre beauty --input stories.json [--date YYYY-MM-DD]`

## ローカルでの毎日の更新（`scripts/run_daily.sh`）

クラウドのルーティンから集めるとアクセス制限でニュースの質が落ちるので、運営者の Mac から毎日 15:30 に集める（2026-10-07〜）。
OSSU はトレンドを 17:00 と 21:00 に、logik2.com の X の下書きは翌朝 06:00 にここから読む。

| 段階 | やること | 担当 |
|---|---|---|
| 1. 集める | Google News の RSS（ジャンル × 国。過去 1 日）、Google トレンドの RSS（jp / us / gb）、百度热搜（cn）の候補 | `scripts/news_pipeline.py collect` |
| 2. 選ぶ | 候補の見出しから国ごとに 4〜6 本選ぶ | `claude -p`（`prompts/genre.md`・`prompts/trends.md`） |
| 3. 確かめる | Google News のリンクを元記事の URL に直し、記事ページの説明文を取る | `news_pipeline.py resolve`（Claude が実行） |
| 4. 書く | 日本語の見出し（40 文字以内）と要約（2〜3 文）。説明文に書いてあることだけで、自分の言葉で | `claude -p` |
| 5. 検査 | 形・文字数・番号を確かめ、出典と URL を埋めて `--input` の JSON にする | `news_pipeline.py build`（Claude が実行） |
| 6. 公開 | `generate_dashboard.py` でページを作り、ブランチに push → PR → マージ | `run_daily.sh` |

- **Claude に許すのは、`work/` の中の読み書きと、`news_pipeline.py` の `resolve` / `build` だけ**（`--permission-mode dontAsk`、ユーザー設定と MCP は読まない）。
  ネットワークに出るのは決まったスクリプトだけなので、記事ページの文に指示が紛れていても、ほかのコマンドや送信には届かない
- 記事の URL は Claude に書かせない（`ref` で選んだ記事を指し、`build` が埋める）。存在しない URL が載らない
- ジャンルは 1 つずつ別の `claude -p` で書く（1 つ 25 分で打ち切り）。失敗したジャンルは前日のまま、ほかは公開する。失敗すると Mac の通知に出る
- 作業ファイルは `work/{日付}/`（2 週間で消す）、ログは `logs/{日付}.log`。どちらも git に入れない

### 始め方

自動実行専用の clone を作り、そこで登録する（手で作業する clone とは分ける。実行のたびに `origin/main` から始め直すため）。

```sh
git clone git@github.com:Keistar/dashboard_news.git ~/dev/dashboard_news-runner
cd ~/dev/dashboard_news-runner
scripts/run_daily.sh                 # 一度手で流して、通ることを確かめる（NEWS_NO_PUBLISH=1 なら公開しない）
scripts/install_launchd.sh           # 毎日 15:30 に登録
launchctl kickstart gui/$(id -u)/com.keistar.dashboard-news   # 今すぐ 1 回
scripts/install_launchd.sh --uninstall                         # 外す
```

- Mac がスリープ中に 15:30 を過ぎたら、起きたときに 1 回動く。電源が切れていた日は動かない（翌日の分で追いつく。X の下書きは 2 日前までのニュースを使う）
- `claude` は、この Mac でログインしている Claude Code の認証をそのまま使う。`git push` は SSH の鍵、PR は `gh` の認証を使う

## トレンド枠（`trends`）

各国の「いま検索・話題になっているワード」を起点に、関連記事を集める枠です。定点ニュースのジャンルとは別に、手動で実行します。

1. **候補を集める**
   - 日本：「Google トレンド 急上昇」「Yahoo!リアルタイム検索」「楽天ランキング 急上昇」
   - 米国：「Trending searches today US」
   - 英国：「UK trending today」
   - 中国：「Weibo hot search」「Douyin viral product」（Google トレンドの対象外）
   - 通信できる環境なら、`python scripts/fetch_trends_rss.py --geo JP,US,GB` で Google トレンド RSS から候補ワードを取れます。
2. **選別する**
   - 各ワードに `commercial_score`（商品に結びつくほど高い、1〜100）と `product_keywords`（1〜3語）を付けます。
   - あわせて `product_keywords_ja`（0〜3語）を付けます。**日本の楽天市場で同じ商品を探すときの日本語の検索語**です
     （例: 英国の `heated airer` → `["電気 物干し", "衣類乾燥 物干しスタンド"]`）。日本のトレンドでは `product_keywords` と同じでかまいません。
     日本で該当する商品が思い当たらなければ空の配列にします。OSSU（ossu.logik2.com）が「海外で先に話題」の判定に使います。
   - `trend_source`（どこで話題を観測したか）に加えて、その種類を `trend_source_type` に入れます。
     `search`（Google トレンド・Yahoo!リアルタイム検索などの検索）／`sns`（X・TikTok・Weibo・Douyin など）／
     `news`（報道・メディア）／`ec_ranking`（楽天・Amazon などの EC の売れ筋ランキング）／`other` のどれか。
     **EC のランキングから拾ったものは必ず `ec_ranking`** にします。OSSU は楽天ランキングを自前で記録しているため、
     `ec_ranking` のトレンドを「ランキングより先に話題になったもの」とは扱いません。
   - 訃報・事件・事故・災害の被害・政治は `brand_safe: false` にします。スクリプトが自動で除外します。
3. **関連記事を付ける**：ワードごとに1〜3本の記事を集めて、日本語で要約します。
4. **生成する**：`python scripts/generate_dashboard.py --genre trends --input trends.json [--date YYYY-MM-DD]`

入力の形式（国コードごとの配列）:
```json
{"jp": [{"keyword": "ポータブル電源", "reason_ja": "キャンプ需要で検索急増", "genre": "outdoor",
         "commercial_score": 90, "brand_safe": true,
         "product_keywords": ["ポータブル電源", "ソーラーパネル"],
         "product_keywords_ja": ["ポータブル電源", "ソーラーパネル"], "trend_source": "Google Trends JP",
         "trend_source_type": "search",
         "articles": [{"title_ja": "...", "summary_ja": "...", "source": "...", "url": "..."}]}]}
```
`genre` には既存ジャンルのコードか `other` を入れます。1か国あたりスコアの高い順に最大10件、記事は3本まで、キーワードは3語までに切り詰めます。

出力されるファイル:
- `docs/trends/{country}/{date}.html` と `.json`
- `docs/trends/latest.json`（全国分を `commercial_score` 順に並べたもの）

## JSON出力

HTMLと同時に、他サイトから使えるJSONも書き出します。

- `docs/{genre}/{country}/{date}.json` — 国・日付ごとの記事
- `docs/{genre}/latest.json` — そのジャンルの最新回（全国分をスコア順）
- `docs/index.json` — 全ジャンルとトレンド枠の一覧（`trends` と `genres`。ジャンル名、国、JSONがある日付、最新ファイルのパス）
- `docs/latest.json` — 最新のトレンド（`trends`）と全ジャンルの最新記事（`stories`）をまとめたもの。記事には `genre` / `genre_label_ja` / `date` が付きます
- ジャンル記事にも、入力にあれば `commercial_score` と `product_keywords` が出力されます（任意）

GitHub Pages経由で `https://<ユーザー名>.github.io/<リポジトリ名>/ai/latest.json` のように取得できます。

```json
{
  "genre": "ai", "genre_label_ja": "AI", "date": "2026-09-28",
  "generated_at": "2026-09-28T23:25:36+09:00",
  "stories": [
    {"title_ja": "...", "summary_ja": "...", "source": "...", "url": "...", "score": 95,
     "country": "us", "country_label_ja": "アメリカ"}
  ]
}
```

国・日付ごとのファイルは `country` / `country_label_ja` がトップレベルに入り、各記事には含まれません。

## カスタマイズ

- ニュース件数や検索回数の上限は `generate_dashboard.py` 冒頭の
  `MAX_STORIES` / `max_uses` を変更
- ニュースの対象トピック（例: ハードウェア寄りに絞る等）は
  `SYSTEM_PROMPT` の文章を編集
- 配色やレイアウトは同ファイル内の `PAGE_TEMPLATE` のCSSを編集

## 注意

- GitHub Actionsのscheduleは混雑時に最大30〜60分程度ずれることがあります
- 1回の実行コストはおおよそ$0.1〜0.2程度の見込み（Sonnet 4.6・検索最大6回の場合）
- ニュース取得やJSON解析に失敗した場合はその日の更新をスキップし、
  前回のダッシュボードがそのまま残ります（壊れた状態で公開されません）
