#!/usr/bin/env python3
"""
情報ソース管理ツール (source_manager.py)

sources.json の情報ソースを管理するCLIツール。
ソースの追加・削除・一覧表示・有効/無効切替・検索・検証が可能。

使い方:
    python source_manager.py list                         # 全ソース一覧
    python source_manager.py list --category exchange     # カテゴリ指定
    python source_manager.py add URL                      # ソース追加
    python source_manager.py add URL --name "名前" --category news
    python source_manager.py remove URL                   # ソース削除
    python source_manager.py enable URL                   # ソース有効化
    python source_manager.py disable URL                  # ソース無効化
    python source_manager.py enable-category exchange     # カテゴリ有効化
    python source_manager.py disable-category exchange    # カテゴリ無効化
    python source_manager.py validate                     # 全ソースの接続テスト
    python source_manager.py search KEYWORD               # ソースをキーワード検索
    python source_manager.py discover                     # おすすめソースを提案
    python source_manager.py stats                        # 統計情報
    python source_manager.py migrate                      # 旧形式からの移行
"""

import os
import re
import sys
import json
import logging
import argparse
import textwrap
from urllib.parse import urlparse
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SOURCES_FILE = "sources.json"

# ==========================================
# 信頼できるおすすめソース一覧
# ==========================================
RECOMMENDED_SOURCES = {
    "exchange": [
        {
            "url": "https://www.jpx.co.jp/rss/markets_news.xml",
            "name": "JPX マーケットニュース",
            "type": "rss",
            "reliability": "official",
            "description": "日本取引所グループの公式マーケットニュース",
        },
        {
            "url": "https://www.jpx.co.jp/rss/equities-suspended.xml",
            "name": "JPX 株式売買停止情報",
            "type": "rss",
            "reliability": "official",
            "description": "上場株式の売買停止・再開情報",
        },
        {
            "url": "https://www.jpx.co.jp/rss/derivatives-suspended.xml",
            "name": "JPX デリバティブ売買停止情報",
            "type": "rss",
            "reliability": "official",
            "description": "デリバティブの売買停止・再開情報",
        },
    ],
    "press": [
        {
            "url": "https://prtimes.jp/index.rdf",
            "name": "PR TIMES",
            "type": "rss",
            "reliability": "official",
            "description": "国内最大級のプレスリリース配信サービス",
        },
    ],
    "news": [
        {
            "url": "https://assets.wor.jp/rss/rdf/nikkei/news.rdf",
            "name": "日経新聞 主要ニュース",
            "type": "rss",
            "reliability": "major_media",
            "description": "日本経済新聞の主要ニュースRSSフィード",
        },
        {
            "url": "https://www.nikkei.com/rss/markets.xml",
            "name": "日経 マーケット",
            "type": "rss",
            "reliability": "major_media",
            "description": "日経新聞マーケット関連ニュース",
        },
        {
            "url": "https://news.yahoo.co.jp/rss/topics/business.xml",
            "name": "Yahoo!ニュース 経済",
            "type": "rss",
            "reliability": "major_media",
            "description": "Yahoo!ニュース経済カテゴリ",
        },
        {
            "url": "https://www.bloomberg.co.jp/feeds/sitemap_news.xml",
            "name": "Bloomberg Japan",
            "type": "rss",
            "reliability": "major_media",
            "description": "ブルームバーグ日本版ニュース",
        },
        {
            "url": "https://jp.reuters.com/rssFeed/businessNews",
            "name": "Reuters Japan ビジネス",
            "type": "rss",
            "reliability": "major_media",
            "description": "ロイター日本版ビジネスニュース",
        },
        {
            "url": "https://toyokeizai.net/list/feed/rss",
            "name": "東洋経済オンライン",
            "type": "rss",
            "reliability": "major_media",
            "description": "東洋経済オンラインの最新記事",
        },
        {
            "url": "https://diamond.jp/feed/index.xml",
            "name": "ダイヤモンド・オンライン",
            "type": "rss",
            "reliability": "major_media",
            "description": "ダイヤモンド社の経済・ビジネスニュース",
        },
    ],
    "government": [
        {
            "url": "https://www.fsa.go.jp/fsanews/fsanews.rdf",
            "name": "金融庁 新着情報",
            "type": "rss",
            "reliability": "official",
            "description": "金融庁の公式発表・新着情報",
        },
        {
            "url": "https://www.boj.or.jp/rss/whatsnew.xml",
            "name": "日本銀行 新着情報",
            "type": "rss",
            "reliability": "official",
            "description": "日本銀行の公式発表",
        },
        {
            "url": "https://www.mof.go.jp/rss/recent.xml",
            "name": "財務省 新着情報",
            "type": "rss",
            "reliability": "official",
            "description": "財務省の新着情報",
        },
    ],
}

RELIABILITY_LABELS = {
    "official": "公式",
    "major_media": "主要メディア",
    "verified": "検証済み",
    "user_added": "ユーザー追加",
}


# ==========================================
# ソースファイル読み書き
# ==========================================
def load_sources(path=SOURCES_FILE):
    """sources.json を読み込む。旧形式（URL配列）も検出する"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.info("sources.json が見つかりません。新規作成します")
        return _create_default_sources()
    except json.JSONDecodeError as e:
        logging.error("sources.json のパースに失敗: %s", e)
        sys.exit(1)

    if isinstance(data, list):
        logging.warning("旧形式の sources.json を検出しました。'migrate' コマンドで新形式に変換できます")
        return _migrate_from_legacy(data)

    if not isinstance(data, dict) or data.get("version") != 2:
        logging.error("sources.json の形式が不正です")
        sys.exit(1)

    return data


def save_sources(data, path=SOURCES_FILE):
    """sources.json に保存"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logging.info("sources.json を保存しました")


def _create_default_sources():
    """デフォルトの sources.json 構造を作成"""
    return {
        "version": 2,
        "description": "株式分析システム - 情報ソース設定ファイル",
        "tdnet_auto_generate": {
            "enabled": True,
            "description": "watch_list.txt の銘柄コードから TDNet RSS を自動生成",
            "base_url": "https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.rss?limit=1000",
        },
        "categories": {
            "exchange": {
                "name": "取引所・公式機関",
                "description": "JPX等の公式情報",
                "enabled": True,
                "sources": [],
            },
            "press": {
                "name": "プレスリリース",
                "description": "企業の公式プレスリリース",
                "enabled": True,
                "sources": [],
            },
            "news": {
                "name": "ニュース・メディア",
                "description": "信頼性の高いニュースメディア",
                "enabled": True,
                "sources": [],
            },
            "government": {
                "name": "官公庁・規制機関",
                "description": "金融庁・財務省等の公式情報",
                "enabled": True,
                "sources": [],
            },
            "user": {
                "name": "ユーザー追加",
                "description": "ユーザーが手動で追加した情報ソース",
                "enabled": True,
                "sources": [],
            },
        },
    }


def _migrate_from_legacy(url_list):
    """旧形式（URL配列）から新形式に変換"""
    data = _create_default_sources()

    for url in url_list:
        if not isinstance(url, str):
            continue
        if "yanoshin.jp" in url:
            continue  # yanoshin はtdnet_auto_generateで自動生成
        if "jpx.co.jp" in url:
            _add_source_to_data(data, url, category="exchange", reliability="official")
        elif "prtimes.jp" in url:
            _add_source_to_data(data, url, category="press", reliability="official")
        else:
            _add_source_to_data(data, url, category="user", reliability="user_added")

    return data


def _add_source_to_data(data, url, name=None, category="user",
                        source_type="rss", reliability="user_added"):
    """data構造にソースを追加（重複チェック付き）"""
    categories = data.get("categories", {})
    if category not in categories:
        logging.error("不明なカテゴリ: %s", category)
        return False

    existing_urls = {s["url"] for s in categories[category].get("sources", [])}
    if url in existing_urls:
        return False

    if not name:
        hostname = urlparse(url).hostname or url
        name = hostname

    source_entry = {
        "url": url,
        "name": name,
        "type": source_type,
        "reliability": reliability,
        "enabled": True,
    }
    categories[category]["sources"].append(source_entry)
    return True


# ==========================================
# URL抽出ヘルパー
# ==========================================
def get_all_urls(data):
    """新形式のsources.jsonから有効な全URLを取得"""
    urls = []
    categories = data.get("categories", {})
    for cat_key, cat_data in categories.items():
        if not cat_data.get("enabled", True):
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
        watch_codes = _load_watch_codes()
        for code in watch_codes:
            urls.append(base_url.format(code=code))

    return urls


def _load_watch_codes():
    """watch_list.txt から銘柄コードを読み込む"""
    codes = []
    try:
        with open("watch_list.txt", "r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if code.isdigit() and len(code) == 4:
                    codes.append(code)
    except FileNotFoundError:
        pass
    return codes


# ==========================================
# CLIコマンド実装
# ==========================================
def cmd_list(args):
    """ソース一覧表示"""
    data = load_sources(args.file)
    categories = data.get("categories", {})

    filter_cat = args.category
    show_disabled = args.all

    print("\n" + "=" * 70)
    print("  📡 情報ソース一覧")
    print("=" * 70)

    # TDNet 自動生成状態
    tdnet = data.get("tdnet_auto_generate", {})
    tdnet_status = "✅ 有効" if tdnet.get("enabled") else "❌ 無効"
    watch_codes = _load_watch_codes()
    code_info = f"{len(watch_codes)} 銘柄" if watch_codes else "全銘柄（watch_list.txt 未設定）"
    print(f"\n  🔄 TDNet 自動生成: {tdnet_status} ({code_info})")

    total_enabled = 0
    total_disabled = 0

    for cat_key, cat_data in categories.items():
        if filter_cat and cat_key != filter_cat:
            continue

        cat_enabled = cat_data.get("enabled", True)
        cat_status = "✅" if cat_enabled else "❌"
        cat_name = cat_data.get("name", cat_key)
        sources = cat_data.get("sources", [])

        enabled_count = sum(1 for s in sources if s.get("enabled", True))
        disabled_count = len(sources) - enabled_count
        total_enabled += enabled_count
        total_disabled += disabled_count

        print(f"\n  {cat_status} [{cat_key}] {cat_name} ({enabled_count}/{len(sources)} 有効)")
        print(f"     {cat_data.get('description', '')}")

        if not sources:
            print("     (ソースなし)")
            continue

        for src in sources:
            src_enabled = src.get("enabled", True)
            if not show_disabled and not src_enabled:
                continue

            status = "  ✅" if src_enabled else "  ❌"
            rel = RELIABILITY_LABELS.get(src.get("reliability", ""), "")
            name = src.get("name", "")
            url = src.get("url", "")
            print(f"     {status} {name} [{rel}]")
            print(f"         {url}")

    print(f"\n  合計: {total_enabled} 有効 / {total_disabled} 無効")
    print("=" * 70 + "\n")


def cmd_add(args):
    """ソース追加"""
    data = load_sources(args.file)
    url = args.url

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        print("❌ エラー: URL は http:// または https:// で始まる必要があります")
        sys.exit(1)

    category = args.category or "user"
    if category not in data.get("categories", {}):
        print(f"❌ エラー: 不明なカテゴリ '{category}'")
        print(f"   利用可能: {', '.join(data['categories'].keys())}")
        sys.exit(1)

    # 重複チェック
    for cat_data in data["categories"].values():
        for src in cat_data.get("sources", []):
            if src["url"] == url:
                print(f"⚠️  このURLは既に登録済みです: {src.get('name', url)}")
                sys.exit(0)

    name = args.name or parsed.hostname or url
    reliability = args.reliability or "user_added"
    source_type = args.type or "rss"

    source_entry = {
        "url": url,
        "name": name,
        "type": source_type,
        "reliability": reliability,
        "enabled": True,
    }
    if args.description:
        source_entry["description"] = args.description

    data["categories"][category]["sources"].append(source_entry)
    save_sources(data, args.file)

    rel_label = RELIABILITY_LABELS.get(reliability, reliability)
    print(f"✅ ソースを追加しました:")
    print(f"   名前: {name}")
    print(f"   URL: {url}")
    print(f"   カテゴリ: {category} ({data['categories'][category]['name']})")
    print(f"   信頼度: {rel_label}")


def cmd_remove(args):
    """ソース削除"""
    data = load_sources(args.file)
    url = args.url
    found = False

    for cat_key, cat_data in data["categories"].items():
        sources = cat_data.get("sources", [])
        for i, src in enumerate(sources):
            if src["url"] == url:
                removed = sources.pop(i)
                found = True
                print(f"✅ ソースを削除しました: {removed.get('name', url)} [{cat_key}]")
                break
        if found:
            break

    if not found:
        print(f"❌ URLが見つかりません: {url}")
        sys.exit(1)

    save_sources(data, args.file)


def cmd_enable(args):
    """ソース有効化"""
    _set_source_enabled(args, True)


def cmd_disable(args):
    """ソース無効化"""
    _set_source_enabled(args, False)


def _set_source_enabled(args, enabled):
    """ソースの有効/無効を切り替える"""
    data = load_sources(args.file)
    url = args.url
    found = False

    for cat_data in data["categories"].values():
        for src in cat_data.get("sources", []):
            if src["url"] == url:
                src["enabled"] = enabled
                found = True
                status = "有効" if enabled else "無効"
                print(f"✅ {src.get('name', url)} を{status}にしました")
                break
        if found:
            break

    if not found:
        print(f"❌ URLが見つかりません: {url}")
        sys.exit(1)

    save_sources(data, args.file)


def cmd_enable_category(args):
    """カテゴリ有効化"""
    _set_category_enabled(args, True)


def cmd_disable_category(args):
    """カテゴリ無効化"""
    _set_category_enabled(args, False)


def _set_category_enabled(args, enabled):
    """カテゴリの有効/無効を切り替える"""
    data = load_sources(args.file)
    category = args.category

    if category not in data.get("categories", {}):
        print(f"❌ 不明なカテゴリ: {category}")
        print(f"   利用可能: {', '.join(data['categories'].keys())}")
        sys.exit(1)

    data["categories"][category]["enabled"] = enabled
    status = "有効" if enabled else "無効"
    cat_name = data["categories"][category].get("name", category)
    print(f"✅ カテゴリ '{cat_name}' を{status}にしました")
    save_sources(data, args.file)


def cmd_validate(args):
    """全ソースの接続テスト"""
    import requests as req

    data = load_sources(args.file)
    headers = {"User-Agent": "StockAnalysisBot/2.0 (+https://github.com/OrosiTororo/Stock-Analysis-System)"}

    print("\n🔍 ソース接続テスト中...\n")
    results = {"ok": 0, "fail": 0, "skip": 0}

    for cat_key, cat_data in data["categories"].items():
        if not cat_data.get("enabled", True):
            continue
        for src in cat_data.get("sources", []):
            if not src.get("enabled", True):
                results["skip"] += 1
                continue

            url = src["url"]
            name = src.get("name", url)
            try:
                res = req.head(url, headers=headers, timeout=10, allow_redirects=True)
                if res.status_code < 400:
                    print(f"  ✅ {name} ({res.status_code})")
                    results["ok"] += 1
                else:
                    print(f"  ❌ {name} (HTTP {res.status_code})")
                    results["fail"] += 1
            except req.RequestException as e:
                print(f"  ❌ {name} ({e})")
                results["fail"] += 1

    print(f"\n結果: {results['ok']} 成功 / {results['fail']} 失敗 / {results['skip']} スキップ\n")


def cmd_search(args):
    """ソースをキーワードで検索"""
    data = load_sources(args.file)
    keyword = args.keyword.lower()
    found = False

    print(f"\n🔍 '{args.keyword}' で検索中...\n")

    # 登録済みソースを検索
    for cat_key, cat_data in data["categories"].items():
        for src in cat_data.get("sources", []):
            name = src.get("name", "").lower()
            url = src.get("url", "").lower()
            desc = src.get("description", "").lower()
            if keyword in name or keyword in url or keyword in desc:
                found = True
                status = "✅" if src.get("enabled", True) else "❌"
                rel = RELIABILITY_LABELS.get(src.get("reliability", ""), "")
                cat_name = cat_data.get("name", cat_key)
                print(f"  {status} {src.get('name', '')} [{rel}] ({cat_name})")
                print(f"      {src['url']}")

    # おすすめソースも検索
    print(f"\n📌 おすすめ（未登録）:\n")
    registered_urls = set()
    for cat_data in data["categories"].values():
        for src in cat_data.get("sources", []):
            registered_urls.add(src["url"])

    rec_found = False
    for cat_key, sources in RECOMMENDED_SOURCES.items():
        for src in sources:
            if src["url"] in registered_urls:
                continue
            name = src.get("name", "").lower()
            desc = src.get("description", "").lower()
            url = src.get("url", "").lower()
            if keyword in name or keyword in desc or keyword in url or keyword in cat_key:
                rec_found = True
                rel = RELIABILITY_LABELS.get(src.get("reliability", ""), "")
                print(f"  💡 {src['name']} [{rel}] (カテゴリ: {cat_key})")
                print(f"      {src['url']}")
                if src.get("description"):
                    print(f"      {src['description']}")
                print(f"      → 追加: python source_manager.py add \"{src['url']}\" "
                      f"--name \"{src['name']}\" --category {cat_key} "
                      f"--reliability {src['reliability']}")
                print()

    if not found and not rec_found:
        print("  結果なし")


def cmd_discover(args):
    """おすすめソースを提案"""
    data = load_sources(args.file)

    registered_urls = set()
    for cat_data in data["categories"].values():
        for src in cat_data.get("sources", []):
            registered_urls.add(src["url"])

    print("\n" + "=" * 70)
    print("  💡 おすすめ情報ソース")
    print("=" * 70)

    has_recommendations = False

    for cat_key, sources in RECOMMENDED_SOURCES.items():
        unregistered = [s for s in sources if s["url"] not in registered_urls]
        if not unregistered:
            continue

        has_recommendations = True
        cat_name = data["categories"].get(cat_key, {}).get("name", cat_key)
        print(f"\n  📂 {cat_name} ({cat_key}):")

        for src in unregistered:
            rel = RELIABILITY_LABELS.get(src.get("reliability", ""), "")
            print(f"\n    📡 {src['name']} [{rel}]")
            print(f"       {src['url']}")
            if src.get("description"):
                print(f"       {src['description']}")
            print(f"       → 追加: python source_manager.py add \"{src['url']}\" "
                  f"--name \"{src['name']}\" --category {cat_key} "
                  f"--reliability {src['reliability']}")

    if not has_recommendations:
        print("\n  全てのおすすめソースが登録済みです 🎉")

    if args.add_all:
        print("\n  一括追加中...")
        added = 0
        for cat_key, sources in RECOMMENDED_SOURCES.items():
            for src in sources:
                if src["url"] not in registered_urls:
                    success = _add_source_to_data(
                        data, src["url"],
                        name=src["name"],
                        category=cat_key,
                        source_type=src.get("type", "rss"),
                        reliability=src.get("reliability", "verified"),
                    )
                    if success:
                        added += 1
        if added > 0:
            save_sources(data, args.file)
            print(f"  ✅ {added} 件のソースを追加しました")
        else:
            print("  追加するソースはありません")

    print("\n" + "=" * 70 + "\n")


def cmd_stats(args):
    """統計情報を表示"""
    data = load_sources(args.file)

    print("\n" + "=" * 70)
    print("  📊 ソース統計情報")
    print("=" * 70)

    total = 0
    enabled = 0
    by_reliability = {}

    for cat_key, cat_data in data["categories"].items():
        sources = cat_data.get("sources", [])
        cat_enabled = sum(1 for s in sources if s.get("enabled", True))
        total += len(sources)
        enabled += cat_enabled

        cat_name = cat_data.get("name", cat_key)
        cat_status = "✅" if cat_data.get("enabled", True) else "❌"
        print(f"\n  {cat_status} {cat_name}: {cat_enabled}/{len(sources)} 有効")

        for src in sources:
            rel = src.get("reliability", "unknown")
            by_reliability[rel] = by_reliability.get(rel, 0) + 1

    # TDNet
    tdnet = data.get("tdnet_auto_generate", {})
    if tdnet.get("enabled"):
        watch_codes = _load_watch_codes()
        tdnet_count = len(watch_codes) if watch_codes else 0
        mode = f"{tdnet_count} 銘柄" if watch_codes else "全銘柄モード"
        print(f"\n  🔄 TDNet自動生成: {mode}")

    print(f"\n  --- 信頼度別 ---")
    for rel, count in sorted(by_reliability.items(), key=lambda x: -x[1]):
        label = RELIABILITY_LABELS.get(rel, rel)
        print(f"    {label}: {count}")

    print(f"\n  合計: {enabled}/{total} 有効")
    print("=" * 70 + "\n")


def cmd_migrate(args):
    """旧形式から新形式に移行"""
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("❌ sources.json が見つかりません")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ sources.json のパースに失敗: {e}")
        sys.exit(1)

    if isinstance(data, dict) and data.get("version") == 2:
        print("✅ 既に新形式です。移行の必要はありません")
        return

    if not isinstance(data, list):
        print("❌ 不明な形式です")
        sys.exit(1)

    # バックアップ作成
    backup_path = f"sources.json.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"📦 バックアップを作成: {backup_path}")

    # 移行
    yanoshin_count = sum(1 for u in data if isinstance(u, str) and "yanoshin.jp" in u)
    new_data = _migrate_from_legacy(data)
    save_sources(new_data, args.file)

    total_sources = sum(
        len(cat.get("sources", []))
        for cat in new_data["categories"].values()
    )
    print(f"✅ 移行完了!")
    print(f"   旧形式: {len(data)} URL (うち Yanoshin {yanoshin_count})")
    print(f"   新形式: {total_sources} ソース + TDNet自動生成")
    print(f"   ※ Yanoshin URLはtdnet_auto_generate機能に置き換えました")


# ==========================================
# メイン
# ==========================================
def main():
    parser = argparse.ArgumentParser(
        description="株式分析システム - 情報ソース管理ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        使用例:
          %(prog)s list                                     全ソース一覧
          %(prog)s list --category exchange                 カテゴリ指定
          %(prog)s add "https://example.com/rss.xml"        ソース追加
          %(prog)s add "URL" --name "名前" --category news  名前とカテゴリ指定
          %(prog)s remove "URL"                             ソース削除
          %(prog)s enable "URL"                             ソース有効化
          %(prog)s disable "URL"                            ソース無効化
          %(prog)s enable-category news                     カテゴリ有効化
          %(prog)s disable-category news                    カテゴリ無効化
          %(prog)s validate                                 接続テスト
          %(prog)s search 日経                              キーワード検索
          %(prog)s discover                                 おすすめソース提案
          %(prog)s discover --add-all                       おすすめを一括追加
          %(prog)s stats                                    統計情報
          %(prog)s migrate                                  旧形式からの移行
        """),
    )
    parser.add_argument(
        "--file", default=SOURCES_FILE, help="sources.json のパス（デフォルト: sources.json）"
    )

    subparsers = parser.add_subparsers(dest="command", help="コマンド")

    # list
    p_list = subparsers.add_parser("list", help="ソース一覧表示")
    p_list.add_argument("--category", "-c", help="カテゴリでフィルタ")
    p_list.add_argument("--all", "-a", action="store_true", help="無効なソースも表示")

    # add
    p_add = subparsers.add_parser("add", help="ソース追加")
    p_add.add_argument("url", help="追加するURL")
    p_add.add_argument("--name", "-n", help="ソース名")
    p_add.add_argument("--category", "-c", default="user",
                       help="カテゴリ (exchange/press/news/government/user)")
    p_add.add_argument("--type", "-t", default="rss", help="ソースタイプ (rss/api/web)")
    p_add.add_argument("--reliability", "-r", default="user_added",
                       help="信頼度 (official/major_media/verified/user_added)")
    p_add.add_argument("--description", "-d", help="ソースの説明")

    # remove
    p_remove = subparsers.add_parser("remove", help="ソース削除")
    p_remove.add_argument("url", help="削除するURL")

    # enable / disable
    p_enable = subparsers.add_parser("enable", help="ソース有効化")
    p_enable.add_argument("url", help="有効化するURL")
    p_disable = subparsers.add_parser("disable", help="ソース無効化")
    p_disable.add_argument("url", help="無効化するURL")

    # enable-category / disable-category
    p_ecat = subparsers.add_parser("enable-category", help="カテゴリ有効化")
    p_ecat.add_argument("category", help="カテゴリ名")
    p_dcat = subparsers.add_parser("disable-category", help="カテゴリ無効化")
    p_dcat.add_argument("category", help="カテゴリ名")

    # validate
    subparsers.add_parser("validate", help="全ソースの接続テスト")

    # search
    p_search = subparsers.add_parser("search", help="ソースをキーワード検索")
    p_search.add_argument("keyword", help="検索キーワード")

    # discover
    p_discover = subparsers.add_parser("discover", help="おすすめソースを提案")
    p_discover.add_argument("--add-all", action="store_true", help="全おすすめを一括追加")

    # stats
    subparsers.add_parser("stats", help="統計情報")

    # migrate
    subparsers.add_parser("migrate", help="旧形式からの移行")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "enable-category": cmd_enable_category,
        "disable-category": cmd_disable_category,
        "validate": cmd_validate,
        "search": cmd_search,
        "discover": cmd_discover,
        "stats": cmd_stats,
        "migrate": cmd_migrate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
