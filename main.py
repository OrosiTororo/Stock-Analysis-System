import os
import requests
import pdfplumber
import io
import schedule
import time
from openai import OpenAI

# 環境変数から設定を読み込む（セキュリティ対策）
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- 機能を定義 ---

def download_and_extract_text(pdf_url):
    print(f"PDF取得中: {pdf_url}")
    try:
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()
        text_data = ""
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            # 最初の2ページだけ抽出（テスト用・コスト節約）
            for page in pdf.pages[:2]: 
                text = page.extract_text()
                if text:
                    text_data += text + "\n"
        return text_data
    except Exception as e:
        print(f"エラー: {e}")
        return None

def analyze_with_gpt(company_name, text_data):
    if not text_data: return "PDFが読めませんでした。"
    
    print("GPT分析中...")
    prompt = f"""
    あなたはプロの機関投資家です。以下の決算資料テキストから、
    1. 投資判断（買い/売り/様子見）
    2. その理由（3行以内）
    3. 売上・利益の重要数値
    をMarkdown形式でまとめてください。
    
    【対象】{company_name}
    【テキスト】{text_data[:4000]}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-5.2", # 運用時はgpt-5.2推奨
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"GPTエラー: {e}"

def notify_slack(message):
    if SLACK_WEBHOOK_URL:
        requests.post(SLACK_WEBHOOK_URL, json={"text": message})
        print("Slack通知完了")
    else:
        print("Slack設定なし: 通知スキップ")

# --- 実行タスク ---

def job():
    print("--- 定期チェック開始 ---")
    
    # 【重要】本番ではここでTDnetなどをスクレイピングしてURLを取得します。
    # 今回は動作確認のため「トヨタ自動車」の固定URLでテストします。
    target_url = "https://global.toyota/pages/global_toyota/ir/financial-results/2024/2024_4q_summary_jp.pdf"
    company = "トヨタ自動車(テスト)"
    
    text = download_and_extract_text(target_url)
    if text:
        result = analyze_with_gpt(company, text)
        msg = f"【決算自動分析】{company}\n{target_url}\n\n{result}"
        notify_slack(msg)
    
    print("--- チェック完了 ---")

# --- 起動設定 ---
if __name__ == "__main__":
    print("GitHub Actionsで実行開始")
    job() # 1回だけ実行して終了する
    print("実行完了")