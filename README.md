# 📈 Stock-Analysis-System

> **🌐 Language / 言語 / 语言**
>
> [🇯🇵 日本語](#-日本語) | [🇬🇧 English](#-english) | [🇨🇳 简体中文](#-简体中文)

---

# 🇯🇵 日本語

このシステムは、**企業の決算情報や開示ニュースを24時間自動で収集し、AIで分析してSlack・メールに通知し、Googleスプレッドシートに記録する自動監視システム**です。

PDFの決算短信もOCR（文字認識）で読み取り、機関投資家のような視点で「強気」「弱気」を自動判定します。

## 🔑 主な特徴

- **マルチLLM対応**: OpenAI / Ollama（ローカル） / Anthropic Claude / Google Gemini を切り替え可能
- **プライバシー重視**: Ollamaを使えば、データを一切外部に送信せずローカルで完結
- **マルチ通知**: Slack、メールに同時送信可能
- **OCR対応**: PDFの画像化された決算資料も自動読み取り
- **自動スケジュール**: GitHub Actionsで30分ごとに自動実行

---

### 📂 フォルダ階層図

```text
stock-analysis-system/          ← (システム本体)
│
├── .github/                    ← GitHub専用の設定フォルダ
│   └── workflows/              ← 自動化の「ワークフロー」を入れる場所
│       └── daily_check.yml     ← ★「30分に1回動け」という指令書 (OCR等のインストール指示含む)
│
├── main.py                     ← ★プログラムの本体 (ロジックの中枢)
│
├── config.json                 ← 設定ファイル (LLMプロバイダー・キーワード・通知先等)
│
├── sources.json                ← 収集するRSSのURL一覧 (サンプル)
│
├── watch_list.example.txt      ← 監視銘柄リストのテンプレート
│
├── .env.example                ← 環境変数のテンプレート
│
└── requirements.txt            ← 必要なPythonライブラリの一覧
```

---

### 各ファイルの詳しい役割

#### 1. `main.py`
**システムの本体**です。

*   **何をしているか？**
    *   RSSからニュースを集める（Yanoshinとそれ以外で挙動を切り替え）。
    *   `watch_list.txt` に基づき、不要な銘柄へのアクセスをカットする。
    *   PDFや画像をOCR処理してテキスト化する。
    *   「上方修正」などのキーワードで選別し、選択したLLMに分析させる。
    *   Slack・メールに通知し、Google Sheetsに保存する。

#### 2. `config.json`
**すべての設定を管理する中心ファイル**です。

*   **`llm_provider`**: 使用するLLMプロバイダー（`"openai"` / `"ollama"` / `"anthropic"` / `"google"`）
*   **`openai_model`**: OpenAI使用時のモデル名
*   **`ollama_base_url`** / **`ollama_model`**: Ollama接続先とモデル名
*   **`anthropic_model`**: Claude使用時のモデル名
*   **`google_model`**: Gemini使用時のモデル名
*   **`notification_channels`**: 通知先の配列（`["slack"]`, `["email"]`, `["slack", "email"]`）
*   **`email_to`**: メール送信先アドレスの配列
*   **`spreadsheet_name`**: Google Sheetsのスプレッドシート名（デフォルト: `"stock_analysis_log"`）
*   **`request_timeout_sec`**: HTTPリクエストのタイムアウト秒数（デフォルト: `60`）
*   **`max_content_size_mb`**: ダウンロードの最大サイズ（MB）（デフォルト: `50`）
*   **`rss_check_days`**: 通常モードで新着チェックする日数（デフォルト: `3`）
*   **`history_check_years`**: 過去データ収集モード時の対象年数（デフォルト: `5`）
*   **`sleep_between_items_sec`**: 記事間のスリープ秒数（デフォルト: `2`）
*   **`allowed_domains`**: アクセスを許可するドメインのホワイトリスト

#### 3. `watch_list.txt`
**監視したい企業の銘柄コード（4桁）を指定するリスト**です。

*   ここにコード（例: `1301`）を書くと、指定した銘柄のYanoshin RSSだけをチェックします。
*   **空の場合:** 全てのRSSをチェックします。

#### 4. `sources.json`
データの元となるRSSのURLリストです。

#### 5. `requirements.txt`
Pythonライブラリのリストです。

#### 6. `.github/workflows/daily_check.yml`
24時間自動稼働のための**指令書**です。

---

## ⚙️ システムの挙動ロジック

本システムは、サイトの種類によって収集モードを自動で切り替えます。

### 1. Yanoshin（適時開示 RSS）の場合
*   **監視リスト機能**: `watch_list.txt` に記載がない銘柄はアクセスせずにスキップします（高速化）。
*   **新着＆過去収集モード**:
    *   通常は「直近3日間」の新着のみを監視します。
    *   **もし新着で重要キーワード（決算など）が見つかった場合**、自動的に「過去データ収集モード」に切り替わり、そのRSSに含まれる**過去5年分のデータ**もまとめて収集・分析します。

### 2. その他のサイト（PR TIMES, JPXなど）の場合
*   常に「直近3日間」の新着記事のみを収集します。

---

## 🚀 導入手順 (セットアップ)

### ステップ1：LLMプロバイダーの選択

`config.json` の `"llm_provider"` を変更して、使用するAIを選択します。

| プロバイダー | `llm_provider` の値 | 必要な環境変数 | データ送信先 | コスト |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI** (GPT) | `"openai"` | `OPENAI_API_KEY` | OpenAIクラウド | 従量課金 |
| **Ollama** (ローカル) | `"ollama"` | なし | **なし（完全ローカル）** | 無料 |
| **Anthropic** (Claude) | `"anthropic"` | `ANTHROPIC_API_KEY` | Anthropicクラウド | 従量課金 |
| **Google** (Gemini) | `"google"` | `GOOGLE_AI_API_KEY` | Googleクラウド | 無料枠あり |

### ステップ2：通知チャンネルの設定

`config.json` の `"notification_channels"` で通知先を指定します。

```json
"notification_channels": ["slack", "email"]
```

| 通知先 | 必要な設定 |
| :--- | :--- |
| **Slack** | `SLACK_WEBHOOK_URL` 環境変数 |
| **メール** | `EMAIL_SMTP_USER`, `EMAIL_SMTP_PASSWORD` 環境変数 + `config.json` の `email_to` |

### ステップ3：環境変数の設定

#### GitHub Actions の場合
GitHubのリポジトリ設定（Settings → Secrets and variables → Actions）に登録。

#### ローカル実行の場合
`.env.example` を `.env` にコピーして編集：
```bash
cp .env.example .env
# .env を編集して必要な値を入力
```

### ステップ4：監視リストの作成
`watch_list.example.txt` を `watch_list.txt` にコピーし、監視したい銘柄コードを改行区切りで入力します。

```text
1301
6758
9984
```

---

## 🔒 Ollamaセットアップガイド（ローカルLLM / プライバシー重視）

Ollamaを使うと、**決算データを一切クラウドに送信せず**、自分のPCだけで分析できます。機密性の高い情報を扱う場合に最適です。

### 1. Ollamaのインストール

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# https://ollama.com/download からインストーラーをダウンロード
```

### 2. モデルのダウンロード

```bash
# Qwen3（推奨 - 日本語対応の高性能モデル）
ollama pull qwen3:8b

# その他の選択肢
ollama pull qwen3:4b          # 軽量版（メモリ4GB以上）
ollama pull qwen3:14b         # 高精度版（メモリ16GB以上）
ollama pull gemma3:12b        # Google Gemma 3
ollama pull llama3.1:8b       # Meta Llama 3.1
ollama pull deepseek-r1:8b    # DeepSeek R1
```

### 3. config.json の設定

```json
{
    "llm_provider": "ollama",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b"
}
```

### 4. 実行

```bash
# Ollamaが起動していることを確認
ollama serve &

# システム実行
python main.py --provider ollama
```

### 推奨スペック

| モデル | 必要メモリ(RAM) | GPU推奨 |
| :--- | :--- | :--- |
| qwen3:4b | 4GB以上 | なしでもOK |
| qwen3:8b | 8GB以上 | あると高速 |
| qwen3:14b | 16GB以上 | 推奨 |

---

## ☁️ クラウドLLMセットアップガイド

### OpenAI (GPT)

1. [OpenAI Platform](https://platform.openai.com/) にアクセス・ログイン
2. 「Settings」→「Billing」でクレジットカードを登録
3. 「API Keys」から「Create new secret key」で発行（`sk-...`）

```json
{
    "llm_provider": "openai",
    "openai_model": "gpt-4o"
}
```

### Anthropic (Claude)

1. [Anthropic Console](https://console.anthropic.com/) にアクセス・ログイン
2. 「Settings」→「API Keys」からキーを発行（`sk-ant-...`）

```json
{
    "llm_provider": "anthropic",
    "anthropic_model": "claude-sonnet-4-20250514"
}
```

### Google (Gemini)

1. [Google AI Studio](https://aistudio.google.com/) にアクセス
2. 「Get API Key」からキーを発行（`AIza...`）
3. 無料枠が利用可能

```json
{
    "llm_provider": "google",
    "google_model": "gemini-2.0-flash"
}
```

---

## 📧 メール通知セットアップ

### 1. config.json の設定

```json
{
    "notification_channels": ["slack", "email"],
    "email_smtp_server": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_use_tls": true,
    "email_to": ["your-email@example.com", "team@example.com"]
}
```

### 2. Gmailの場合（アプリパスワード）

1. [Googleアカウント](https://myaccount.google.com/) → セキュリティ → 2段階認証を有効化
2. 「アプリパスワード」を作成
3. 生成された16文字のパスワードを `EMAIL_SMTP_PASSWORD` に設定

### 3. 環境変数

```bash
EMAIL_SMTP_USER=your-email@gmail.com
EMAIL_SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

---

## 🖥️ コマンドライン オプション

```bash
# 基本実行（config.json の設定で動作）
python main.py

# プロバイダーをCLIから指定（config.jsonより優先）
python main.py --provider ollama
python main.py --provider openai
python main.py --provider anthropic
python main.py --provider google

# テスト実行（通知・記録なし）
python main.py --dry-run --verbose

# 設定ファイルを指定
python main.py --config my_config.json

# 組み合わせ例
python main.py --provider ollama --dry-run --verbose
```

---

## 🔒 プライバシーとセキュリティ

### データフロー比較

| 項目 | Ollama（ローカル） | クラウドLLM |
| :--- | :--- | :--- |
| 決算データの送信先 | なし（PC内で完結） | 各社APIサーバー |
| APIキーの管理 | 不要 | 環境変数で安全に管理 |
| インターネット接続 | RSS取得のみ必要 | RSS取得 + LLM API |
| 処理速度 | PC性能に依存 | 高速（サーバー処理） |

### セキュリティ対策

- **SSRF防止**: ホワイトリスト方式でアクセス先ドメインを制限
- **リダイレクト検証**: リダイレクト先URLもホワイトリストで検証し、SSRF迂回を防止
- **APIキー保護**: `.env` ファイルは `.gitignore` で除外済み
- **環境変数管理**: GitHub Secrets または `.env` でキーを管理
- **サイズ制限**: ストリーミング読み込みで実際のダウンロードサイズを制限し、メモリ攻撃を防止
- **ページ数制限**: PDF解析は最大100ページ、OCRは最大5ページに制限

---

## 🔧 カスタマイズ方法

### 1. 検索ワード・フィルタの調整
`config.json` の `target_keywords`, `positive_words`, `negative_words` を編集。

### 2. 監視対象の変更（企業・サイト）
- `watch_list.txt`: 監視する銘柄コード（4桁）
- `sources.json`: 情報収集先のRSS URL

### 3. AIへの指示（プロンプト）の調整
`main.py` の `ANALYSIS_PROMPT` 変数を編集します。

### 4. 通知のデザイン・宛先
- Slack: `main.py` の `notify_slack` 関数
- メール: `main.py` の `notify_email` 関数

### 5. 実行頻度の変更
`.github/workflows/daily_check.yml` の `cron` 設定を変更。

---

## ⚠️ 注意事項

1.  **システム依存ツール**:
    ローカル環境では `tesseract-ocr` と `poppler-utils` のインストールが必要です。

2.  **GitHub Actionsの仕様**:
    *   **開始遅延**: 30分ごとの設定でも、サーバー混雑時は数分遅れることがあります。
    *   **60日ルール**: リポジトリ更新が60日間ないと自動停止します。
    *   **Ollamaはローカル専用**: GitHub ActionsではOllamaは使用できません（クラウドLLMを設定してください）。

3.  **APIコスト**:
    クラウドLLM（OpenAI / Anthropic / Google）は従量課金です。Ollamaは完全無料です。

---

### 【マニュアル】システムの停止・再開

*   **停止**: GitHubの「Actions」タブ → 「Stock Analysis 365」 → 右上の「...」 → 「Disable workflow」
*   **再開**: 同画面で「Enable workflow」をクリック

---

### 【マニュアル】SLACK_WEBHOOK_URL とは？

`SLACK_WEBHOOK_URL` は、システムからSlackへメッセージを届けるための「**専用の郵便ポストの住所**」です。

#### 取得方法
1.  [Slack API: Your Apps](https://api.slack.com/apps) にアクセス
2.  「Create New App」→「From scratch」を選択
3.  左側メニュー「Incoming Webhooks」→ スイッチを「On」
4.  「Add New Webhook to Workspace」→ チャンネルを選択
5.  「Webhook URL」をコピー

---

### Google Cloud設定マニュアル

#### 1. Google Cloudへアクセス
`https://console.cloud.google.com/` にアクセス・ログイン。

#### 2. プロジェクトの作成
「プロジェクトの選択」→「新しいプロジェクト」→ 名前に「Stock-Analysis」→ 作成。

#### 3. APIの有効化
検索バーで「Google Sheets API」→「有効にする」。

#### 4. サービスアカウントの作成
「IAMと管理」→「サービスアカウント」→「＋サービスアカウントを作成」→ 名前に「bot-user」。

#### 5. JSONキーの発行
作成したアカウント → 「キー」タブ →「鍵を追加」→「JSON」で作成。

#### 6. スプレッドシートの共有
JSONファイル内の `client_email` のアドレスをスプレッドシートに「編集者」として共有。

---

### ⚠️ 変更時の注意点

1.  **バックアップをとる** - コードをいじる前に動いている状態を保存
2.  **全角文字に気をつける** - PythonやJSONで全角スペースや全角引用符はエラーの原因
3.  **watch_list.txt は数字だけ** - 「1301 極洋」のように社名を書くと動きません

---

[⬆ Back to top / トップに戻る / 返回顶部](#-stock-analysis-system)

---
---

# 🇬🇧 English

This is an **automated monitoring system that collects corporate earnings reports and disclosure news 24/7, analyzes them with AI, sends notifications via Slack and email, and records results in Google Spreadsheets**.

It can also read image-based earnings summaries (Tanshin) from PDFs using OCR, and automatically determines a "bullish" or "bearish" outlook from an institutional investor's perspective.

## 🔑 Key Features

- **Multi-LLM Support**: Switch between OpenAI / Ollama (local) / Anthropic Claude / Google Gemini
- **Privacy-First**: With Ollama, all data stays on your machine — nothing is sent externally
- **Multi-Channel Notifications**: Send to Slack and email simultaneously
- **OCR Support**: Automatically reads image-based financial PDFs
- **Automated Scheduling**: Runs every 30 minutes via GitHub Actions

---

### 📂 Directory Structure

```text
stock-analysis-system/          ← (Project root)
│
├── .github/                    ← GitHub-specific configuration
│   └── workflows/              ← Automation workflow definitions
│       └── daily_check.yml     ← ★ "Run every 30 minutes" schedule (includes OCR installation)
│
├── main.py                     ← ★ Main application (core logic)
│
├── config.json                 ← Configuration file (LLM provider, keywords, notification targets, etc.)
│
├── sources.json                ← List of RSS feed URLs to collect from (sample)
│
├── watch_list.example.txt      ← Template for the stock watchlist
│
├── .env.example                ← Environment variable template
│
└── requirements.txt            ← Required Python libraries
```

---

### Detailed File Descriptions

#### 1. `main.py`
**The main application.**

*   **What it does:**
    *   Collects news from RSS feeds (with different behavior for Yanoshin vs. other sources).
    *   Filters out unwanted stocks based on `watch_list.txt`.
    *   OCR-processes PDFs and images into text.
    *   Filters by keywords like "upward revision," then sends selected items to the chosen LLM for analysis.
    *   Sends notifications via Slack/email and saves results to Google Sheets.

#### 2. `config.json`
**The central configuration file.**

*   **`llm_provider`**: LLM provider to use (`"openai"` / `"ollama"` / `"anthropic"` / `"google"`)
*   **`openai_model`**: Model name when using OpenAI
*   **`ollama_base_url`** / **`ollama_model`**: Ollama connection URL and model name
*   **`anthropic_model`**: Model name when using Claude
*   **`google_model`**: Model name when using Gemini
*   **`notification_channels`**: Notification targets (`["slack"]`, `["email"]`, `["slack", "email"]`)
*   **`email_to`**: Array of email recipient addresses
*   **`spreadsheet_name`**: Google Sheets spreadsheet name (default: `"stock_analysis_log"`)
*   **`request_timeout_sec`**: HTTP request timeout in seconds (default: `60`)
*   **`max_content_size_mb`**: Maximum download size in MB (default: `50`)
*   **`rss_check_days`**: Number of days to check for new items in normal mode (default: `3`)
*   **`history_check_years`**: Number of years for historical data collection mode (default: `5`)
*   **`sleep_between_items_sec`**: Sleep duration between items in seconds (default: `2`)
*   **`allowed_domains`**: Whitelist of allowed access domains

#### 3. `watch_list.txt`
**A list of 4-digit stock codes for companies you want to monitor.**

*   Adding a code (e.g., `1301`) limits Yanoshin RSS checks to only those stocks.
*   **If empty:** All RSS feeds are checked.

#### 4. `sources.json`
List of RSS feed URLs used as data sources.

#### 5. `requirements.txt`
List of required Python libraries.

#### 6. `.github/workflows/daily_check.yml`
**The instruction file** for 24/7 automated operation.

---

## ⚙️ System Behavior Logic

The system automatically switches collection modes depending on the type of source site.

### 1. Yanoshin (Timely Disclosure RSS)
*   **Watchlist Filtering**: Stocks not listed in `watch_list.txt` are skipped without being accessed (for speed).
*   **New & Historical Collection Mode**:
    *   Normally monitors only new items from the **last 3 days**.
    *   **If important keywords (e.g., earnings) are found in new items**, the system automatically switches to "historical collection mode" and also collects and analyzes **up to 5 years of past data** from that RSS feed.

### 2. Other Sites (PR TIMES, JPX, etc.)
*   Always collects only new articles from the **last 3 days**.

---

## 🚀 Setup Guide

### Step 1: Choose an LLM Provider

Change `"llm_provider"` in `config.json` to select your AI.

| Provider | `llm_provider` Value | Required Env Variable | Data Destination | Cost |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI** (GPT) | `"openai"` | `OPENAI_API_KEY` | OpenAI Cloud | Pay-per-use |
| **Ollama** (Local) | `"ollama"` | None | **None (fully local)** | Free |
| **Anthropic** (Claude) | `"anthropic"` | `ANTHROPIC_API_KEY` | Anthropic Cloud | Pay-per-use |
| **Google** (Gemini) | `"google"` | `GOOGLE_AI_API_KEY` | Google Cloud | Free tier available |

### Step 2: Configure Notification Channels

Set notification targets in `"notification_channels"` in `config.json`.

```json
"notification_channels": ["slack", "email"]
```

| Channel | Required Setup |
| :--- | :--- |
| **Slack** | `SLACK_WEBHOOK_URL` environment variable |
| **Email** | `EMAIL_SMTP_USER`, `EMAIL_SMTP_PASSWORD` env variables + `email_to` in `config.json` |

### Step 3: Set Environment Variables

#### For GitHub Actions
Register in your repository settings (Settings → Secrets and variables → Actions).

#### For Local Execution
Copy `.env.example` to `.env` and edit:
```bash
cp .env.example .env
# Edit .env and fill in the required values
```

### Step 4: Create a Watchlist
Copy `watch_list.example.txt` to `watch_list.txt` and enter stock codes (one per line).

```text
1301
6758
9984
```

---

## 🔒 Ollama Setup Guide (Local LLM / Privacy-Focused)

With Ollama, you can analyze earnings data **without sending anything to the cloud** — everything runs on your own PC. Ideal for handling confidential information.

### 1. Install Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download the installer from https://ollama.com/download
```

### 2. Download a Model

```bash
# Qwen3 (recommended - high-performance model with Japanese support)
ollama pull qwen3:8b

# Other options
ollama pull qwen3:4b          # Lightweight (4GB+ RAM)
ollama pull qwen3:14b         # High-accuracy (16GB+ RAM)
ollama pull gemma3:12b        # Google Gemma 3
ollama pull llama3.1:8b       # Meta Llama 3.1
ollama pull deepseek-r1:8b    # DeepSeek R1
```

### 3. Configure config.json

```json
{
    "llm_provider": "ollama",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b"
}
```

### 4. Run

```bash
# Make sure Ollama is running
ollama serve &

# Run the system
python main.py --provider ollama
```

### Recommended Specs

| Model | Required RAM | GPU Recommended |
| :--- | :--- | :--- |
| qwen3:4b | 4GB+ | Not required |
| qwen3:8b | 8GB+ | Speeds things up |
| qwen3:14b | 16GB+ | Recommended |

---

## ☁️ Cloud LLM Setup Guide

### OpenAI (GPT)

1. Visit [OpenAI Platform](https://platform.openai.com/) and log in
2. Go to "Settings" → "Billing" and register a credit card
3. Go to "API Keys" → "Create new secret key" (`sk-...`)

```json
{
    "llm_provider": "openai",
    "openai_model": "gpt-4o"
}
```

### Anthropic (Claude)

1. Visit [Anthropic Console](https://console.anthropic.com/) and log in
2. Go to "Settings" → "API Keys" and generate a key (`sk-ant-...`)

```json
{
    "llm_provider": "anthropic",
    "anthropic_model": "claude-sonnet-4-20250514"
}
```

### Google (Gemini)

1. Visit [Google AI Studio](https://aistudio.google.com/)
2. Click "Get API Key" to generate a key (`AIza...`)
3. Free tier is available

```json
{
    "llm_provider": "google",
    "google_model": "gemini-2.0-flash"
}
```

---

## 📧 Email Notification Setup

### 1. Configure config.json

```json
{
    "notification_channels": ["slack", "email"],
    "email_smtp_server": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_use_tls": true,
    "email_to": ["your-email@example.com", "team@example.com"]
}
```

### 2. For Gmail (App Password)

1. Go to [Google Account](https://myaccount.google.com/) → Security → Enable 2-Step Verification
2. Create an "App Password"
3. Set the generated 16-character password as `EMAIL_SMTP_PASSWORD`

### 3. Environment Variables

```bash
EMAIL_SMTP_USER=your-email@gmail.com
EMAIL_SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

---

## 🖥️ Command-Line Options

```bash
# Basic run (uses config.json settings)
python main.py

# Specify provider via CLI (overrides config.json)
python main.py --provider ollama
python main.py --provider openai
python main.py --provider anthropic
python main.py --provider google

# Dry run (no notifications or recording)
python main.py --dry-run --verbose

# Specify a config file
python main.py --config my_config.json

# Combined example
python main.py --provider ollama --dry-run --verbose
```

---

## 🔒 Privacy & Security

### Data Flow Comparison

| Item | Ollama (Local) | Cloud LLM |
| :--- | :--- | :--- |
| Earnings data destination | None (stays on your PC) | Each provider's API servers |
| API key management | Not required | Managed securely via env variables |
| Internet connection | Only needed for RSS fetching | RSS fetching + LLM API |
| Processing speed | Depends on PC specs | Fast (server-side processing) |

### Security Measures

- **SSRF Prevention**: Whitelist-based access domain restrictions
- **Redirect Verification**: Redirect destination URLs are also validated against the whitelist to prevent SSRF bypass
- **API Key Protection**: `.env` file is excluded via `.gitignore`
- **Env Variable Management**: Keys managed via GitHub Secrets or `.env`
- **Size Limits**: Streaming downloads with actual size enforcement to prevent memory attacks
- **Page Limits**: PDF parsing is limited to 100 pages; OCR is limited to 5 pages

---

## 🔧 Customization

### 1. Adjust Search Keywords & Filters
Edit `target_keywords`, `positive_words`, `negative_words` in `config.json`.

### 2. Change Monitored Companies & Sources
- `watch_list.txt`: Stock codes (4 digits) to monitor
- `sources.json`: RSS feed URLs for data collection

### 3. Adjust AI Instructions (Prompt)
Edit the `ANALYSIS_PROMPT` variable in `main.py`.

### 4. Notification Design & Recipients
- Slack: `notify_slack` function in `main.py`
- Email: `notify_email` function in `main.py`

### 5. Change Execution Frequency
Modify the `cron` setting in `.github/workflows/daily_check.yml`.

---

## ⚠️ Notes

1.  **System Dependencies**:
    `tesseract-ocr` and `poppler-utils` must be installed for local execution.

2.  **GitHub Actions Behavior**:
    *   **Start Delay**: Even with a 30-minute schedule, execution may be delayed by a few minutes during server congestion.
    *   **60-Day Rule**: Workflows are automatically disabled if the repository has no updates for 60 days.
    *   **Ollama is Local Only**: Ollama cannot be used with GitHub Actions (configure a cloud LLM instead).

3.  **API Costs**:
    Cloud LLMs (OpenAI / Anthropic / Google) are pay-per-use. Ollama is completely free.

---

### [Manual] Stopping & Resuming the System

*   **Stop**: GitHub "Actions" tab → "Stock Analysis 365" → "..." (top-right) → "Disable workflow"
*   **Resume**: Click "Enable workflow" on the same page

---

### [Manual] What is SLACK_WEBHOOK_URL?

`SLACK_WEBHOOK_URL` is like a **dedicated mailbox address** that lets the system deliver messages to Slack.

#### How to Get It
1.  Visit [Slack API: Your Apps](https://api.slack.com/apps)
2.  Click "Create New App" → "From scratch"
3.  In the left menu, go to "Incoming Webhooks" → Toggle the switch to "On"
4.  Click "Add New Webhook to Workspace" → Select a channel
5.  Copy the "Webhook URL"

---

### Google Cloud Setup Manual

#### 1. Access Google Cloud
Go to `https://console.cloud.google.com/` and log in.

#### 2. Create a Project
"Select a project" → "New Project" → Name it "Stock-Analysis" → Create.

#### 3. Enable the API
Search for "Google Sheets API" → Click "Enable."

#### 4. Create a Service Account
"IAM & Admin" → "Service Accounts" → "+ Create Service Account" → Name it "bot-user."

#### 5. Generate a JSON Key
Click on the created account → "Keys" tab → "Add Key" → "JSON" to create.

#### 6. Share the Spreadsheet
Share the spreadsheet with the `client_email` address from the JSON file as an "Editor."

---

### ⚠️ Important Notes When Making Changes

1.  **Back up first** — Save the working state before modifying any code
2.  **Watch out for full-width characters** — Full-width spaces or quotes in Python/JSON will cause errors
3.  **watch_list.txt: numbers only** — Writing "1301 Kyokuyo" (with the company name) will break it

---

[⬆ Back to top / トップに戻る / 返回顶部](#-stock-analysis-system)

---
---

# 🇨🇳 简体中文

本系统是一个**全天候自动监控系统，可自动收集企业财报信息和公告新闻，通过AI进行分析，并将结果通过Slack和邮件发送通知，同时记录到Google电子表格中**。

系统还能通过OCR（光学字符识别）读取PDF格式的财报摘要，并从机构投资者的视角自动判定"看涨"或"看跌"。

## 🔑 主要特点

- **多LLM支持**：可在 OpenAI / Ollama（本地） / Anthropic Claude / Google Gemini 之间切换
- **注重隐私**：使用Ollama时，数据完全在本地处理，不会发送到外部
- **多渠道通知**：可同时发送到Slack和邮件
- **OCR支持**：自动识别图片化的PDF财务资料
- **自动调度**：通过GitHub Actions每30分钟自动执行一次

---

### 📂 目录结构

```text
stock-analysis-system/          ← （项目根目录）
│
├── .github/                    ← GitHub专用配置文件夹
│   └── workflows/              ← 自动化工作流定义
│       └── daily_check.yml     ← ★ "每30分钟运行一次"的调度文件（包含OCR等安装指令）
│
├── main.py                     ← ★ 主程序（核心逻辑）
│
├── config.json                 ← 配置文件（LLM提供商、关键词、通知目标等）
│
├── sources.json                ← 待采集的RSS源URL列表（示例）
│
├── watch_list.example.txt      ← 监控股票列表模板
│
├── .env.example                ← 环境变量模板
│
└── requirements.txt            ← 所需Python库列表
```

---

### 各文件详细说明

#### 1. `main.py`
**系统主程序。**

*   **功能说明：**
    *   从RSS源收集新闻（Yanoshin和其他来源采用不同的处理逻辑）。
    *   根据 `watch_list.txt` 过滤掉不需要关注的股票。
    *   对PDF和图片进行OCR处理，转化为文本。
    *   通过"上调预期"等关键词进行筛选，然后发送给选定的LLM进行分析。
    *   通过Slack和邮件发送通知，并保存到Google Sheets。

#### 2. `config.json`
**核心配置文件。**

*   **`llm_provider`**：使用的LLM提供商（`"openai"` / `"ollama"` / `"anthropic"` / `"google"`）
*   **`openai_model`**：使用OpenAI时的模型名称
*   **`ollama_base_url`** / **`ollama_model`**：Ollama连接地址和模型名称
*   **`anthropic_model`**：使用Claude时的模型名称
*   **`google_model`**：使用Gemini时的模型名称
*   **`notification_channels`**：通知渠道数组（`["slack"]`、`["email"]`、`["slack", "email"]`）
*   **`email_to`**：邮件接收地址数组
*   **`spreadsheet_name`**：Google Sheets电子表格名称（默认：`"stock_analysis_log"`）
*   **`request_timeout_sec`**：HTTP请求超时秒数（默认：`60`）
*   **`max_content_size_mb`**：最大下载大小（MB）（默认：`50`）
*   **`rss_check_days`**：普通模式下检查新数据的天数（默认：`3`）
*   **`history_check_years`**：历史数据采集模式的目标年数（默认：`5`）
*   **`sleep_between_items_sec`**：文章间的休眠秒数（默认：`2`）
*   **`allowed_domains`**：允许访问的域名白名单

#### 3. `watch_list.txt`
**指定要监控的企业股票代码（4位数字）列表。**

*   添加代码（如 `1301`）后，只会检查该股票的Yanoshin RSS。
*   **为空时：** 检查所有RSS源。

#### 4. `sources.json`
数据来源的RSS URL列表。

#### 5. `requirements.txt`
所需Python库列表。

#### 6. `.github/workflows/daily_check.yml`
实现7×24小时自动运行的**调度文件**。

---

## ⚙️ 系统行为逻辑

系统会根据数据源网站的类型自动切换采集模式。

### 1. Yanoshin（适时披露RSS）
*   **监控列表过滤**：未在 `watch_list.txt` 中列出的股票将被跳过，不会访问（提高速度）。
*   **新数据和历史数据采集模式**：
    *   通常仅监控**最近3天**的新数据。
    *   **如果在新数据中发现重要关键词（如财报等）**，系统会自动切换到"历史数据采集模式"，同时采集和分析该RSS源中**过去5年的数据**。

### 2. 其他网站（PR TIMES、JPX等）
*   始终仅采集**最近3天**的新文章。

---

## 🚀 安装指南

### 步骤1：选择LLM提供商

修改 `config.json` 中的 `"llm_provider"` 来选择要使用的AI。

| 提供商 | `llm_provider` 值 | 所需环境变量 | 数据发送目标 | 费用 |
| :--- | :--- | :--- | :--- | :--- |
| **OpenAI** (GPT) | `"openai"` | `OPENAI_API_KEY` | OpenAI云 | 按量计费 |
| **Ollama**（本地） | `"ollama"` | 无 | **无（完全本地）** | 免费 |
| **Anthropic** (Claude) | `"anthropic"` | `ANTHROPIC_API_KEY` | Anthropic云 | 按量计费 |
| **Google** (Gemini) | `"google"` | `GOOGLE_AI_API_KEY` | Google云 | 有免费额度 |

### 步骤2：配置通知渠道

在 `config.json` 的 `"notification_channels"` 中指定通知目标。

```json
"notification_channels": ["slack", "email"]
```

| 通知渠道 | 所需配置 |
| :--- | :--- |
| **Slack** | `SLACK_WEBHOOK_URL` 环境变量 |
| **邮件** | `EMAIL_SMTP_USER`、`EMAIL_SMTP_PASSWORD` 环境变量 + `config.json` 中的 `email_to` |

### 步骤3：设置环境变量

#### 使用GitHub Actions时
在仓库设置中注册（Settings → Secrets and variables → Actions）。

#### 本地运行时
将 `.env.example` 复制为 `.env` 并编辑：
```bash
cp .env.example .env
# 编辑 .env 并填入所需值
```

### 步骤4：创建监控列表
将 `watch_list.example.txt` 复制为 `watch_list.txt`，每行输入一个股票代码。

```text
1301
6758
9984
```

---

## 🔒 Ollama安装指南（本地LLM / 注重隐私）

使用Ollama可以**完全不向云端发送财报数据**，所有分析都在您自己的电脑上完成。非常适合处理机密信息。

### 1. 安装Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# 从 https://ollama.com/download 下载安装程序
```

### 2. 下载模型

```bash
# Qwen3（推荐 - 支持日语的高性能模型）
ollama pull qwen3:8b

# 其他选择
ollama pull qwen3:4b          # 轻量版（需4GB以上内存）
ollama pull qwen3:14b         # 高精度版（需16GB以上内存）
ollama pull gemma3:12b        # Google Gemma 3
ollama pull llama3.1:8b       # Meta Llama 3.1
ollama pull deepseek-r1:8b    # DeepSeek R1
```

### 3. 配置 config.json

```json
{
    "llm_provider": "ollama",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b"
}
```

### 4. 运行

```bash
# 确认Ollama正在运行
ollama serve &

# 运行系统
python main.py --provider ollama
```

### 推荐配置

| 模型 | 所需内存(RAM) | 是否推荐GPU |
| :--- | :--- | :--- |
| qwen3:4b | 4GB以上 | 不需要 |
| qwen3:8b | 8GB以上 | 有则更快 |
| qwen3:14b | 16GB以上 | 推荐 |

---

## ☁️ 云端LLM安装指南

### OpenAI (GPT)

1. 访问 [OpenAI Platform](https://platform.openai.com/) 并登录
2. 进入"Settings"→"Billing"注册信用卡
3. 在"API Keys"中点击"Create new secret key"获取密钥（`sk-...`）

```json
{
    "llm_provider": "openai",
    "openai_model": "gpt-4o"
}
```

### Anthropic (Claude)

1. 访问 [Anthropic Console](https://console.anthropic.com/) 并登录
2. 进入"Settings"→"API Keys"获取密钥（`sk-ant-...`）

```json
{
    "llm_provider": "anthropic",
    "anthropic_model": "claude-sonnet-4-20250514"
}
```

### Google (Gemini)

1. 访问 [Google AI Studio](https://aistudio.google.com/)
2. 点击"Get API Key"获取密钥（`AIza...`）
3. 可使用免费额度

```json
{
    "llm_provider": "google",
    "google_model": "gemini-2.0-flash"
}
```

---

## 📧 邮件通知设置

### 1. 配置 config.json

```json
{
    "notification_channels": ["slack", "email"],
    "email_smtp_server": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_use_tls": true,
    "email_to": ["your-email@example.com", "team@example.com"]
}
```

### 2. Gmail（应用专用密码）

1. 访问 [Google账户](https://myaccount.google.com/) → 安全性 → 启用两步验证
2. 创建"应用专用密码"
3. 将生成的16位密码设置为 `EMAIL_SMTP_PASSWORD`

### 3. 环境变量

```bash
EMAIL_SMTP_USER=your-email@gmail.com
EMAIL_SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

---

## 🖥️ 命令行选项

```bash
# 基本运行（使用config.json的设置）
python main.py

# 通过CLI指定提供商（优先于config.json）
python main.py --provider ollama
python main.py --provider openai
python main.py --provider anthropic
python main.py --provider google

# 测试运行（不发送通知、不记录）
python main.py --dry-run --verbose

# 指定配置文件
python main.py --config my_config.json

# 组合示例
python main.py --provider ollama --dry-run --verbose
```

---

## 🔒 隐私与安全

### 数据流对比

| 项目 | Ollama（本地） | 云端LLM |
| :--- | :--- | :--- |
| 财报数据发送目标 | 无（在电脑内完成） | 各提供商的API服务器 |
| API密钥管理 | 不需要 | 通过环境变量安全管理 |
| 网络连接 | 仅需用于获取RSS | 获取RSS + LLM API |
| 处理速度 | 取决于电脑性能 | 快速（服务器端处理） |

### 安全措施

- **SSRF防护**：通过白名单方式限制可访问的域名
- **重定向验证**：重定向目标URL也会通过白名单验证，防止SSRF绕过
- **API密钥保护**：`.env` 文件已在 `.gitignore` 中排除
- **环境变量管理**：通过GitHub Secrets或 `.env` 管理密钥
- **大小限制**：通过流式下载限制实际下载大小，防止内存攻击
- **页数限制**：PDF解析最多100页，OCR最多5页

---

## 🔧 自定义方法

### 1. 调整搜索关键词和过滤器
编辑 `config.json` 中的 `target_keywords`、`positive_words`、`negative_words`。

### 2. 更改监控对象（企业/网站）
- `watch_list.txt`：要监控的股票代码（4位数字）
- `sources.json`：数据采集源的RSS URL

### 3. 调整AI指令（提示词）
编辑 `main.py` 中的 `ANALYSIS_PROMPT` 变量。

### 4. 通知样式和收件人
- Slack：`main.py` 中的 `notify_slack` 函数
- 邮件：`main.py` 中的 `notify_email` 函数

### 5. 更改执行频率
修改 `.github/workflows/daily_check.yml` 中的 `cron` 设置。

---

## ⚠️ 注意事项

1.  **系统依赖工具**：
    本地环境需要安装 `tesseract-ocr` 和 `poppler-utils`。

2.  **GitHub Actions特性**：
    *   **启动延迟**：即使设置为每30分钟执行，服务器繁忙时可能会延迟数分钟。
    *   **60天规则**：如果仓库60天内没有更新，工作流将自动停止。
    *   **Ollama仅限本地**：GitHub Actions中无法使用Ollama（请配置云端LLM）。

3.  **API费用**：
    云端LLM（OpenAI / Anthropic / Google）按量计费。Ollama完全免费。

---

### 【手册】系统停止与恢复

*   **停止**：GitHub的"Actions"标签页 → "Stock Analysis 365" → 右上角"..." → "Disable workflow"
*   **恢复**：在同一页面点击"Enable workflow"

---

### 【手册】什么是 SLACK_WEBHOOK_URL？

`SLACK_WEBHOOK_URL` 就像一个**专用邮箱地址**，让系统能够向Slack发送消息。

#### 获取方法
1.  访问 [Slack API: Your Apps](https://api.slack.com/apps)
2.  点击"Create New App"→"From scratch"
3.  左侧菜单"Incoming Webhooks"→ 将开关设为"On"
4.  点击"Add New Webhook to Workspace"→ 选择频道
5.  复制"Webhook URL"

---

### Google Cloud设置手册

#### 1. 访问Google Cloud
访问 `https://console.cloud.google.com/` 并登录。

#### 2. 创建项目
"选择项目"→"新建项目"→ 名称填写"Stock-Analysis"→ 创建。

#### 3. 启用API
在搜索栏中搜索"Google Sheets API"→ 点击"启用"。

#### 4. 创建服务账号
"IAM和管理"→"服务账号"→"+创建服务账号"→ 名称填写"bot-user"。

#### 5. 生成JSON密钥
点击已创建的账号 → "密钥"标签页 → "添加密钥"→ 选择"JSON"创建。

#### 6. 共享电子表格
将JSON文件中 `client_email` 的地址以"编辑者"身份共享到电子表格。

---

### ⚠️ 修改时的注意事项

1.  **先备份** — 修改代码前保存当前可运行的状态
2.  **注意全角字符** — Python和JSON中使用全角空格或全角引号会导致错误
3.  **watch_list.txt 只写数字** — 写成"1301 极洋"（附带公司名）会导致程序无法运行

---

[⬆ Back to top / トップに戻る / 返回顶部](#-stock-analysis-system)
