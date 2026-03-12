import os
import re
import json
import time
import logging
import requests
import pdfplumber
import feedparser
import gspread
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from bs4 import BeautifulSoup
from io import BytesIO
from datetime import datetime, timedelta
from time import mktime
from urllib.parse import urlparse
from google.oauth2.service_account import Credentials
from openai import OpenAI
from slack_sdk.webhook import WebhookClient

# ==========================================
# ログ設定
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ==========================================
# ★設定
# ==========================================
# HTTPリクエスト用の共通ヘッダー
HEADERS = {
    'User-Agent': 'StockAnalysisBot/1.0 (+https://github.com/TroroOrosi/Stock-Analysis-System)'
}

# 許可するURLスキーム・ドメインのホワイトリスト（SSRF対策）
ALLOWED_DOMAINS = {
    "prtimes.jp",
    "www.jpx.co.jp",
    "webapi.yanoshin.jp",
    "www.release.tdnet.info",
    "tdnet.info",
}

# リクエストのタイムアウト（秒）
REQUEST_TIMEOUT = 60
MAX_CONTENT_SIZE = 50 * 1024 * 1024  # 50MB

POSITIVE_WORDS = [
    "上方修正", "業績予想の修正", "営業利益率改善", "黒字転換", "赤字縮小",
    "想定を上回る", "過去最高", "V字回復", "自己株式取得", "取得枠の設定",
    "消却", "増配", "配当方針の変更", "DOE", "累進配当", "構造的", "恒常的",
    "収益体質の改善", "固定費削減", "高付加価値", "高収益案件", "価格転嫁"
]

NEGATIVE_WORDS = [
    "下方修正", "未定", "慎重", "厳しい", "減益", "一時的", "反動減",
    "特殊要因", "外部環境", "為替影響", "原材料高", "一過性要因", "先行投資",
    "想定外", "希薄化", "第三者割当", "MSワラント", "CB", "新株予約権", "支配株主"
]

# RSS判定用キーワード
TARGET_KEYWORDS = ["決算", "修正", "配当", "短信", "報告書", "中期経営計画"]

# GPTモデル (最新のモデル名を指定)
GPT_MODEL = "gpt-5.2"

# 環境変数チェック（起動時に必須変数がなければ即終了）
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

if not all([OPENAI_API_KEY, SLACK_WEBHOOK_URL, GOOGLE_CREDENTIALS_JSON]):
    raise SystemExit("❌ エラー: 環境変数(OPENAI_API_KEY, SLACK_WEBHOOK_URL, GOOGLE_CREDENTIALS_JSON)を設定してください。")

# ==========================================
# ユーティリティ関数
# ==========================================

def is_allowed_url(url):
    """URLがホワイトリストに含まれるドメインかチェック（SSRF対策）"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname or ""
        return any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in ALLOWED_DOMAINS
        )
    except Exception:
        return False


def get_sheet():
    """Google Sheets接続（失敗してもNoneを返して処理を続行させる）"""
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS_JSON), scopes=scopes)
        return gspread.authorize(creds).open("stock_analysis_log").sheet1
    except Exception as e:
        logging.warning("スプレッドシート接続エラー: %s", e)
        return None

def fetch_with_retry(url, retries=3, timeout=REQUEST_TIMEOUT):
    """リトライ機能付きのURL取得（ホワイトリストチェック付き）"""
    if not is_allowed_url(url):
        logging.warning("許可されていないドメインへのアクセスをブロック: %s", urlparse(url).hostname)
        return None

    for i in range(retries):
        try:
            res = requests.get(
                url,
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            # レスポンスサイズチェック
            content_length = res.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_CONTENT_SIZE:
                logging.warning("レスポンスが大きすぎます (%s bytes): %s", content_length, url)
                return None
            res.raise_for_status()
            return res
        except Exception as e:
            logging.warning("接続エラー(%d/%d): %s - %s", i + 1, retries, url, e)
            time.sleep(2 * (i + 1))
    return None

def fetch_rss_urls():
    logging.info("新着情報を収集中...")
    target_items = []

    # 時間設定
    NORMAL_THRESHOLD = timedelta(days=3)     # 通常モード
    HISTORY_THRESHOLD = timedelta(days=365*5) # 過去収集モード（Yanoshin新着時）

    current_time = datetime.now()

    # -------------------------------------------------------
    # 1. 監視銘柄リスト(watch_list.txt)の読み込み
    # -------------------------------------------------------
    watch_codes = set()
    try:
        with open('watch_list.txt', 'r', encoding='utf-8') as f:
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

    # RSSソースの読み込み
    rss_sources = []
    try:
        with open('sources.json', 'r', encoding='utf-8') as f:
            rss_sources = json.load(f)
        if not isinstance(rss_sources, list):
            logging.error("sources.json はURL文字列のリストである必要があります")
            return []
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error("sources.json の読み込み失敗: %s", e)
        return []

    for rss_url in rss_sources:
        if not isinstance(rss_url, str) or not is_allowed_url(rss_url):
            continue

        try:
            # -------------------------------------------------------
            # 2. 事前フィルタリング (Yanoshinの場合のみ)
            # -------------------------------------------------------
            is_yanoshin = "tdnet/list" in rss_url

            if is_yanoshin and watch_codes:
                match = re.search(r'/list/(\d{4})\.rss', rss_url)
                if match:
                    code = match.group(1)
                    if code not in watch_codes:
                        continue

            # -------------------------------------------------------
            # 3. RSS取得
            # -------------------------------------------------------
            res = fetch_with_retry(rss_url, retries=2, timeout=30)
            if not res:
                continue

            feed = feedparser.parse(res.content)

            # モード判定
            enable_history_mode = False

            if is_yanoshin:
                for entry in feed.entries:
                    date_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                    if date_struct:
                        pub_date = datetime.fromtimestamp(mktime(date_struct))

                        if (current_time - pub_date <= NORMAL_THRESHOLD) and \
                           any(k in entry.get("title", "") for k in TARGET_KEYWORDS):
                            enable_history_mode = True
                            logging.info("新着検知: %s -> 過去データも収集します", rss_url)
                            break

            # 閾値決定
            threshold = HISTORY_THRESHOLD if enable_history_mode else NORMAL_THRESHOLD
            
            # 記事収集
            for entry in feed.entries:
                date_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                
                if date_struct:
                    pub_date = datetime.fromtimestamp(mktime(date_struct))
                    if current_time - pub_date > threshold:
                        continue
                else:
                    if not enable_history_mode:
                        continue

                title = entry.get("title", "")
                link = entry.get("link", "")
                if any(k in title for k in TARGET_KEYWORDS) and link:
                    target_items.append({
                        "url": link,
                        "title": title,
                        "date": datetime.now().strftime("%Y-%m-%d")
                    })

        except Exception as e:
            logging.error("RSS処理エラー (%s): %s", rss_url, e)
    
    return target_items

def extract_content(url):
    """PDF/HTMLからテキスト抽出（OCR対応ハイブリッド版）"""
    logging.info("コンテンツ取得: %s", url)

    res = fetch_with_retry(url, retries=2)
    if not res:
        return None

    content_type = res.headers.get('Content-Type', '').lower()
    text_data = ""

    try:
        # PDF判定
        is_pdf = 'pdf' in content_type or url.lower().endswith('.pdf') or res.content[:4] == b'%PDF'

        if is_pdf:
            # ---------------------------------------------------------
            # ステップ1: まず高速なpdfplumberでテキスト抽出を試みる
            # ---------------------------------------------------------
            with pdfplumber.open(BytesIO(res.content)) as pdf:
                for i, p in enumerate(pdf.pages):
                    if i > 100: break 
                    extracted = p.extract_text()
                    if extracted:
                        text_data += extracted + "\n"
            
            # ---------------------------------------------------------
            # ステップ2: テキストがほとんど取れない場合、画像PDFとみなしてOCRを実行
            # ---------------------------------------------------------
            if len(text_data.strip()) < 50:
                logging.info("テキストレイヤー不足。OCR(画像解析)を実行します...")
                try:
                    # PDFを画像データに変換 (dpi=200で精度と速度のバランスを取る)
                    images = convert_from_bytes(res.content, dpi=200)
                    
                    for i, img in enumerate(images):
                        if i > 5: break # OCRは負荷が高いので、冒頭5ページのみ解析
                        
                        # 日英混合モードで解析
                        text_data += pytesseract.image_to_string(img, lang='jpn+eng') + "\n"
                        
                except Exception as e_ocr:
                    logging.error("OCR処理エラー: %s", e_ocr)
                    if "poppler" in str(e_ocr).lower():
                        logging.info("ヒント: OSに 'poppler-utils' のインストールが必要です。")

        else:
            # HTMLの場合の処理（変更なし）
            soup = BeautifulSoup(res.content, 'html.parser')
            for tag in soup.find_all(['p', 'article', 'div']):
                t = tag.get_text().strip()
                if len(t) > 50: text_data += t + "\n"

        # GPTのトークン制限に合わせてカット
        return text_data[:400000] 

    except Exception as e:
        logging.error("解析エラー: %s", e)
        return None

def check_keywords_category(text):
    """キーワードによる簡易スクリーニング"""
    found_pos = [k for k in POSITIVE_WORDS if k in text]
    found_neg = [k for k in NEGATIVE_WORDS if k in text]
    
    if found_pos and found_neg:
        return True, "混合(注目)", list(set(found_pos + found_neg))
    elif found_pos:
        return True, "ポジティブ", list(set(found_pos))
    elif found_neg:
        return True, "ネガティブ(警戒)", list(set(found_neg))
    else:
        return False, "なし", []

def analyze_gpt(text, category, keywords):
    """OpenAI APIによる分析"""
    logging.info("AI分析実行中... (%s)", category)
    client = OpenAI(api_key=OPENAI_API_KEY)
    keywords_str = ", ".join(keywords)

    prompt = f"""
    あなたはプロの機関投資家です。以下の資料テキストから、株価への影響を分析してください。

    【コンテキスト】
    検出カテゴリ: {category}
    検出キーワード: {keywords_str}

    以下のJSONフォーマットのみを出力してください。Markdownのバッククォートは不要です。
    {{
        "verdict": "強気 / 中立 / 弱気 / 要警戒",
        "reason": "判断の根拠（簡潔に）",
        "summary": "内容の要約（3行以内）",
        "impact": "短期的な株価インパクト予想（大/中/小）"
    }}
    """

    try:
        res = client.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            timeout=300
        )
        content = res.choices[0].message.content
        data = json.loads(content)
        # レスポンスのバリデーション
        expected_keys = {"verdict", "reason", "summary", "impact"}
        if not expected_keys.issubset(data.keys()):
            logging.warning("GPTレスポンスに必要なキーが不足: %s", data.keys())
            return None
        return data
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logging.error("GPTレスポンスのパースに失敗: %s", e)
        return None
    except Exception as e:
        logging.error("GPT APIエラー: %s", e)
        return None

def notify_slack(data, item, category, hit_words):
    """Slack通知送信"""
    webhook = WebhookClient(SLACK_WEBHOOK_URL)
    
    # 色分け
    color = "#808080" # グレー
    if "ポジティブ" in category or data.get("verdict") == "強気":
        color = "#36a64f" # 緑
    elif "ネガティブ" in category or data.get("verdict") in ["弱気", "要警戒"]:
        color = "#ff0000" # 赤

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🔔 {item['title'][:50]}..."}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*AI判断:* {data.get('verdict')}"},
            {"type": "mrkdwn", "text": f"*カテゴリ:* {category}"}
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【根拠】*\n{data.get('reason')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*【要約】*\n{data.get('summary')}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"キーワード: {', '.join(hit_words[:5])}..."}]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "原文を見る"}, "url": item['url']}
        ]}
    ]
    
    try:
        webhook.send(text=f"分析完了: {item['title']}", blocks=blocks, attachments=[{"color": color}])
    except Exception as e:
        logging.error("Slack送信エラー: %s", e)

# ==========================================
# メイン処理
# ==========================================
def main():
    logging.info("--- 株式監視システム起動 ---")

    # 1. 処理済みURLのロード (Setを使って高速化)
    processed_urls = set()
    sheet = get_sheet()

    if sheet:
        try:
            existing_urls = sheet.col_values(9)
            processed_urls = set(existing_urls)
        except Exception as e:
            logging.warning("履歴読み込み失敗: %s", e)

    # 2. RSSからURL収集
    target_items = fetch_rss_urls()
    
    logging.info("%d 件の記事をチェックします...", len(target_items))

    for item in target_items:
        url = item['url']
        
        # 重複チェック
        if url in processed_urls:
            continue
            
        # 3. コンテンツ抽出 & 解析
        text = extract_content(url)
        if not text:
            # 取得失敗した場合も、何度もリトライしないように一旦処理済みにするかは運用次第
            # 今回はスキップのみ
            continue
            
        # 4. キーワード判定
        is_hit, category, hit_words = check_keywords_category(text)
        
        if is_hit:
            logging.info("HIT: %s -> %s", item['title'], category)
            
            # 5. GPT分析
            analysis = analyze_gpt(text, category, hit_words)
            
            if analysis:
                notify_slack(analysis, item, category, hit_words)
                
                # 結果を記録
                if sheet:
                    try:
                        sheet.append_row([
                            datetime.now().strftime("%Y-%m-%d %H:%M"),
                            item['title'],
                            analysis.get("verdict"),
                            analysis.get("summary"),
                            analysis.get("reason"),
                            analysis.get("impact"),
                            f"{category}",
                            "Success",
                            url
                        ])
                    except Exception as e:
                        logging.warning("シート記録エラー: %s", e)
        else:
            logging.debug("Pass (キーワードなし): %s", item['title'])
        
        # 処理済みリストに追加
        processed_urls.add(url)
        
        # APIレート制限とサーバー負荷軽減のため少し待機
        time.sleep(2)

    logging.info("--- 処理完了 ---")

if __name__ == "__main__":
    main()






