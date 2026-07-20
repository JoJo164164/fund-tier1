# -*- coding: utf-8 -*-
"""
SITCA 境內基金淨值歷史建庫腳本（GitHub Actions 版）
=====================================================
依《台灣基金滾動跌幅系統 — 專案憲法》。設計給 GitHub Actions 執行，
零終端機操作：使用者在 repo 的 Actions 分頁按「Run workflow」即可。

核心設計（對應 GitHub Actions 6 小時上限）：
  - 斷點續傳：每抓完一批寫入 CSV + 進度檔，下次自動從斷點接續
  - 禮貌延遲：每次請求間隔 SLEEP 秒，不打爆 SITCA
  - 時間預算：跑到 TIME_BUDGET_MIN 分鐘就主動存檔停止，避免被強制砍斷

環境變數（由 workflow 傳入，使用者改 workflow 檔即可調整）：
  SITCA_COMPANIES   逗號分隔投信代碼，如 "A0036,A0032"（預設安聯+野村）
  SITCA_START       起始日 YYYYMMDD（預設：今天往回推 DAYS_BACK 天）
  SITCA_DAYS_BACK   往回抓幾個營業日（預設 5，驗證用；建庫時改大）
  SITCA_TIME_BUDGET 本次最多跑幾分鐘（預設 300=5小時，留 1 小時緩衝）

輸出：
  data/sitca_nav.csv       淨值長表（代碼,日期,淨值,幣別,名稱,投信,分類）
  data/sitca_progress.json 已完成的 (公司,日期) 清單，供斷點續傳
"""

import os
import re
import csv
import json
import time
import datetime as dt

try:
    import requests
except ImportError:
    raise SystemExit("需要 requests：workflow 會自動 pip install")

# ── 設定（可由環境變數覆蓋）──
URL = "https://www.sitca.org.tw/ROC/Industry/IN2106.aspx?pid=IN2213_02"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": URL,
    "Origin": "https://www.sitca.org.tw",
}
SLEEP = float(os.environ.get("SITCA_SLEEP", "1.0"))            # 每請求間隔秒
TIME_BUDGET_MIN = int(os.environ.get("SITCA_TIME_BUDGET", "300"))
DAYS_BACK = int(os.environ.get("SITCA_DAYS_BACK", "5"))

DATA_DIR = "data"
CSV_PATH = os.path.join(DATA_DIR, "sitca_nav.csv")
PROGRESS_PATH = os.path.join(DATA_DIR, "sitca_progress.json")
CSV_COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "投信代碼", "投信", "分類"]

COMPANIES = {
    "A0001": "兆豐投信", "A0003": "第一金投信", "A0004": "滙豐投信", "A0005": "元大投信",
    "A0006": "景順投信", "A0007": "瀚亞投信", "A0008": "玉山投信", "A0009": "統一投信",
    "A0010": "富邦投信", "A0011": "摩根投信", "A0012": "華南永昌投信", "A0015": "瑞銀投信",
    "A0016": "群益投信", "A0017": "台中銀投信", "A0018": "聯博投信", "A0021": "柏瑞投信",
    "A0022": "復華投信", "A0025": "永豐投信", "A0026": "中國信託投信", "A0027": "宏利投信",
    "A0031": "貝萊德投信", "A0032": "野村投信", "A0033": "聯邦投信", "A0035": "東方匯理投信",
    "A0036": "安聯投信", "A0037": "國泰投信", "A0038": "富達投信", "A0040": "德銀遠東投信",
    "A0041": "凱基投信", "A0042": "施羅德投信", "A0043": "街口投信", "A0045": "富蘭克林華美投信",
    "A0047": "台新投信", "A0048": "合庫投信", "A0049": "大華銀投信", "A0050": "路博邁投信",
}


def classify_etf(code):
    c = str(code).strip().upper()
    if len(c) == 6 and c[5] == "A":
        return "主動ETF-股"
    if len(c) == 6 and c[5] == "D":
        return "主動ETF-債"
    return "被動ETF/共同基金"  # Tier2 再用SITCA類型代號欄細分


def parse_rows(html):
    """兩段式解析（已用三張真實DOM圖驗證：不受單雙引號/class/長名稱影響）。"""
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        if 'DTHeader' in tr or '類型代號' in tr:
            continue
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        tds = [re.sub(r'<[^>]+>', '', t).strip() for t in tds]
        if len(tds) < 8:
            continue
        comp, code, fname, cur, nav = tds[1], tds[3], tds[5], tds[6], tds[7]
        if not re.match(r'^A00\d{2}$', comp):
            continue
        out.append({"代碼": code, "名稱": fname, "幣別": cur, "淨值": nav})
    return out


def _hidden(html, name):
    m = re.search(r'<input[^>]*name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(r'<input[^>]*value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', html)
    return m.group(1) if m else ""


def _detect_fields(html):
    date_name = "ctl00$ContentPlaceHolder1$txtQ_Date"
    company_name = "ctl00$ContentPlaceHolder1$ddlQ_Comid"
    md = re.search(r'name="(ctl00\$[^"]*txt[^"]*[Dd]ate)"', html)
    if md:
        date_name = md.group(1)
    for sm in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        if "A0005" in sm.group(2) or "A0001" in sm.group(2):
            company_name = sm.group(1)
            break
    return date_name, company_name


def fetch_one(session, company, date_str):
    """抓某投信某日全部基金淨值。回傳 (list[dict], 錯誤或None)。"""
    r = session.get(URL, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return [], "GET {}".format(r.status_code)
    html = r.text
    vs = _hidden(html, "__VIEWSTATE")
    if not vs:
        return [], "no VIEWSTATE"
    date_name, company_name = _detect_fields(html)
    payload = {
        "__VIEWSTATE": vs,
        "__VIEWSTATEGENERATOR": _hidden(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(html, "__EVENTVALIDATION"),
        date_name: date_str,
        company_name: company,
    }
    for btn in ["ctl00$ContentPlaceHolder1$btnQuery",
                "ctl00$ContentPlaceHolder1$BtnQuery",
                "ctl00$ContentPlaceHolder1$Button1"]:
        payload.setdefault(btn, "查詢")
    r2 = session.post(URL, headers=HEADERS, data=payload, timeout=30)
    if r2.status_code != 200:
        return [], "POST {}".format(r2.status_code)
    return parse_rows(r2.text), None


def load_progress():
    try:
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return set(tuple(x) for x in json.load(f))
    except Exception:
        return set()


def save_progress(done):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def append_csv(records):
    os.makedirs(DATA_DIR, exist_ok=True)
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        if not exists:
            w.writeheader()
        for r in records:
            w.writerow(r)


def business_days(start, days_back):
    """從 start 往回取 days_back 個工作日（週末跳過；國定假日靠SITCA回空自然略過）。"""
    out = []
    d = start
    while len(out) < days_back:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def main():
    companies_env = os.environ.get("SITCA_COMPANIES", "A0036,A0032")
    companies = [c.strip() for c in companies_env.split(",") if c.strip()]

    start_env = os.environ.get("SITCA_START", "").strip()
    start = (dt.datetime.strptime(start_env, "%Y%m%d").date()
             if start_env else dt.date.today() - dt.timedelta(days=1))
    days = business_days(start, DAYS_BACK)

    print("=" * 60)
    print("SITCA 建庫  投信:", companies, "| 日數:", len(days))
    print("區間:", days[0], "~", days[-1], "| 時間預算:", TIME_BUDGET_MIN, "分")
    print("=" * 60)

    done = load_progress()
    print("已完成(斷點續傳):", len(done), "個(公司,日)組合")

    session = requests.Session()
    t0 = time.time()
    new_records = 0
    tasks_done = 0

    for company in companies:
        cname = COMPANIES.get(company, company)
        for d in days:
            date_str = d.strftime("%Y%m%d")
            key = (company, date_str)
            if key in done:
                continue
            # 時間預算檢查（留緩衝，避免被6小時上限強砍）
            if (time.time() - t0) / 60 > TIME_BUDGET_MIN:
                print("⏸ 達時間預算，存檔停止。下次 Run 自動續傳。")
                save_progress(done)
                _summary(new_records, tasks_done, done)
                return

            rows, err = fetch_one(session, company, date_str)
            if err:
                print("  ✗ {} {} → {}".format(cname, date_str, err))
                time.sleep(SLEEP)
                continue

            recs = []
            for x in rows:
                recs.append({
                    "代碼": x["代碼"],
                    "日期": d.isoformat(),
                    "淨值": x["淨值"],
                    "幣別": x["幣別"],
                    "名稱": x["名稱"][:40],
                    "投信代碼": company,
                    "投信": cname,
                    "分類": classify_etf(x["代碼"]),
                })
            append_csv(recs)
            done.add(key)
            new_records += len(recs)
            tasks_done += 1
            n_active = sum(1 for r in recs if r["分類"].startswith("主動ETF"))
            print("  ✓ {} {} → {} 檔（主動ETF {}）".format(cname, date_str, len(recs), n_active))
            save_progress(done)  # 每筆都存，確保任何中斷都可續傳
            time.sleep(SLEEP)

    _summary(new_records, tasks_done, done)


def _summary(new_records, tasks_done, done):
    print("=" * 60)
    print("本次完成 {} 個(公司,日)，新增 {} 筆淨值記錄".format(tasks_done, new_records))
    print("累計進度:", len(done), "個組合")
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig") as f:
            total = sum(1 for _ in f) - 1
        print("CSV 總記錄數:", total)
    print("=" * 60)


if __name__ == "__main__":
    main()
