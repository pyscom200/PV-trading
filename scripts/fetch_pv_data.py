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

# ===== 20MA接触メール通知 設定 =====
MA_SYMBOL = "USD/JPY"
MA_INTERVAL = "15min"
MA_PERIOD = 20
MA_STATE_PATH = "ma_alert_state.json"
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
MA_NOTIFY_TO = "invmaker@gmail.com"


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


def send_ma_touch_email(bar_time, price, ma20):
    """20MA接触をGmail経由でメール通知する"""
    import smtplib
    from email.mime.text import MIMEText

    body = (
        f"USD/JPYが{MA_INTERVAL}足の{MA_PERIOD}MAに接触しました。\n\n"
        f"時刻: {bar_time}\n"
        f"価格: {price}\n"
        f"{MA_PERIOD}MA: {round(ma20, 3)}\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = "【USD/JPY】20MA接触通知"
    msg["From"] = GMAIL_USER
    msg["To"] = MA_NOTIFY_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [MA_NOTIFY_TO], msg.as_string())
    print(f"[OK] 20MA接触メールを送信しました → {MA_NOTIFY_TO}")


def check_ma_touch_alert():
    """USD/JPY 15分足の20MAに直近確定足が接触したか判定し、必要ならメール通知する"""
    if not (GMAIL_USER and GMAIL_APP_PASSWORD):
        print("[INFO] GMAIL_USER / GMAIL_APP_PASSWORD が未設定のため20MA通知はスキップします。")
        return

    params = {
        "symbol": MA_SYMBOL,
        "interval": MA_INTERVAL,
        "outputsize": MA_PERIOD + 2,
        "apikey": API_KEY,
        "timezone": "Asia/Tokyo",
    }
    r = requests.get(BASE_URL, params=params, timeout=15)
    data = r.json()
    if data.get("status") != "ok":
        print(f"[ERROR] 20MA判定用データ取得失敗: {data.get('message', 'unknown error')}")
        return

    values = data.get("values") or []
    if len(values) < MA_PERIOD + 2:
        print("[WARN] 20MA判定に必要な本数のデータが取得できませんでした。")
        return

    # values[0]は形成中の可能性があるため、直近の確定足(values[1])を判定対象にする
    last_closed = values[1]
    ma_source = values[2:2 + MA_PERIOD]
    ma20 = sum(float(v["close"]) for v in ma_source) / MA_PERIOD

    bar_high = float(last_closed["high"])
    bar_low = float(last_closed["low"])
    bar_close = float(last_closed["close"])
    bar_time = last_closed["datetime"]
    touched = bar_low <= ma20 <= bar_high

    prev_state = {}
    if os.path.exists(MA_STATE_PATH):
        with open(MA_STATE_PATH, "r", encoding="utf-8") as f:
            prev_state = json.load(f)

    already_notified = prev_state.get("last_notified_bar") == bar_time

    print(f"[INFO] 20MA({MA_INTERVAL}) = {round(ma20, 3)} / 直近確定足 高値={bar_high} 安値={bar_low} 接触={touched}")

    if touched and not already_notified:
        send_ma_touch_email(bar_time, bar_close, ma20)
        prev_state["last_notified_bar"] = bar_time

    prev_state["last_checked_bar"] = bar_time
    prev_state["ma20"] = round(ma20, 5)
    prev_state["touched"] = touched
    with open(MA_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(prev_state, f, ensure_ascii=False, indent=2)


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

    check_ma_touch_alert()


if __name__ == "__main__":
    main()
