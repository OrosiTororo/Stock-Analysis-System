import os
import re
import json
import time
import ipaddress
import logging
import argparse
import smtplib
import requests
import pdfplumber
import feedparser
import gspread
import pytesseract
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pdf2image import convert_from_bytes
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime, timedelta
from time import mktime
from urllib.parse import urlparse
from google.oauth2.service_account import Credentials

# ==========================================
# ログ設定
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ==========================================
# 設定の読み込み
# ==========================================
DEFAULT_CONFIG = {
    "llm_provider": "openai",
    "openai_model": "gpt-4o",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    "anthropic_model": "claude-sonnet-4-20250514",
    "google_model": "gemini-2.0-flash",
    "spreadsheet_name": "stock_analysis_log",
    "request_timeout_sec": 60,
    "max_content_size_mb": 50,
    "rss_check_days": 3,
    "history_check_years": 5,
    "sleep_between_items_sec": 2,
    "notification_channels": ["slack"],
    "email_smtp_server": "smtp.gmail.com",
    "email_smtp_port": 587,
    "email_use_tls": True,
    "email_to": [],
    "target_keywords": ["決算", "修正", "配当", "短信", "報告書", "中期経営計画"],
    "positive_words": [
        "上方修正", "業績予想の修正", "営業利益率改善", "黒字転換", "赤字縮小",
        "想定を上回る", "過去最高", "V字回復", "自己株式取得", "取得枠の設定",
        "消却", "増配", "配当方針の変更", "DOE", "累進配当", "構造的", "恒常的",
        "収益体質の改善", "固定費削減", "高付加価値", "高収益案件", "価格転嫁",
    ],
    "negative_words": [
        "下方修正", "未定", "慎重", "厳しい", "減益", "一時的", "反動減",
        "特殊要因", "外部環境", "為替影響", "原材料高", "一過性要因", "先行投資",
        "想定外", "希薄化", "第三者割当", "MSワラント", "CB", "新株予約権", "支配株主",
    ],
    "allowed_domains": [
        "prtimes.jp",
        "www.jpx.co.jp",
        "webapi.yanoshin.jp",
        "www.release.tdnet.info",
        "tdnet.info",
    ],
}


def load_config(config_path):
    """config.json を読み込み、デフォルト値とマージして返す"""
    config = dict(DEFAULT_CONFIG)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        if not isinstance(user_config, dict):
            logging.warning("config.json の形式が不正です。デフォルト設定を使用します")
            return config
        config.update(user_config)
        logging.info("設定ファイルを読み込みました: %s", config_path)
    except FileNotFoundError:
        logging.info("config.json が見つかりません。デフォルト設定を使用します")
    except json.JSONDecodeError as e:
        logging.warning("config.json のパースに失敗しました: %s デフォルト設定を使用します", e)

    # 設定値のバリデーション
    valid_providers = {"openai", "ollama", "anthropic", "google"}
    if config.get("llm_provider") not in valid_providers:
        logging.warning("不正な llm_provider: %s。デフォルト 'openai' を使用します", config.get("llm_provider"))
        config["llm_provider"] = "openai"
    for key in ("request_timeout_sec", "max_content_size_mb", "rss_check_days",
                "history_check_years", "sleep_between_items_sec"):
        if not isinstance(config.get(key), (int, float)) or config[key] <= 0:
            logging.warning("不正な設定値 %s=%s。デフォルト値を使用します", key, config.get(key))
            config[key] = DEFAULT_CONFIG[key]

    return config


def build_allowed_domains(config, rss_sources):
    """config.json の allowed_domains と sources.json のドメインを統合する"""
    domains = set(config.get("allowed_domains", []))
    for url in rss_sources:
        if isinstance(url, str):
            try:
                hostname = urlparse(url).hostname
                if hostname:
                    domains.add(hostname)
            except (ValueError, AttributeError):
                pass
    return domains


# ==========================================
# HTTPリクエスト
# ==========================================
HEADERS = {
    "User-Agent": "StockAnalysisBot/1.0 (+https://github.com/TroroOrosi/Stock-Analysis-System)"
}


def _is_private_ip(hostname):
    """ホスト名がプライベートIPアドレスかどうかを判定する（SSRF対策）"""
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        return False


def is_allowed_url(url, allowed_domains):
    """URLがホワイトリストに含まれるドメインかチェック（SSRF対策）"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname or ""
        if _is_private_ip(hostname):
            logging.warning("プライベートIPアドレスへのアクセスをブロック: %s", hostname)
            return False
        return any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in allowed_domains
        )
    except (ValueError, AttributeError):
        return False


def fetch_with_retry(url, allowed_domains, retries=3, timeout=60, max_size=50 * 1024 * 1024):
    """リトライ機能付きのURL取得（ホワイトリストチェック付き）"""
    if not is_allowed_url(url, allowed_domains):
        logging.warning("許可されていないドメインへのアクセスをブロック: %s", urlparse(url).hostname)
        return None

    for i in range(retries):
        try:
            res = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, stream=True)

            # リダイレクト先がホワイトリスト外でないか検証（SSRF対策）
            if not is_allowed_url(res.url, allowed_domains):
                logging.warning("リダイレクト先が許可されていないドメインです: %s -> %s", url, res.url)
                res.close()
                return None

            content_length = res.headers.get("Content-Length")
            try:
                if content_length and int(content_length) > max_size:
                    logging.warning("レスポンスが大きすぎます (%s bytes): %s", content_length, url)
                    res.close()
                    return None
            except (ValueError, TypeError):
                logging.debug("Content-Lengthヘッダーが不正: %s", content_length)

            # ストリーミングで実際のサイズを制限しながら読み込み
            chunks = []
            downloaded = 0
            for chunk in res.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > max_size:
                    logging.warning("ダウンロードサイズが上限を超えました (%d bytes): %s", downloaded, url)
                    res.close()
                    return None
                chunks.append(chunk)

            # レスポンスオブジェクトの代わりに、必要な属性を持つラッパーを使用
            content = b"".join(chunks)
            res.raise_for_status()
            # ストリーミング完了後にcontentプロパティを利用可能にする
            res._content = content
            return res
        except requests.RequestException as e:
            logging.warning("接続エラー(%d/%d): %s - %s", i + 1, retries, url, e)
            time.sleep(2 * (i + 1))
    return None


# ==========================================
# Google Sheets
# ==========================================
def get_sheet(spreadsheet_name):
    """Google Sheets接続（失敗してもNoneを返して処理を続行させる）"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
        return gspread.authorize(creds).open(spreadsheet_name).sheet1
    except Exception as e:
        logging.warning("スプレッドシート接続エラー: %s", e)
        return None


# ==========================================
# RSS収集
# ==========================================
def fetch_rss_urls(config, allowed_domains, rss_sources):
    """RSSフィードから対象記事のURLを収集する"""
    logging.info("新着情報を収集中...")
    target_items = []

    if not rss_sources:
        logging.warning("RSSソースが空です")
        return []

    rss_check_days = config.get("rss_check_days", 3)
    history_years = config.get("history_check_years", 5)
    target_keywords = config.get("target_keywords", [])
    normal_threshold = timedelta(days=rss_check_days)
    history_threshold = timedelta(days=365 * history_years)
    current_time = datetime.now()

    # 監視銘柄リスト読み込み
    watch_codes = set()
    try:
        with open("watch_list.txt", "r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if code.isdigit() and len(code) == 4:
                    watch_codes.add(code)
        if watch_codes:
            logging.info("監視リスト適用中: %d 銘柄のみチェックします", len(watch_codes))
        else:
            logging.info("監視リストが空のため、全銘柄をチェックします")
    except FileNotFoundError:
        logging.info("watch_list.txt がないため、全銘柄をチェックします")

    for rss_url in rss_sources:
        if not isinstance(rss_url, str) or not is_allowed_url(rss_url, allowed_domains):
            continue

        try:
            is_yanoshin = "tdnet/list" in rss_url

            # 監視リストによる事前フィルタリング（Yanoshinのみ）
            if is_yanoshin and watch_codes:
                match = re.search(r"/list/(\d{4})\.rss", rss_url)
                if match and match.group(1) not in watch_codes:
                    continue

            res = fetch_with_retry(rss_url, allowed_domains, retries=2, timeout=30)
            if not res:
                continue

            feed = feedparser.parse(res.content)

            # 新着検知で過去データ収集モードに切り替え（Yanoshinのみ）
            enable_history_mode = False
            if is_yanoshin:
                for entry in feed.entries:
                    date_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                    if date_struct:
                        pub_date = datetime.fromtimestamp(mktime(date_struct))
                        if (current_time - pub_date <= normal_threshold) and any(
                            k in entry.get("title", "") for k in target_keywords
                        ):
                            enable_history_mode = True
                            logging.info("新着検知: %s -> 過去データも収集します", rss_url)
                            break

            threshold = history_threshold if enable_history_mode else normal_threshold

            # 記事収集
            for entry in feed.entries:
                date_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                if date_struct:
                    pub_date = datetime.fromtimestamp(mktime(date_struct))
                    if current_time - pub_date > threshold:
                        continue
                elif not enable_history_mode:
                    continue

                title = entry.get("title", "")
                link = entry.get("link", "")
                if any(k in title for k in target_keywords) and link:
                    target_items.append({
                        "url": link,
                        "title": title,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                    })

        except (requests.RequestException, ValueError, KeyError) as e:
            logging.error("RSS処理エラー (%s): %s", rss_url, e)

    return target_items


# ==========================================
# コンテンツ抽出
# ==========================================
def extract_content(url, allowed_domains, timeout=60, max_size=50 * 1024 * 1024):
    """PDF/HTMLからテキスト抽出（OCR対応ハイブリッド版）"""
    logging.info("コンテンツ取得: %s", url)

    res = fetch_with_retry(url, allowed_domains, retries=2, timeout=timeout, max_size=max_size)
    if not res:
        return None

    content_type = res.headers.get("Content-Type", "").lower()
    text_data = ""

    try:
        is_pdf = "pdf" in content_type or url.lower().endswith(".pdf") or res.content[:4] == b"%PDF"

        if is_pdf:
            with pdfplumber.open(BytesIO(res.content)) as pdf:
                for i, p in enumerate(pdf.pages):
                    if i >= 100:
                        break
                    extracted = p.extract_text()
                    if extracted:
                        text_data += extracted + "\n"

            # テキストが少なければOCRにフォールバック
            if len(text_data.strip()) < 50:
                logging.info("テキストレイヤー不足。OCR(画像解析)を実行します...")
                try:
                    images = convert_from_bytes(res.content, dpi=200)
                    for i, img in enumerate(images):
                        if i >= 5:
                            break
                        text_data += pytesseract.image_to_string(img, lang="jpn+eng") + "\n"
                except Exception as e_ocr:
                    logging.error("OCR処理エラー: %s", e_ocr)
                    if "poppler" in str(e_ocr).lower():
                        logging.info("ヒント: OSに 'poppler-utils' のインストールが必要です。")
        else:
            soup = BeautifulSoup(res.content, "html.parser")
            for tag in soup.find_all(["p", "article", "div"]):
                t = tag.get_text().strip()
                if len(t) > 50:
                    text_data += t + "\n"

        return text_data[:400000]

    except Exception as e:
        logging.error("解析エラー: %s", e)
        return None


# ==========================================
# キーワード判定
# ==========================================
def check_keywords_category(text, positive_words, negative_words):
    """キーワードによる簡易スクリーニング"""
    found_pos = [k for k in positive_words if k in text]
    found_neg = [k for k in negative_words if k in text]

    if found_pos and found_neg:
        return True, "混合(注目)", list(set(found_pos + found_neg))
    elif found_pos:
        return True, "ポジティブ", list(set(found_pos))
    elif found_neg:
        return True, "ネガティブ(警戒)", list(set(found_neg))
    else:
        return False, "なし", []


# ==========================================
# LLM分析（マルチプロバイダー対応）
# ==========================================
ANALYSIS_PROMPT = """あなたはプロの機関投資家です。以下の資料テキストから、株価への影響を分析してください。

【コンテキスト】
検出カテゴリ: {category}
検出キーワード: {keywords_str}

以下のJSONフォーマットのみを出力してください。Markdownのバッククォートは不要です。
{{
    "verdict": "強気 / 中立 / 弱気 / 要警戒",
    "reason": "判断の根拠（簡潔に）",
    "summary": "内容の要約（3行以内）",
    "impact": "短期的な株価インパクト予想（大/中/小）"
}}"""


def _analyze_openai(text, prompt, model):
    """OpenAI APIによる分析"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logging.error("OPENAI_API_KEY が設定されていません")
        return None

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    res = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        timeout=300,
    )
    if not res.choices:
        logging.warning("OpenAI: レスポンスにchoicesが含まれていません")
        return None
    return res.choices[0].message.content


def _analyze_ollama(text, prompt, config):
    """Ollama（ローカルLLM）による分析 - データは外部に送信されません"""
    base_url = config.get("ollama_base_url", "http://localhost:11434")
    model = config.get("ollama_model", "qwen3:8b")

    logging.info("Ollama ローカルLLM使用中 (model=%s) - データは外部送信されません", model)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": "json",
    }

    res = requests.post(
        f"{base_url}/api/chat",
        json=payload,
        timeout=300,
    )
    res.raise_for_status()
    data = res.json()
    return data.get("message", {}).get("content", "")


def _analyze_anthropic(text, prompt, model):
    """Anthropic Claude APIによる分析"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logging.error("ANTHROPIC_API_KEY が設定されていません")
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    res = client.messages.create(
        model=model,
        max_tokens=2048,
        system=prompt,
        messages=[
            {"role": "user", "content": text},
        ],
    )
    if not res.content:
        logging.warning("Anthropic: レスポンスにcontentが含まれていません")
        return None
    return res.content[0].text


def _analyze_google(text, prompt, model):
    """Google Gemini APIによる分析"""
    api_key = os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key:
        logging.error("GOOGLE_AI_API_KEY が設定されていません")
        return None

    import google.generativeai as genai
    genai.configure(api_key=api_key)

    gen_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=prompt,
        generation_config={"response_mime_type": "application/json"},
    )
    res = gen_model.generate_content(text)
    return res.text


def analyze_llm(text, category, keywords, config):
    """マルチプロバイダー対応のLLM分析エントリーポイント"""
    provider = config.get("llm_provider", "openai")
    keywords_str = ", ".join(keywords)
    prompt = ANALYSIS_PROMPT.format(category=category, keywords_str=keywords_str)

    logging.info("AI分析実行中... (provider=%s, カテゴリ=%s)", provider, category)

    try:
        if provider == "openai":
            model = config.get("openai_model", "gpt-4o")
            content = _analyze_openai(text, prompt, model)
        elif provider == "ollama":
            content = _analyze_ollama(text, prompt, config)
        elif provider == "anthropic":
            model = config.get("anthropic_model", "claude-sonnet-4-20250514")
            content = _analyze_anthropic(text, prompt, model)
        elif provider == "google":
            model = config.get("google_model", "gemini-2.0-flash")
            content = _analyze_google(text, prompt, model)
        else:
            logging.error("未対応のLLMプロバイダー: %s", provider)
            return None

        if not content:
            return None

        data = json.loads(content)
        expected_keys = {"verdict", "reason", "summary", "impact"}
        if not expected_keys.issubset(data.keys()):
            logging.warning("LLMレスポンスに必要なキーが不足: %s", data.keys())
            return None
        return data

    except json.JSONDecodeError as e:
        logging.error("LLMレスポンスのパースに失敗: %s (length: %d)", e, len(content) if content else 0)
        return None
    except requests.ConnectionError:
        logging.error("Ollamaサーバーに接続できません。Ollamaが起動しているか確認してください: %s",
                      config.get("ollama_base_url", "http://localhost:11434"))
        return None
    except Exception as e:
        logging.error("LLM APIエラー (%s): %s", provider, e)
        return None


# ==========================================
# Slack通知
# ==========================================
def notify_slack(data, item, category, hit_words):
    """Slack通知送信"""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logging.error("SLACK_WEBHOOK_URL が設定されていません")
        return

    from slack_sdk.webhook import WebhookClient
    webhook = WebhookClient(webhook_url)

    color = "#808080"
    if "ポジティブ" in category or data.get("verdict") == "強気":
        color = "#36a64f"
    elif "ネガティブ" in category or data.get("verdict") in ["弱気", "要警戒"]:
        color = "#ff0000"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🔔 {item['title'][:50]}..."}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*AI判断:* {data.get('verdict')}"},
            {"type": "mrkdwn", "text": f"*カテゴリ:* {category}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【根拠】*\n{data.get('reason')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【要約】*\n{data.get('summary')}"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"キーワード: {', '.join(hit_words[:5])}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "原文を見る"}, "url": item["url"]},
        ]},
    ]

    try:
        webhook.send(text=f"分析完了: {item['title']}", blocks=blocks, attachments=[{"color": color}])
    except Exception as e:
        logging.error("Slack送信エラー: %s", e)


# ==========================================
# メール通知
# ==========================================
def notify_email(data, item, category, hit_words, config):
    """メール通知送信（SMTP）"""
    smtp_user = os.environ.get("EMAIL_SMTP_USER")
    smtp_password = os.environ.get("EMAIL_SMTP_PASSWORD")
    if not smtp_user or not smtp_password:
        logging.error("EMAIL_SMTP_USER / EMAIL_SMTP_PASSWORD が設定されていません")
        return

    recipients = config.get("email_to", [])
    if not recipients:
        logging.warning("メール送信先 (email_to) が設定されていません")
        return

    smtp_server = config.get("email_smtp_server", "smtp.gmail.com")
    smtp_port = config.get("email_smtp_port", 587)
    use_tls = config.get("email_use_tls", True)

    verdict = data.get("verdict", "不明")
    verdict_emoji = {"強気": "📈", "弱気": "📉", "要警戒": "⚠️"}.get(verdict, "📊")

    subject = f"{verdict_emoji} [{verdict}] {item['title'][:60]}"

    body = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 株式開示情報 AI分析レポート
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

■ タイトル: {item['title']}
■ AI判断: {verdict}
■ カテゴリ: {category}
■ インパクト: {data.get('impact', '不明')}

【根拠】
{data.get('reason', 'N/A')}

【要約】
{data.get('summary', 'N/A')}

【検出キーワード】
{', '.join(hit_words[:10])}

【原文URL】
{item['url']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
※ このメールは Stock-Analysis-System により自動送信されています。
"""

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        if use_tls:
            server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipients, msg.as_string())
        server.quit()
        logging.info("メール送信完了: %s -> %s", subject[:40], ", ".join(recipients))
    except Exception as e:
        logging.error("メール送信エラー: %s", e)


# ==========================================
# 通知ディスパッチャー
# ==========================================
def send_notifications(data, item, category, hit_words, config):
    """設定に基づき、有効な通知チャンネルすべてに送信する"""
    channels = config.get("notification_channels", ["slack"])

    if "slack" in channels:
        notify_slack(data, item, category, hit_words)

    if "email" in channels:
        notify_email(data, item, category, hit_words, config)


# ==========================================
# メイン処理
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="株式開示情報の自動監視・AI分析システム")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="テスト実行（通知・記録をスキップ）",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="設定ファイルのパス（デフォルト: config.json）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="デバッグログを表示",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "ollama", "anthropic", "google"],
        default=None,
        help="LLMプロバイダーを指定（config.jsonより優先）",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    dry_run = args.dry_run
    if dry_run:
        logging.info("=== ドライラン モード（通知・記録は行いません） ===")

    # 設定読み込み
    config = load_config(args.config)

    # CLIからのプロバイダー指定はconfigより優先
    if args.provider:
        config["llm_provider"] = args.provider

    provider = config.get("llm_provider", "openai")
    spreadsheet_name = config.get("spreadsheet_name", "stock_analysis_log")
    timeout = config.get("request_timeout_sec", 60)
    max_size = config.get("max_content_size_mb", 50) * 1024 * 1024
    sleep_sec = config.get("sleep_between_items_sec", 2)
    positive_words = config.get("positive_words", [])
    negative_words = config.get("negative_words", [])

    # 環境変数チェック（プロバイダーに応じて必須項目が変わる）
    required_vars = []
    if provider == "openai":
        required_vars.append("OPENAI_API_KEY")
    elif provider == "anthropic":
        required_vars.append("ANTHROPIC_API_KEY")
    elif provider == "google":
        required_vars.append("GOOGLE_AI_API_KEY")
    # ollama はローカルなのでAPIキー不要

    notification_channels = config.get("notification_channels", ["slack"])
    if "slack" in notification_channels:
        required_vars.append("SLACK_WEBHOOK_URL")
    if "email" in notification_channels:
        required_vars.extend(["EMAIL_SMTP_USER", "EMAIL_SMTP_PASSWORD"])

    # Google Sheets は任意（設定があれば使用）
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing and not dry_run:
        raise SystemExit(f"エラー: 環境変数が未設定です: {', '.join(missing)}\n"
                         f"  .env.example を参照してください。")
    elif missing:
        logging.warning("未設定の環境変数があります（ドライランのため続行）: %s", ", ".join(missing))

    logging.info("--- 株式監視システム起動 (LLM: %s) ---", provider)

    # RSSソースを読み込んで許可ドメインリストを構築
    rss_sources = []
    try:
        with open("sources.json", "r", encoding="utf-8") as f:
            rss_sources = json.load(f)
        if not isinstance(rss_sources, list):
            logging.error("sources.json はURL文字列のリストである必要があります")
            rss_sources = []
    except FileNotFoundError:
        logging.error("sources.json が見つかりません")
    except json.JSONDecodeError as e:
        logging.error("sources.json のパースに失敗: %s", e)
    allowed_domains = build_allowed_domains(config, rss_sources)
    logging.debug("許可ドメイン: %s", allowed_domains)

    # 処理済みURL読み込み
    processed_urls = set()
    sheet = None
    if not dry_run:
        sheet = get_sheet(spreadsheet_name)
        if sheet:
            try:
                existing_urls = sheet.col_values(9)
                processed_urls = {u for u in existing_urls if u}
            except Exception as e:
                logging.warning("履歴読み込み失敗: %s", e)

    # RSS収集
    target_items = fetch_rss_urls(config, allowed_domains, rss_sources)
    logging.info("%d 件の記事をチェックします...", len(target_items))

    analyzed_count = 0
    for item in target_items:
        url = item["url"]

        if url in processed_urls:
            continue

        text = extract_content(url, allowed_domains, timeout=timeout, max_size=max_size)
        if not text:
            continue

        is_hit, category, hit_words = check_keywords_category(text, positive_words, negative_words)

        if is_hit:
            logging.info("HIT: %s -> %s", item["title"], category)

            if dry_run:
                logging.info("[ドライラン] 分析スキップ: %s (カテゴリ=%s, キーワード=%s)",
                             item["title"], category, ", ".join(hit_words[:5]))
                analyzed_count += 1
            else:
                analysis = analyze_llm(text, category, hit_words, config)
                if analysis:
                    send_notifications(analysis, item, category, hit_words, config)
                    analyzed_count += 1

                    if sheet:
                        row_data = [
                            datetime.now().strftime("%Y-%m-%d %H:%M"),
                            item["title"],
                            analysis.get("verdict"),
                            analysis.get("summary"),
                            analysis.get("reason"),
                            analysis.get("impact"),
                            category,
                            "Success",
                            url,
                        ]
                        for attempt in range(3):
                            try:
                                sheet.append_row(row_data)
                                break
                            except Exception as e:
                                if attempt < 2:
                                    logging.warning("シート記録リトライ (%d/3): %s", attempt + 1, e)
                                    time.sleep(2 * (attempt + 1))
                                else:
                                    logging.error("シート記録失敗（全リトライ失敗）: %s", e)
                else:
                    logging.warning("LLM分析がNullを返しました: %s", item["title"])
        else:
            logging.debug("Pass (キーワードなし): %s", item["title"])

        processed_urls.add(url)
        time.sleep(sleep_sec)

    logging.info("--- 処理完了 (分析件数: %d / 対象: %d) ---", analyzed_count, len(target_items))


if __name__ == "__main__":
    main()
