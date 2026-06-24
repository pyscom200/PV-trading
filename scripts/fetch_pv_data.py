#!/usr/bin/env python3
"""
PV値ダッシュボード用 データ取得スクリプト（GitHub Actions版）
毎朝GitHub Actionsが自動実行し、最新の前日PV値と当日の状況を index.html に直接埋め込む。

PV値 = (前日高値 + 前日安値 + 前日終値) ÷ 3
判定  = 当日安値 ≦ 前日PV値 ≦ 当日高値 なら「通過」
"""

import requests
import json
import os
import re
import time
import sys
from datetime import datetime, timezone

# ===== 設定 =====
API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
BASE_URL = "https://api.twelvedata.com/time_series"

# 表示する銘柄: (シンボル, 表示名, pipサイズ)
INSTRUMENTS = [
    ("USD/JPY", "USD/JPY", 0.01),
    ("EUR/USD", "EUR/USD", 0.0001),
    ("GBP/USD", "GBP/USD", 0.0001),
    ("EUR/JPY", "EUR/JPY", 0.01),
    ("AUD/USD", "AUD/USD", 0.0001),
    ("USD/CHF", "USD/CHF", 0.0001),
    ("USD/CAD", "USD/CAD", 0.0001),
    ("NZD/USD", "NZD/USD", 0.0001),
    ("EUR/GBP", "EUR/GBP", 0.0001),
    ("EUR/CHF", "EUR/CHF", 0.0001),
    ("XAU/USD", "GOLD (XAU/USD)", 0.01),
]

OUTPUT_PATH = "pv_data.json"
HTML_PATH = "index.html"
REQUEST_INTERVAL_SEC = 8  # 無料プランのレート制限(1分8クレジット)対策


def fetch_symbol(symbol):
    """直近3本の日足データを取得"""
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": 3,
        "apikey": API_KEY,
        "timezone": "America/New_York",
    }
    r = requests.get(BASE_URL, params=params, timeout=15)
    data = r.json()
    if data.get("status") != "ok":
        return None, data.get("message", "unknown error")
    return data["values"], None


def calc_pv_and_status(values):
    if not values or len(values) < 2:
        return None

    curr = values[0]
    prev = values[1]

    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    prev_close = float(prev["close"])
    pv = (prev_high + prev_low + prev_close) / 3

    curr_open = float(curr["open"])
    curr_high = float(curr["high"])
    curr_low = float(curr["low"])
    curr_close = float(curr["close"])

    touched = curr_low <= pv <= curr_high

    if curr_open > pv:
        direction = "sell"
    elif curr_open < pv:
        direction = "buy"
    else:
        direction = "flat"

    return {
        "date": curr["datetime"],
        "prev_date": prev["datetime"],
        "pv_value": round(pv, 5),
        "curr_open": curr_open,
        "curr_high": curr_high,
        "curr_low": curr_low,
        "curr_close": curr_close,
        "direction": direction,
        "touched": touched,
        "dist_from_open": round(abs(curr_open - pv), 5),
    }


def update_html_with_data(html_path, data):
    """index.html内のEMBEDDED_DATAを最新データに書き換える"""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    json_str = json.dumps(data, ensure_ascii=False)
    new_line = f"const EMBEDDED_DATA = {json_str};"

    pattern = re.compile(r"const EMBEDDED_DATA = .*?;\n")
    if not pattern.search(html):
        print(f"[ERROR] {html_path} 内に EMBEDDED_DATA が見つかりませんでした。")
        return False

    html_new = pattern.sub(new_line + "\n", html, count=1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_new)
    return True


def main():
    if not API_KEY:
        print("[FATAL] TWELVEDATA_API_KEY が設定されていません。")
        sys.exit(1)

    results = []
    errors = []

    for symbol, label, pip_size in INSTRUMENTS:
        values, err = fetch_symbol(symbol)
        if err:
            errors.append({"symbol": symbol, "error": err})
            print(f"[ERROR] {symbol}: {err}")
        else:
            stat = calc_pv_and_status(values)
            if stat:
                stat["symbol"] = symbol
                stat["label"] = label
                stat["pip_size"] = pip_size
                stat["dist_from_open_pips"] = round(stat["dist_from_open"] / pip_size, 1)
                results.append(stat)
                print(f"[OK] {label}: PV={stat['pv_value']} 方向={stat['direction']} 通過={stat['touched']}")
        time.sleep(REQUEST_INTERVAL_SEC)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "instruments": results,
        "errors": errors,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    html_updated = update_html_with_data(HTML_PATH, output)

    print(f"\n保存完了: {OUTPUT_PATH} ({len(results)}件成功, {len(errors)}件エラー)")

    # 取得が0件なら異常終了させて、古いデータでページが上書きされないようにする
    if len(results) == 0:
        print("[FATAL] 全銘柄の取得に失敗しました。コミットを中止します。")
        sys.exit(1)

    if not html_updated:
        print("[FATAL] index.html の更新に失敗しました。")
        sys.exit(1)


if __name__ == "__main__":
    main()
