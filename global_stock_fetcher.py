"""
グローバル株式情報取得モジュール

東証以外のあらゆる銘柄（米国株、欧州株、アジア株など）の
決算資料・ニュース・財務データを自動取得し、トレンド分析を行う。
"""

import os
import re
import json
import logging
import time
import requests
import feedparser
from datetime import datetime, timedelta
from urllib.parse import quote_plus

HEADERS = {
    "User-Agent": "StockAnalysisBot/2.0 (+https://github.com/OrosiTororo/Stock-Analysis-System)"
}


# ==========================================
# 銘柄コード判定
# ==========================================
def parse_watch_list(watch_list_path="watch_list.txt"):
    """watch_list.txt を読み込み、東証コードとグローバルティッカーを分類する

    フォーマット:
        4桁数字 → 東証コード (例: 7203)
        英字ティッカー → グローバル (例: AAPL, TSLA)
        ティッカー:市場 → 市場指定付き (例: 7203:JP, AAPL:US, VOW3:DE)
    """
    tse_codes = []
    global_tickers = []

    try:
        with open(watch_list_path, "r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if not code or code.startswith("#"):
                    continue

                # 市場サフィックス付き (例: AAPL:US, 7203:JP)
                if ":" in code:
                    ticker, market = code.split(":", 1)
                    ticker = ticker.strip().upper()
                    market = market.strip().upper()
                    if market == "JP" and ticker.isdigit() and len(ticker) == 4:
                        tse_codes.append(ticker)
                    else:
                        global_tickers.append({
                            "ticker": ticker,
                            "market": market,
                            "raw": code,
                        })
                elif code.isdigit() and len(code) == 4:
                    tse_codes.append(code)
                else:
                    # 英字のみ → グローバルティッカーとして扱う
                    global_tickers.append({
                        "ticker": code.upper(),
                        "market": "US",  # デフォルトは米国市場
                        "raw": code,
                    })

    except FileNotFoundError:
        logging.info("watch_list.txt が見つかりません")

    return tse_codes, global_tickers


# ==========================================
# Yahoo Finance yfinance によるデータ取得
# ==========================================
def get_yfinance_ticker_symbol(ticker_info):
    """ティッカー情報からyfinance用のシンボルを生成する"""
    ticker = ticker_info["ticker"]
    market = ticker_info.get("market", "US")

    market_suffix_map = {
        "US": "",
        "JP": ".T",
        "UK": ".L",
        "DE": ".DE",
        "FR": ".PA",
        "HK": ".HK",
        "CN": ".SS",
        "SZ": ".SZ",
        "KR": ".KS",
        "TW": ".TW",
        "AU": ".AX",
        "IN": ".NS",
        "CA": ".TO",
        "SG": ".SI",
        "BR": ".SA",
    }

    suffix = market_suffix_map.get(market, "")
    return f"{ticker}{suffix}"


def fetch_stock_data(ticker_info):
    """yfinance を使用して株価データと財務情報を取得する"""
    try:
        import yfinance as yf
    except ImportError:
        logging.error("yfinance がインストールされていません。pip install yfinance を実行してください")
        return None

    symbol = get_yfinance_ticker_symbol(ticker_info)
    logging.info("株価データ取得中: %s (symbol=%s)", ticker_info["ticker"], symbol)

    try:
        stock = yf.Ticker(symbol)
        info = stock.info or {}

        # 基本情報
        data = {
            "symbol": symbol,
            "ticker": ticker_info["ticker"],
            "market": ticker_info.get("market", "US"),
            "company_name": info.get("longName") or info.get("shortName", symbol),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "currency": info.get("currency", "N/A"),
        }

        # 株価情報
        data["current_price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        data["previous_close"] = info.get("previousClose") or info.get("regularMarketPreviousClose")
        data["market_cap"] = info.get("marketCap")
        data["pe_ratio"] = info.get("trailingPE")
        data["forward_pe"] = info.get("forwardPE")
        data["dividend_yield"] = info.get("dividendYield")
        data["52w_high"] = info.get("fiftyTwoWeekHigh")
        data["52w_low"] = info.get("fiftyTwoWeekLow")
        data["50d_avg"] = info.get("fiftyDayAverage")
        data["200d_avg"] = info.get("twoHundredDayAverage")
        data["beta"] = info.get("beta")

        # 財務情報
        data["revenue"] = info.get("totalRevenue")
        data["net_income"] = info.get("netIncomeToCommon")
        data["profit_margin"] = info.get("profitMargins")
        data["operating_margin"] = info.get("operatingMargins")
        data["roe"] = info.get("returnOnEquity")
        data["debt_to_equity"] = info.get("debtToEquity")
        data["free_cash_flow"] = info.get("freeCashflow")
        data["earnings_growth"] = info.get("earningsGrowth")
        data["revenue_growth"] = info.get("revenueGrowth")

        # 株価履歴（90日分）
        try:
            hist = stock.history(period="3mo")
            if not hist.empty:
                prices = []
                for date, row in hist.iterrows():
                    prices.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "close": round(row["Close"], 2),
                        "volume": int(row["Volume"]),
                    })
                data["price_history"] = prices[-30:]  # 直近30日分のみ
                data["price_change_1m"] = _calc_price_change(prices, 20)
                data["price_change_3m"] = _calc_price_change(prices, len(prices) - 1)
            else:
                data["price_history"] = []
        except Exception as e:
            logging.warning("株価履歴取得エラー (%s): %s", symbol, e)
            data["price_history"] = []

        # 直近の決算情報
        try:
            earnings = stock.earnings_dates
            if earnings is not None and not earnings.empty:
                upcoming = []
                for date, row in earnings.head(5).iterrows():
                    upcoming.append({
                        "date": date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date),
                        "eps_estimate": _safe_float(row.get("EPS Estimate")),
                        "eps_actual": _safe_float(row.get("Reported EPS")),
                        "surprise": _safe_float(row.get("Surprise(%)")),
                    })
                data["earnings_dates"] = upcoming
            else:
                data["earnings_dates"] = []
        except Exception as e:
            logging.debug("決算日程取得エラー (%s): %s", symbol, e)
            data["earnings_dates"] = []

        logging.info("株価データ取得完了: %s (%s)", data["company_name"], symbol)
        return data

    except Exception as e:
        logging.error("yfinance データ取得エラー (%s): %s", symbol, e)
        return None


def _calc_price_change(prices, lookback):
    """株価変化率を計算する"""
    if len(prices) < 2 or lookback <= 0 or lookback >= len(prices):
        return None
    old_price = prices[-(lookback + 1)]["close"]
    new_price = prices[-1]["close"]
    if old_price == 0:
        return None
    return round((new_price - old_price) / old_price * 100, 2)


def _safe_float(val):
    """安全にfloatに変換する"""
    try:
        import math
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


# ==========================================
# ニュース取得
# ==========================================
def fetch_stock_news(ticker_info, config=None):
    """指定銘柄に関するニュースをRSSフィードから取得する"""
    ticker = ticker_info["ticker"]
    company_name = ticker_info.get("company_name", ticker)
    market = ticker_info.get("market", "US")
    news_items = []

    news_check_days = 7
    if config:
        news_check_days = config.get("news_check_days", 7)
    threshold = timedelta(days=news_check_days)
    current_time = datetime.now()

    # Google News RSS（多言語対応）
    news_sources = _build_news_sources(ticker, company_name, market)

    for source in news_sources:
        try:
            res = requests.get(
                source["url"],
                headers=HEADERS,
                timeout=30,
            )
            if res.status_code != 200:
                continue

            feed = feedparser.parse(res.content)

            for entry in feed.entries[:10]:  # 各ソースから最大10件
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")

                # 日付フィルタ
                date_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_date = None
                if date_struct:
                    pub_date = datetime.fromtimestamp(time.mktime(date_struct))
                    if current_time - pub_date > threshold:
                        continue

                if title and link:
                    news_items.append({
                        "title": title,
                        "url": link,
                        "summary": _clean_html(summary),
                        "source": source["name"],
                        "date": pub_date.strftime("%Y-%m-%d") if pub_date else "N/A",
                        "language": source.get("language", "en"),
                    })

        except Exception as e:
            logging.warning("ニュース取得エラー (%s): %s", source["name"], e)

    # 重複除去
    seen_titles = set()
    unique_news = []
    for item in news_items:
        title_key = re.sub(r'\s+', '', item["title"])[:50]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_news.append(item)

    logging.info("ニュース取得完了: %s -> %d 件", ticker, len(unique_news))
    return unique_news


def _build_news_sources(ticker, company_name, market):
    """銘柄に応じたニュースソースURLを構築する"""
    sources = []

    # Google News RSS（英語）
    query_en = quote_plus(f"{ticker} stock earnings")
    sources.append({
        "name": f"Google News ({ticker})",
        "url": f"https://news.google.com/rss/search?q={query_en}&hl=en&gl=US&ceid=US:en",
        "language": "en",
    })

    # Google News RSS（日本語）- 日本市場の場合
    if market == "JP":
        query_jp = quote_plus(f"{ticker} 決算 株価")
        sources.append({
            "name": f"Google News JP ({ticker})",
            "url": f"https://news.google.com/rss/search?q={query_jp}&hl=ja&gl=JP&ceid=JP:ja",
            "language": "ja",
        })
    else:
        # グローバル銘柄でも日本語ニュースを検索
        query_jp = quote_plus(f"{ticker} {company_name} 決算")
        sources.append({
            "name": f"Google News JP ({ticker})",
            "url": f"https://news.google.com/rss/search?q={query_jp}&hl=ja&gl=JP&ceid=JP:ja",
            "language": "ja",
        })

    # Yahoo Finance RSS
    sources.append({
        "name": f"Yahoo Finance ({ticker})",
        "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
        "language": "en",
    })

    return sources


def _clean_html(text):
    """HTMLタグを除去する"""
    if not text:
        return ""
    from bs4 import BeautifulSoup
    return BeautifulSoup(text, "html.parser").get_text().strip()[:500]


# ==========================================
# SEC EDGAR 決算資料取得（米国株向け）
# ==========================================
def fetch_sec_filings(ticker_info, max_filings=5):
    """SEC EDGAR から直近の決算資料を取得する（米国株のみ）"""
    market = ticker_info.get("market", "US")
    ticker = ticker_info["ticker"]

    if market != "US":
        return []

    logging.info("SEC EDGAR 決算資料検索中: %s", ticker)

    try:
        # EDGAR company search
        headers = {
            "User-Agent": "StockAnalysisBot/2.0 research@example.com",
            "Accept": "application/json",
        }

        # Use EDGAR company tickers JSON
        tickers_url = "https://www.sec.gov/files/company_tickers.json"
        res = requests.get(tickers_url, headers=headers, timeout=30)
        if res.status_code != 200:
            logging.warning("SEC EDGAR ティッカー一覧取得失敗: %d", res.status_code)
            return []

        tickers_data = res.json()
        cik = None
        for key, val in tickers_data.items():
            if val.get("ticker", "").upper() == ticker.upper():
                cik = str(val["cik_str"]).zfill(10)
                break

        if not cik:
            logging.info("SEC EDGAR にティッカー %s が見つかりません", ticker)
            return []

        # Get recent filings
        filings_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        res = requests.get(filings_url, headers=headers, timeout=30)
        if res.status_code != 200:
            logging.warning("SEC EDGAR 提出書類取得失敗: %d", res.status_code)
            return []

        filings_data = res.json()
        recent = filings_data.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        descriptions = recent.get("primaryDocDescription", [])
        documents = recent.get("primaryDocument", [])

        target_forms = {"10-K", "10-Q", "8-K", "6-K", "20-F"}
        filings = []

        for i in range(min(len(forms), 50)):
            if forms[i] in target_forms:
                accession_clean = accessions[i].replace("-", "")
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession_clean}/{documents[i]}"
                filings.append({
                    "form": forms[i],
                    "date": dates[i],
                    "description": descriptions[i] if i < len(descriptions) else forms[i],
                    "url": filing_url,
                    "accession": accessions[i],
                })
                if len(filings) >= max_filings:
                    break

        logging.info("SEC EDGAR 決算資料: %s -> %d 件", ticker, len(filings))
        return filings

    except Exception as e:
        logging.error("SEC EDGAR エラー (%s): %s", ticker, e)
        return []


# ==========================================
# トレンド・予測分析テキスト生成
# ==========================================
def build_analysis_context(ticker_info, stock_data, news_items, sec_filings=None):
    """LLM分析用のコンテキストテキストを構築する"""
    parts = []
    ticker = ticker_info["ticker"]
    market = ticker_info.get("market", "US")

    # ヘッダー
    parts.append(f"=== 銘柄分析: {ticker} ({market}) ===\n")

    # 基本情報
    if stock_data:
        parts.append("【企業基本情報】")
        parts.append(f"企業名: {stock_data.get('company_name', 'N/A')}")
        parts.append(f"セクター: {stock_data.get('sector', 'N/A')}")
        parts.append(f"業種: {stock_data.get('industry', 'N/A')}")
        parts.append(f"通貨: {stock_data.get('currency', 'N/A')}")
        parts.append("")

        # 株価情報
        parts.append("【株価情報】")
        if stock_data.get("current_price"):
            parts.append(f"現在株価: {stock_data['current_price']}")
        if stock_data.get("previous_close"):
            parts.append(f"前日終値: {stock_data['previous_close']}")
        if stock_data.get("market_cap"):
            parts.append(f"時価総額: {_format_number(stock_data['market_cap'])}")
        if stock_data.get("pe_ratio"):
            parts.append(f"PER(実績): {stock_data['pe_ratio']:.2f}")
        if stock_data.get("forward_pe"):
            parts.append(f"PER(予想): {stock_data['forward_pe']:.2f}")
        if stock_data.get("dividend_yield"):
            parts.append(f"配当利回り: {stock_data['dividend_yield']*100:.2f}%")
        if stock_data.get("52w_high"):
            parts.append(f"52週高値: {stock_data['52w_high']}")
        if stock_data.get("52w_low"):
            parts.append(f"52週安値: {stock_data['52w_low']}")
        if stock_data.get("50d_avg"):
            parts.append(f"50日移動平均: {stock_data['50d_avg']:.2f}")
        if stock_data.get("200d_avg"):
            parts.append(f"200日移動平均: {stock_data['200d_avg']:.2f}")
        if stock_data.get("beta"):
            parts.append(f"ベータ: {stock_data['beta']:.2f}")
        parts.append("")

        # 株価変動
        if stock_data.get("price_change_1m") is not None:
            parts.append(f"1ヶ月株価変動: {stock_data['price_change_1m']:+.2f}%")
        if stock_data.get("price_change_3m") is not None:
            parts.append(f"3ヶ月株価変動: {stock_data['price_change_3m']:+.2f}%")
        parts.append("")

        # 財務情報
        parts.append("【財務指標】")
        if stock_data.get("revenue"):
            parts.append(f"売上高: {_format_number(stock_data['revenue'])}")
        if stock_data.get("net_income"):
            parts.append(f"純利益: {_format_number(stock_data['net_income'])}")
        if stock_data.get("profit_margin"):
            parts.append(f"利益率: {stock_data['profit_margin']*100:.2f}%")
        if stock_data.get("operating_margin"):
            parts.append(f"営業利益率: {stock_data['operating_margin']*100:.2f}%")
        if stock_data.get("roe"):
            parts.append(f"ROE: {stock_data['roe']*100:.2f}%")
        if stock_data.get("debt_to_equity"):
            parts.append(f"D/Eレシオ: {stock_data['debt_to_equity']:.2f}")
        if stock_data.get("free_cash_flow"):
            parts.append(f"フリーキャッシュフロー: {_format_number(stock_data['free_cash_flow'])}")
        if stock_data.get("earnings_growth"):
            parts.append(f"利益成長率: {stock_data['earnings_growth']*100:.2f}%")
        if stock_data.get("revenue_growth"):
            parts.append(f"売上成長率: {stock_data['revenue_growth']*100:.2f}%")
        parts.append("")

        # 決算日程
        if stock_data.get("earnings_dates"):
            parts.append("【直近の決算実績/予定】")
            for ed in stock_data["earnings_dates"]:
                eps_str = ""
                if ed.get("eps_actual") is not None:
                    eps_str += f"実績EPS: {ed['eps_actual']}"
                if ed.get("eps_estimate") is not None:
                    eps_str += f" / 予想EPS: {ed['eps_estimate']}"
                if ed.get("surprise") is not None:
                    eps_str += f" (サプライズ: {ed['surprise']}%)"
                parts.append(f"  {ed['date']}: {eps_str}")
            parts.append("")

        # 株価履歴サマリー
        if stock_data.get("price_history"):
            parts.append("【直近株価推移（過去30営業日）】")
            history = stock_data["price_history"]
            # 5日おきにサマリー表示
            for i in range(0, len(history), 5):
                h = history[i]
                parts.append(f"  {h['date']}: {h['close']} (出来高: {_format_number(h['volume'])})")
            parts.append("")

    # ニュース
    if news_items:
        parts.append(f"【直近のニュース ({len(news_items)}件)】")
        for i, news in enumerate(news_items[:15], 1):
            parts.append(f"  {i}. [{news['date']}] {news['title']}")
            if news.get("summary"):
                parts.append(f"     概要: {news['summary'][:200]}")
            parts.append(f"     出典: {news['source']}")
        parts.append("")

    # SEC提出書類
    if sec_filings:
        parts.append("【SEC提出書類（直近）】")
        for filing in sec_filings:
            parts.append(f"  {filing['date']} - {filing['form']}: {filing['description']}")
            parts.append(f"     URL: {filing['url']}")
        parts.append("")

    return "\n".join(parts)


def _format_number(num):
    """数値を読みやすい形式にフォーマットする"""
    if num is None:
        return "N/A"
    try:
        num = float(num)
        if abs(num) >= 1e12:
            return f"{num/1e12:.2f}兆"
        elif abs(num) >= 1e8:
            return f"{num/1e8:.2f}億"
        elif abs(num) >= 1e4:
            return f"{num/1e4:.2f}万"
        else:
            return f"{num:,.0f}"
    except (ValueError, TypeError):
        return str(num)


# ==========================================
# グローバル銘柄の包括分析プロンプト
# ==========================================
GLOBAL_ANALYSIS_PROMPT = """あなたはプロの機関投資家兼アナリストです。以下の銘柄データ・ニュース・財務情報を包括的に分析してください。

【分析対象】
銘柄: {ticker} ({market})

【分析の観点】
1. ファンダメンタルズ分析（財務指標、バリュエーション）
2. テクニカル分析（株価トレンド、移動平均との乖離）
3. ニュースセンチメント分析（直近ニュースのポジティブ/ネガティブ傾向）
4. 決算動向（EPSサプライズ、成長トレンド）
5. リスク要因
6. 短期・中期の見通し

以下のJSONフォーマットのみを出力してください。Markdownのバッククォートは不要です。
{{
    "verdict": "強気 / 中立 / 弱気 / 要警戒",
    "reason": "判断の根拠（主要な3つのポイント）",
    "summary": "銘柄の現状サマリー（5行以内）",
    "impact": "短期的な株価インパクト予想（大/中/小）",
    "trend": "現在のトレンド（上昇トレンド / 下降トレンド / レンジ / 転換点）",
    "key_metrics": "注目すべき主要指標とその評価",
    "news_sentiment": "ニュースセンチメント（ポジティブ / ネガティブ / 中立 / 混合）",
    "risks": "主要リスク要因（2-3点）",
    "outlook_short": "短期見通し（1-3ヶ月）",
    "outlook_medium": "中期見通し（3-12ヶ月）"
}}"""


def build_global_analysis_prompt(ticker_info):
    """グローバル銘柄用の分析プロンプトを構築する"""
    return GLOBAL_ANALYSIS_PROMPT.format(
        ticker=ticker_info["ticker"],
        market=ticker_info.get("market", "US"),
    )


# ==========================================
# メイン取得関数
# ==========================================
def fetch_global_stock_info(ticker_info, config=None):
    """グローバル銘柄の全情報を取得する

    Returns:
        dict: {
            "ticker_info": ...,
            "stock_data": ...,
            "news": [...],
            "sec_filings": [...],
            "analysis_context": "LLM分析用テキスト",
            "analysis_prompt": "LLM分析プロンプト",
        }
    """
    ticker = ticker_info["ticker"]
    logging.info("=== グローバル銘柄分析開始: %s (%s) ===", ticker, ticker_info.get("market", "US"))

    # 1. 株価・財務データ取得
    stock_data = fetch_stock_data(ticker_info)

    # 企業名を取得してニュース検索に使用
    if stock_data and stock_data.get("company_name"):
        ticker_info["company_name"] = stock_data["company_name"]

    # 2. ニュース取得
    news_items = fetch_stock_news(ticker_info, config)

    # 3. SEC決算資料取得（米国株のみ）
    sec_filings = fetch_sec_filings(ticker_info)

    # 4. 分析コンテキスト構築
    analysis_context = build_analysis_context(ticker_info, stock_data, news_items, sec_filings)
    analysis_prompt = build_global_analysis_prompt(ticker_info)

    return {
        "ticker_info": ticker_info,
        "stock_data": stock_data,
        "news": news_items,
        "sec_filings": sec_filings,
        "analysis_context": analysis_context,
        "analysis_prompt": analysis_prompt,
    }
