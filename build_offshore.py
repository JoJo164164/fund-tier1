# -*- coding: utf-8 -*-
"""
境外基金雙源容錯建庫腳本（GitHub Actions 版）
================================================
依《台灣基金滾動跌幅系統 — 專案憲法 v3》。

雙源策略（自動容錯，不押寶單一來源）：
  ① 優先 yfinance 0P 代碼（乾淨、跟 ETF 同管線、有 15 年歷史者直接用）
  ② 抓不到/歷史太短者，fallback 到 MoneyDJ（清單全、中文名全）
  每檔標記「來源 + 抓到幾年」，讓資料自己顯示真實覆蓋率，不靠事前猜測。

對應憲法：
  鐵律11 範圍不可縮減（境外基金必抓）
  鐵律13 sandbox 無網路 → 連線由 Actions 實跑；本地只驗邏輯
  鐵律16 淨值截至日：每筆記錄實際日期，不對齊
  「抓當下最新NAV、是哪天算哪天、標日期」（使用者裁決）

環境變數（workflow 傳入）：
  OFFSHORE_SOURCE   'yfinance' | 'moneydj' | 'both'（預設 both）
  OFFSHORE_CODES    逗號分隔要抓的代碼（驗證用；空=用內建測試清單）
  OFFSHORE_YEARS    抓幾年歷史（預設 15）
  TIME_BUDGET_MIN   本次最多跑幾分鐘（預設 300）

輸出：
  data/offshore_nav.csv       淨值長表
  data/offshore_progress.json 斷點續傳
  data/offshore_coverage.csv  每檔覆蓋率報告（來源/起訖/年數）— 讓你看真實覆蓋
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
    raise SystemExit("需要 requests（workflow 會 pip install）")

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

DATA_DIR = "data"
NAV_CSV = os.path.join(DATA_DIR, "offshore_nav.csv")
PROGRESS = os.path.join(DATA_DIR, "offshore_progress.json")
COVERAGE_CSV = os.path.join(DATA_DIR, "offshore_coverage.csv")
NAV_COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源"]
COV_COLS = ["代碼", "名稱", "來源", "資料起", "資料截至", "年數", "筆數", "狀態"]

YEARS = int(os.environ.get("OFFSHORE_YEARS", "15"))
TIME_BUDGET_MIN = int(os.environ.get("TIME_BUDGET_MIN", "300"))
SOURCE = os.environ.get("OFFSHORE_SOURCE", "both").lower()
SLEEP = float(os.environ.get("OFFSHORE_SLEEP", "1.0"))

# 內建測試清單：知名境外基金的 (yfinance 0P代碼, MoneyDJ代碼, 中文名)
# 0P 代碼是 Yahoo 給共同基金的專屬前綴（實測 doc86: 0P000019KV = JPM Greater China）
# 正式建庫時此清單由 MoneyDJ 清單頁 + Yahoo 搜尋自動產生；此處為驗證種子。
SEED_FUNDS = [
    {"yf": "0P000019KV", "mdj": "jfzh2", "name": "摩根基金-中國基金A股(美元)(累計)"},
    {"yf": "",           "mdj": "jfz14", "name": "摩根印度基金"},
    {"yf": "",           "mdj": "NBTG1", "name": "路博邁NB次世代通訊A累積(南非幣)"},
    {"yf": "",           "mdj": "FLZ01", "name": "富蘭克林黃金基金美元A"},
    {"yf": "",           "mdj": "FLZ14", "name": "富蘭克林坦伯頓外國基金A"},
]

# ── Yahoo 奇摩基金（境外主源：SSR、有「最長」歷史、ID為Morningstar SecId）──
# 實測：tw.stock.yahoo.com/fund/{ID}/history 為SSR，表格直接在HTML；
#       頁面有「1個月/3個月/6個月/1年/3年/5年/最長」+「下載歷史報價(日期區間)」
YH_HIST_URL = "https://tw.stock.yahoo.com/fund/{fid}/history"
YH_LIST_URL = "https://tw.stock.yahoo.com/fund/offshore/"
YH_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 期間參數候選（實跑時自動試，取回傳最多者；不猜死一種）
YH_PERIOD_PARAMS = [
    "?period=max", "?range=max", "?period=10y", "?range=10y",
    "?period=5y", "?range=5y", "",
]

# 歷史列格式：2026/07/21 → 22.69（SSR純文字）
_YH_ROW_RE = re.compile(r'(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)')


def fetch_yahoo_fund(fid, years=15):
    """抓 Yahoo 奇摩基金歷史淨值。自動嘗試多種期間參數，取回傳最多的那組。

    fid 格式：F00000Q03Y:FO（Morningstar SecId + :FO境外）
    回傳 (list[(date,nav)], 幣別, 錯誤)
    """
    best, best_param = [], None
    for p in YH_PERIOD_PARAMS:
        try:
            r = requests.get(YH_HIST_URL.format(fid=fid) + p,
                             headers=YH_HEADERS, timeout=25)
            if r.status_code != 200:
                continue
            rows = _YH_ROW_RE.findall(r.text)
            out = []
            for ds, nav in rows:
                try:
                    y, m, d = ds.split("/")
                    out.append(("{}-{}-{}".format(y, m, d), float(nav.replace(",", ""))))
                except Exception:
                    continue
            # 去重（同頁可能重複出現）
            out = sorted(set(out))
            if len(out) > len(best):
                best, best_param = out, p
            # 已拿到夠長歷史就不用再試
            if len(best) > 250 * min(years, 3):
                break
            time.sleep(0.2)
        except Exception:
            continue
    if not best:
        return [], "", "yahoo無資料"
    return best, "", None if best_param is None else None


def fetch_yahoo_offshore_list(max_pages=40):
    """抓 Yahoo 奇摩境外基金清單。

    ★ 修正（實測）：原用 /fund/offshore/ 抓到 0 檔（該路徑非清單頁）。
      正確入口為績效排行頁：/fund/offshore/ranking?range={1wk|3mo|1yr|3yr}
      每個維度百大，4維度聯集可涵蓋數百檔（有重疊，自動去重）。
    """
    found = {}
    id_re = re.compile(r'/fund/([A-Z0-9]{8,12}:FO)')
    bases = [
        "https://tw.stock.yahoo.com/fund/offshore/ranking?range={}",
        "https://tw.finance.yahoo.com/fund/offshore/ranking?range={}",
    ]
    for rng in ["1wk", "1mo", "3mo", "1yr", "3yr", "5yr"]:
        got = 0
        for base in bases:
            try:
                r = requests.get(base.format(rng), headers=YH_HEADERS, timeout=25)
                if r.status_code != 200:
                    continue
                for i in id_re.findall(r.text):
                    if i not in found:
                        found[i] = i
                        got += 1
                if got:
                    break  # 這個維度已拿到，換下一個維度
            except Exception:
                continue
        print("  Yahoo排行[{}] → 新增 {}（累計 {}）".format(rng, got, len(found)))
        time.sleep(0.3)
    return list(found.keys())


# ── MoneyDJ 抓取（Big5、傳統server頁，已實測可抓30日淨值）──
MDJ_NAV_URL = "https://www.moneydj.com/funddj/ya/yp010001.djhtm?a={code}"
MDJ_LIST_URL = "https://www.moneydj.com/funddj/ya/yp081001.djhtm"
MDJ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 清單頁每檔連結格式：yp010001.djhtm?a={代碼}（實測含 NBTG1/ALZM9/SHZ19/FTZF9…）
_MDJ_CODE_RE = re.compile(r'yp010001\.djhtm\?a=([A-Za-z0-9]+)')


def fetch_moneydj_list(max_pages=30):
    """抓 MoneyDJ 境外基金全清單。

    ★ 修正（實測）：`?a=` 是**公司代碼**不是頁碼（原以為分頁 → 只抓到首頁100檔）。
      改為：先從首頁抓所有公司連結，再逐家抓該公司的基金列表。
    """
    found = {}
    # ① 先抓首頁（預設列出部分基金 + 公司連結）
    try:
        r = requests.get(MDJ_LIST_URL, headers=MDJ_HEADERS, timeout=25)
        r.encoding = "big5"
        html = r.text
        for c in _MDJ_CODE_RE.findall(html):
            found.setdefault(c, c)
        # 公司代碼：yp081001.djhtm?a=XXX 這類連結
        comp_re = re.compile(r'yp081001\.djhtm\?a=([A-Za-z0-9]+)')
        comps = sorted(set(comp_re.findall(html)))
        print("  MoneyDJ 首頁 → {} 檔基金、{} 家公司".format(len(found), len(comps)))
    except Exception as e:
        print("  MoneyDJ 首頁失敗:", type(e).__name__)
        return [(k, v) for k, v in found.items()]

    # ② 逐家公司抓其基金列表
    for i, comp in enumerate(comps[:max_pages]):
        try:
            r = requests.get("{}?a={}".format(MDJ_LIST_URL, comp),
                             headers=MDJ_HEADERS, timeout=25)
            r.encoding = "big5"
            new = 0
            for c in _MDJ_CODE_RE.findall(r.text):
                if c not in found:
                    found[c] = c
                    new += 1
            if new:
                print("  MoneyDJ 公司[{}] → 新增 {}（累計 {}）".format(comp, new, len(found)))
            time.sleep(0.3)
        except Exception:
            continue
    return [(k, v) for k, v in found.items()]


def fetch_moneydj(code):
    """抓 MoneyDJ 單檔淨值（最近30日）。回傳 (list[(date,nav)], 幣別, 錯誤)。

    已實測（憲法）：yp010001 頁含「最近30日淨值」表，格式 MM/DD → 淨值。
    Big5 編碼，數字不受影響。
    """
    try:
        r = requests.get(MDJ_NAV_URL.format(code=code), headers=MDJ_HEADERS, timeout=20)
        r.encoding = "big5"
        html = r.text
        # 抓「最近30日淨值」表：MM/DD 後接淨值
        rows = re.findall(r'(\d{2}/\d{2})\s*</td>\s*<td[^>]*>\s*([\d,]+\.\d+)', html)
        if not rows:
            # 寬鬆 fallback：任何 MM/DD + 數字組合
            rows = re.findall(r'>(\d{2}/\d{2})<[^>]*>[^0-9]*([\d,]+\.\d{2,4})<', html)
        out = []
        year = dt.date.today().year
        for md, nav in rows:
            try:
                mm, dd = md.split("/")
                # 跨年處理：若月份大於本月，視為去年
                y = year if int(mm) <= dt.date.today().month else year - 1
                d = dt.date(y, int(mm), int(dd))
                out.append((d.isoformat(), float(nav.replace(",", ""))))
            except Exception:
                continue
        cur_m = re.search(r'(TWD|USD|EUR|JPY|新[臺台]幣|美元|歐元|日[圓元])', html)
        cur = cur_m.group(1) if cur_m else ""
        return out, cur, None
    except Exception as e:
        return [], "", "{}: {}".format(type(e).__name__, e)


def fetch_yfinance(code, years):
    """抓 yfinance 0P 代碼歷史淨值。回傳 (list[(date,nav)], 幣別, 錯誤)。"""
    if not _HAS_YF or not code:
        return [], "", "no yf code"
    try:
        t = yf.Ticker(code)
        df = t.history(period="{}y".format(years), auto_adjust=True)
        if df is None or len(df) == 0:
            return [], "", "empty"
        cur = ""
        try:
            cur = t.fast_info.get("currency", "") or ""
        except Exception:
            pass
        out = [(idx.strftime("%Y-%m-%d"), float(row["Close"]))
               for idx, row in df.iterrows() if row["Close"] == row["Close"]]
        return out, cur, None
    except Exception as e:
        return [], "", "{}: {}".format(type(e).__name__, e)


def load_progress():
    try:
        with open(PROGRESS, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_progress(done):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def append_nav(records):
    os.makedirs(DATA_DIR, exist_ok=True)
    exists = os.path.exists(NAV_CSV)
    with open(NAV_CSV, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NAV_COLS)
        if not exists:
            w.writeheader()
        for r in records:
            w.writerow(r)


def append_coverage(row):
    os.makedirs(DATA_DIR, exist_ok=True)
    exists = os.path.exists(COVERAGE_CSV)
    with open(COVERAGE_CSV, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COV_COLS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def build_one(fund):
    """對單檔基金執行三源容錯抓取（Yahoo奇摩 → yfinance → MoneyDJ）。

    優先序理由（憲法v4實測）：
      ① Yahoo奇摩基金：SSR、有「最長」歷史、ID即Morningstar SecId → 回測需要的長歷史
      ② yfinance 0P代碼：同源Yahoo，覆蓋率待實測
      ③ MoneyDJ：只有30天，當最後補網
    """
    name = fund["name"]
    series, cur, src, err = [], "", "", ""

    # ① Yahoo 奇摩基金（境外長歷史主源）
    if SOURCE in ("yahoo", "both") and fund.get("yh"):
        series, cur, err = fetch_yahoo_fund(fund["yh"], YEARS)
        if series:
            src = "yahoo"

    # ② yfinance
    if not series and SOURCE in ("yfinance", "both") and fund.get("yf"):
        series, cur, err = fetch_yfinance(fund["yf"], YEARS)
        if series:
            src = "yfinance"

    # ③ MoneyDJ（30天，補網）
    if not series and SOURCE in ("moneydj", "both") and fund.get("mdj"):
        series, cur, err2 = fetch_moneydj(fund["mdj"])
        if series:
            src = "moneydj"
        else:
            err = "yh/yf:{} | mdj:{}".format(err, err2)

    code_id = fund.get("yh") or fund.get("yf") or fund.get("mdj")
    if not series:
        return [], {"代碼": code_id, "名稱": name, "來源": "無",
                    "資料起": "", "資料截至": "", "年數": 0, "筆數": 0,
                    "狀態": "✗ 三源皆失敗: {}".format(err)}

    dates = [d for d, _ in series]
    yrs = round((dt.date.fromisoformat(max(dates)) - dt.date.fromisoformat(min(dates))).days / 365.25, 1)
    recs = [{"代碼": code_id, "日期": d, "淨值": v, "幣別": cur, "名稱": name[:40], "來源": src}
            for d, v in series]
    cov = {"代碼": code_id, "名稱": name[:40], "來源": src,
           "資料起": min(dates), "資料截至": max(dates), "年數": yrs,
           "筆數": len(series), "狀態": "✓"}
    return recs, cov


def main():
    codes_env = os.environ.get("OFFSHORE_CODES", "").strip()
    if codes_env.upper() == "ALL":
        # ★ 全市場：優先用 Yahoo 奇摩清單（該源有長歷史），MoneyDJ 當補充
        print("【全市場模式】抓取境外基金清單…")
        yh_ids = fetch_yahoo_offshore_list()
        funds = [{"yh": i, "yf": "", "mdj": "", "name": i} for i in yh_ids]
        print("Yahoo清單：{} 檔".format(len(yh_ids)))
        if len(yh_ids) < 50:  # Yahoo清單抓太少 → 補 MoneyDJ
            print("Yahoo清單偏少，補抓 MoneyDJ 清單…")
            for c, n in fetch_moneydj_list():
                funds.append({"yh": "", "yf": "", "mdj": c, "name": n})
        print("清單合計：{} 檔\n".format(len(funds)))
    elif codes_env:
        funds = [{"yh": c if c.endswith(":FO") else "",
                  "yf": c if c.startswith("0P") else "",
                  "mdj": c if (not c.startswith("0P") and not c.endswith(":FO")) else "",
                  "name": c} for c in codes_env.split(",")]
    else:
        funds = SEED_FUNDS

    print("=" * 60)
    print("境外雙源建庫  來源模式:", SOURCE, "| 基金數:", len(funds), "| 年數:", YEARS)
    print("yfinance 可用:", _HAS_YF)
    print("=" * 60)

    done = load_progress()
    print("已完成(斷點續傳):", len(done))
    t0 = time.time()
    total_new = 0

    for fund in funds:
        code_id = fund.get("yf") or fund.get("mdj")
        if code_id in done:
            continue
        if (time.time() - t0) / 60 > TIME_BUDGET_MIN:
            print("⏸ 達時間預算，存檔停止，下次續傳。")
            break

        recs, cov = build_one(fund)
        if recs:
            append_nav(recs)
            total_new += len(recs)
        append_coverage(cov)
        done.add(code_id)
        save_progress(done)
        print("  {} {} → [{}] {}筆 {}年".format(
            cov["狀態"][:1], fund["name"][:24], cov["來源"],
            cov["筆數"], cov["年數"]))
        time.sleep(SLEEP)

    print("=" * 60)
    print("本次新增", total_new, "筆淨值")
    # 覆蓋率彙總
    if os.path.exists(COVERAGE_CSV):
        with open(COVERAGE_CSV, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        yf_n = sum(1 for r in rows if r["來源"] == "yfinance")
        mdj_n = sum(1 for r in rows if r["來源"] == "moneydj")
        fail_n = sum(1 for r in rows if r["來源"] == "無")
        print("覆蓋率報告: yfinance={} MoneyDJ={} 失敗={} (共{})".format(
            yf_n, mdj_n, fail_n, len(rows)))
        print("→ 詳見 data/offshore_coverage.csv（每檔來源/年數）")
    print("=" * 60)


if __name__ == "__main__":
    main()
