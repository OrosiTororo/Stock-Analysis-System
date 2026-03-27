import os
import re
import json
import time
import socket
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials
from global_stock_fetcher import (
    parse_watch_list,
    fetch_global_stock_info,
    build_global_analysis_prompt,
)

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
        "news.google.com",
        "feeds.finance.yahoo.com",
        "efts.sec.gov",
        "data.sec.gov",
        "www.sec.gov",
    ],
    "positive_words_en": [
        "beat expectations", "record revenue", "raised guidance", "upward revision",
        "strong growth", "exceeded estimates", "dividend increase", "buyback",
        "share repurchase", "margin expansion", "outperform", "upgrade",
        "beat consensus", "positive surprise", "accelerating growth",
    ],
    "negative_words_en": [
        "missed expectations", "lowered guidance", "downward revision", "revenue decline",
        "profit warning", "restructuring", "layoffs", "debt concern",
        "dilution", "secondary offering", "downgrade", "underperform",
        "negative surprise", "decelerating growth", "margin compression",
    ],
    "global_stock_enabled": True,
    "news_check_days": 7,
    "global_analysis_sleep_sec": 3,
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


def load_sources_file(sources_path="sources.json"):
    """sources.json を読み込み、URLリストを返す（v2構造化形式・旧形式の両方に対応）"""
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.error("sources.json が見つかりません")
        return []
    except json.JSONDecodeError as e:
        logging.error("sources.json のパースに失敗: %s", e)
        return []

    # 旧形式（URL配列）
    if isinstance(data, list):
        logging.info("sources.json: 旧形式 (%d URLs)", len(data))
        return [u for u in data if isinstance(u, str)]

    # 新形式（v2構造化）
    if isinstance(data, dict) and data.get("version") == 2:
        urls = []
        categories = data.get("categories", {})
        for cat_key, cat_data in categories.items():
            if not cat_data.get("enabled", True):
                logging.debug("カテゴリ無効: %s", cat_key)
                continue
            for source in cat_data.get("sources", []):
                if source.get("enabled", True):
                    urls.append(source["url"])

        # TDNet自動生成
        tdnet_conf = data.get("tdnet_auto_generate", {})
        if tdnet_conf.get("enabled", False):
            base_url = tdnet_conf.get(
                "base_url",
                "https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.rss?limit=1000"
            )
            watch_codes = []
            try:
                with open("watch_list.txt", "r", encoding="utf-8") as f:
                    for line in f:
                        code = line.strip()
                        if code.isdigit() and len(code) == 4:
                            watch_codes.append(code)
            except FileNotFoundError:
                pass

            if watch_codes:
                for code in watch_codes:
                    urls.append(base_url.format(code=code))
                logging.info("TDNet自動生成: %d 銘柄", len(watch_codes))
            else:
                logging.info("TDNet自動生成: watch_list.txt 未設定のため、スキップ")

        logging.info("sources.json: v2形式 (%d URLs, %d カテゴリ)", len(urls), len(categories))
        return urls

    logging.error("sources.json の形式が不正です")
    return []


# ==========================================
# HTTPリクエスト
# ==========================================
HEADERS = {
    "User-Agent": "StockAnalysisBot/2.0 (+https://github.com/OrosiTororo/Stock-Analysis-System)"
}


def _is_private_ip(hostname):
    """ホスト名がプライベートIPアドレスかどうかを判定する（SSRF対策）"""
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        return False


def _resolves_to_private_ip(hostname):
    """DNS解決後のIPアドレスがプライベートかチェック（DNSリバインディング対策）"""
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addrinfos:
            ip_str = sockaddr[0]
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                logging.warning(
                    "DNS解決先がプライベートIPです: %s -> %s", hostname, ip_str
                )
                return True
        return False
    except (socket.gaierror, ValueError, OSError):
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
        if _resolves_to_private_ip(hostname):
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


def _call_llm_provider(text, prompt, config):
    """LLMプロバイダーにリクエストを送信する（共通処理）"""
    provider = config.get("llm_provider", "openai")

    if provider == "openai":
        model = config.get("openai_model", "gpt-4o")
        return _analyze_openai(text, prompt, model)
    elif provider == "ollama":
        return _analyze_ollama(text, prompt, config)
    elif provider == "anthropic":
        model = config.get("anthropic_model", "claude-sonnet-4-20250514")
        return _analyze_anthropic(text, prompt, model)
    elif provider == "google":
        model = config.get("google_model", "gemini-2.0-flash")
        return _analyze_google(text, prompt, model)
    else:
        logging.error("未対応のLLMプロバイダー: %s", provider)
        return None


def _call_llm_with_retry(text, prompt, config, label="LLM分析", max_retries=3):
    """リトライ付きLLM呼び出し（指数バックオフ）"""
    provider = config.get("llm_provider", "openai")
    expected_keys = {"verdict", "reason", "summary", "impact"}

    for attempt in range(max_retries):
        try:
            logging.info("%s実行中... (provider=%s, 試行=%d/%d)", label, provider, attempt + 1, max_retries)

            content = _call_llm_provider(text, prompt, config)
            if not content:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logging.warning("%s: 空レスポンス。%d秒後にリトライ", label, wait)
                    time.sleep(wait)
                    continue
                return None

            data = json.loads(content)
            if not expected_keys.issubset(data.keys()):
                logging.warning("LLMレスポンスに必要なキーが不足: %s", data.keys())
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                return None
            return data

        except json.JSONDecodeError as e:
            logging.warning("LLMレスポンスのパースに失敗 (試行%d): %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            logging.error("LLMレスポンスのパースに全リトライ失敗")
            return None

        except requests.ConnectionError:
            logging.error("Ollamaサーバーに接続できません: %s",
                          config.get("ollama_base_url", "http://localhost:11434"))
            return None

        except Exception as e:
            logging.warning("%sエラー (試行%d/%d): %s", label, attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            logging.error("%s: 全リトライ失敗 (%s)", label, e)
            return None

    return None


def analyze_llm(text, category, keywords, config):
    """マルチプロバイダー対応のLLM分析エントリーポイント"""
    keywords_str = ", ".join(keywords)
    prompt = ANALYSIS_PROMPT.format(category=category, keywords_str=keywords_str)
    return _call_llm_with_retry(text, prompt, config, label=f"AI分析 (カテゴリ={category})")


def analyze_global_stock(analysis_context, analysis_prompt, config):
    """グローバル銘柄の包括分析（ニュース・財務・トレンドを含む）"""
    return _call_llm_with_retry(analysis_context, analysis_prompt, config, label="グローバル銘柄AI分析")


# ==========================================
# グローバル銘柄 Slack通知
# ==========================================
def notify_slack_global(data, ticker_info, stock_data):
    """グローバル銘柄のSlack通知送信"""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logging.error("SLACK_WEBHOOK_URL が設定されていません")
        return

    from slack_sdk.webhook import WebhookClient
    webhook = WebhookClient(webhook_url)

    ticker = ticker_info["ticker"]
    market = ticker_info.get("market", "US")
    company = stock_data.get("company_name", ticker) if stock_data else ticker
    verdict = data.get("verdict", "不明")

    color = "#808080"
    if verdict == "強気":
        color = "#36a64f"
    elif verdict in ["弱気", "要警戒"]:
        color = "#ff0000"

    # 株価情報サマリー
    price_info = ""
    if stock_data and stock_data.get("current_price"):
        price_info = f"💰 {stock_data['current_price']} {stock_data.get('currency', '')}"
        if stock_data.get("price_change_1m") is not None:
            change = stock_data["price_change_1m"]
            emoji = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            price_info += f" | 1M: {emoji} {change:+.2f}%"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🌐 {company} ({ticker}:{market})"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*AI判断:* {verdict}"},
            {"type": "mrkdwn", "text": f"*トレンド:* {data.get('trend', 'N/A')}"},
        ]},
    ]

    if price_info:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": price_info}})

    blocks.extend([
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【根拠】*\n{data.get('reason', 'N/A')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【要約】*\n{data.get('summary', 'N/A')}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*短期見通し:* {data.get('outlook_short', 'N/A')}"},
            {"type": "mrkdwn", "text": f"*中期見通し:* {data.get('outlook_medium', 'N/A')}"},
        ]},
        {"type": "section", "text": {
            "type": "mrkdwn",
            "text": f"*ニュースセンチメント:* {data.get('news_sentiment', 'N/A')} | *リスク:* {data.get('risks', 'N/A')}"
        }},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"インパクト: {data.get('impact', 'N/A')} | 注目指標: {data.get('key_metrics', 'N/A')[:100]}"},
        ]},
    ])

    try:
        webhook.send(
            text=f"グローバル銘柄分析: {company} ({ticker}) - {verdict}",
            blocks=blocks,
            attachments=[{"color": color}],
        )
    except Exception as e:
        logging.error("Slack送信エラー (グローバル): %s", e)


def notify_email_global(data, ticker_info, stock_data, config):
    """グローバル銘柄のメール通知送信"""
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

    ticker = ticker_info["ticker"]
    market = ticker_info.get("market", "US")
    company = stock_data.get("company_name", ticker) if stock_data else ticker
    verdict = data.get("verdict", "不明")
    verdict_emoji = {"強気": "📈", "弱気": "📉", "要警戒": "⚠️"}.get(verdict, "📊")

    subject = f"{verdict_emoji} [{verdict}] {company} ({ticker}:{market}) 分析レポート"

    price_section = ""
    if stock_data and stock_data.get("current_price"):
        price_section = f"""
■ 現在株価: {stock_data['current_price']} {stock_data.get('currency', '')}
■ 時価総額: {stock_data.get('market_cap', 'N/A')}
■ PER: {stock_data.get('pe_ratio', 'N/A')}
"""

    body = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 グローバル銘柄 AI分析レポート
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

■ 銘柄: {company} ({ticker}:{market})
■ AI判断: {verdict}
■ トレンド: {data.get('trend', 'N/A')}
■ インパクト: {data.get('impact', 'N/A')}
{price_section}
【根拠】
{data.get('reason', 'N/A')}

【要約】
{data.get('summary', 'N/A')}

【短期見通し】
{data.get('outlook_short', 'N/A')}

【中期見通し】
{data.get('outlook_medium', 'N/A')}

【ニュースセンチメント】
{data.get('news_sentiment', 'N/A')}

【リスク要因】
{data.get('risks', 'N/A')}

【注目指標】
{data.get('key_metrics', 'N/A')}

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
        logging.info("メール送信完了 (グローバル): %s", subject[:50])
    except Exception as e:
        logging.error("メール送信エラー (グローバル): %s", e)


def send_global_notifications(data, ticker_info, stock_data, config):
    """グローバル銘柄の通知を全チャンネルに送信する"""
    channels = config.get("notification_channels", ["slack"])

    if "slack" in channels:
        notify_slack_global(data, ticker_info, stock_data)

    if "email" in channels:
        notify_email_global(data, ticker_info, stock_data, config)


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
# サマリーレポート
# ==========================================
def _print_summary_report(tse_analyzed, tse_total, global_analyzed, global_results):
    """実行結果のサマリーレポートをログに出力する"""
    logging.info("")
    logging.info("=" * 60)
    logging.info("  実行サマリーレポート (%s)", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logging.info("=" * 60)
    logging.info("  東証銘柄: %d / %d 件分析完了", tse_analyzed, tse_total)
    logging.info("  グローバル銘柄: %d 件分析完了", global_analyzed)

    if global_results:
        ok = [r for r in global_results if r.get("status") == "ok"]
        errors = [r for r in global_results if r.get("status") in ("error", "llm_error", "fetch_error")]

        if ok:
            logging.info("  --- グローバル分析結果 ---")
            for r in ok:
                logging.info(
                    "    %s:%s -> %s (トレンド: %s)",
                    r["ticker"], r["market"],
                    r.get("verdict", "N/A"), r.get("trend", "N/A"),
                )

        if errors:
            logging.info("  --- エラー ---")
            for r in errors:
                logging.info("    %s:%s -> %s", r["ticker"], r["market"], r["status"])

    logging.info("=" * 60)


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
    rss_sources = load_sources_file("sources.json")
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

    logging.info("--- 東証銘柄処理完了 (分析件数: %d / 対象: %d) ---", analyzed_count, len(target_items))

    # ==========================================
    # グローバル銘柄分析パイプライン
    # ==========================================
    global_enabled = config.get("global_stock_enabled", True)
    global_analyzed = 0
    global_results = []  # サマリーレポート用

    if not global_enabled:
        logging.info("グローバル銘柄分析は無効です (global_stock_enabled=false)")
    else:
        _, global_tickers = parse_watch_list("watch_list.txt")

        if not global_tickers:
            logging.info("グローバル銘柄がwatch_listに登録されていません")
        else:
            logging.info("=== グローバル銘柄分析開始 (%d 銘柄) ===", len(global_tickers))
            global_sleep = config.get("global_analysis_sleep_sec", 3)
            max_workers = min(config.get("global_fetch_workers", 4), len(global_tickers))

            # Phase 1: データ取得を並列実行（I/O待ちが多いため並列化が効果的）
            logging.info("データ取得フェーズ開始 (並列度=%d)", max_workers)
            fetch_results = {}

            def _fetch_ticker_data(t_info):
                return t_info["ticker"], fetch_global_stock_info(t_info, config)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_fetch_ticker_data, ti): ti
                    for ti in global_tickers
                }
                for future in as_completed(futures):
                    ti = futures[future]
                    try:
                        ticker_key, result = future.result()
                        fetch_results[ticker_key] = result
                    except Exception as e:
                        logging.error("データ取得エラー (%s): %s", ti["ticker"], e)

            # Phase 2: LLM分析は逐次実行（レートリミット考慮）
            for ticker_info in global_tickers:
                ticker = ticker_info["ticker"]
                market = ticker_info.get("market", "US")
                result = fetch_results.get(ticker)

                try:
                    if not result or not result.get("analysis_context"):
                        logging.warning("データ取得失敗: %s:%s", ticker, market)
                        global_results.append({"ticker": ticker, "market": market, "status": "fetch_error"})
                        continue

                    stock_data = result.get("stock_data")
                    news_count = len(result.get("news", []))
                    sec_count = len(result.get("sec_filings", []))
                    logging.info(
                        "データ取得完了: %s (ニュース=%d件, SEC=%d件)",
                        ticker, news_count, sec_count,
                    )

                    if dry_run:
                        logging.info(
                            "[ドライラン] グローバル分析スキップ: %s:%s (ニュース=%d, SEC=%d)",
                            ticker, market, news_count, sec_count,
                        )
                        global_analyzed += 1
                        global_results.append({"ticker": ticker, "market": market, "status": "dry_run"})
                    else:
                        analysis = analyze_global_stock(
                            result["analysis_context"],
                            result["analysis_prompt"],
                            config,
                        )

                        if analysis:
                            send_global_notifications(analysis, ticker_info, stock_data, config)
                            global_analyzed += 1

                            if sheet:
                                row_data = [
                                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    f"[Global] {stock_data.get('company_name', ticker)} ({ticker}:{market})",
                                    analysis.get("verdict"),
                                    analysis.get("summary"),
                                    analysis.get("reason"),
                                    analysis.get("impact"),
                                    f"トレンド: {analysis.get('trend', 'N/A')}",
                                    "Success",
                                    f"global:{ticker}:{market}",
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
                                            logging.error("シート記録失敗: %s", e)

                            logging.info(
                                "✓ %s:%s -> %s (トレンド: %s)",
                                ticker, market,
                                analysis.get("verdict"),
                                analysis.get("trend", "N/A"),
                            )
                            global_results.append({
                                "ticker": ticker, "market": market,
                                "status": "ok", "verdict": analysis.get("verdict"),
                                "trend": analysis.get("trend"),
                            })
                        else:
                            logging.warning("LLM分析失敗: %s:%s", ticker, market)
                            global_results.append({"ticker": ticker, "market": market, "status": "llm_error"})

                except Exception as e:
                    logging.error("グローバル銘柄処理エラー (%s:%s): %s", ticker, market, e)
                    global_results.append({"ticker": ticker, "market": market, "status": "error", "error": str(e)})

                time.sleep(global_sleep)

            logging.info(
                "=== グローバル銘柄処理完了 (分析件数: %d / 対象: %d) ===",
                global_analyzed, len(global_tickers),
            )

    # ==========================================
    # サマリーレポート出力
    # ==========================================
    _print_summary_report(analyzed_count, len(target_items), global_analyzed, global_results)
    logging.info("--- 全処理完了 ---")


if __name__ == "__main__":
    main()
