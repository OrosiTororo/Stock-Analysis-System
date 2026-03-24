# 📈 Stock-Analysis-System

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
- **APIキー保護**: `.env` ファイルは `.gitignore` で除外済み
- **環境変数管理**: GitHub Secrets または `.env` でキーを管理
- **サイズ制限**: ダウンロードサイズの上限設定でメモリ攻撃を防止

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
