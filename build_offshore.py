# -*- coding: utf-8 -*-
"""境外基金淨值建庫（cnyes 清單 → yfinance 長歷史 backfill）
=====================================================
定案架構（免費源實測最佳解）：
  清單源：cnyes v2/search/fund → 全市場基金，含 fundClassId(=Yahoo 0P)、isin、
          sitca(空=境外)、名稱、投資區域、幣別。用 sitca=="" 篩境外。
  歷史源：yfinance 抓 fundClassId(0P) 的 max 歷史。非美元類股 0P 裸碼常無資料，
          故「先試裸 0P、再試 .F/.SG/.DE/.MU 等交易所後綴」救援，最大化涵蓋率。
輸出：data/offshore_nav.csv（代碼,日期,淨值,幣別,名稱,來源,資產類型,投資區域）
      代碼=cnyesId(去逗號)；日期 ISO 對齊境內。

模式（環境變數 OFFSHORE_MODE）：
  enumerate → 只抓 cnyes 全境外清單，寫 data/offshore_universe.csv（prepare 用）
  fetch     → 讀 universe，做本段(SEGMENT_INDEX)的 yfinance backfill，寫 seg 檔
分段：SEGMENT_TOTAL / SEGMENT_INDEX（比照境內，按基金 index 間隔切）。
"""
import os
import csv
import time

import requests
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

DATA_DIR = "data"
UNIVERSE_CSV = os.path.join(DATA_DIR, "offshore_universe.csv")
NAV_COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域"]
UNI_COLS = ["代碼", "0P", "isin", "名稱", "幣別", "投資區域", "資產類型"]

LIST = "https://fund.api.cnyes.com/fund/api/v2/search/fund"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://fund.cnyes.com", "Referer": "https://fund.cnyes.com/"}
FIELDS = ("cnyesId,fundClassId,isin,sitca,displayNameLocal,investmentArea,"
          "classCurrencyLocal,categoryAbbr")

MODE = os.environ.get("OFFSHORE_MODE", "fetch")
SEG_TOTAL = int(os.environ.get("SEGMENT_TOTAL", "1"))
SEG_INDEX = int(os.environ.get("SEGMENT_INDEX", "0"))
SLEEP = float(os.environ.get("OFFSHORE_SLEEP", "0.5"))
SUFFIXES = ["", ".F", ".SG", ".DE", ".MU", ".L", ".SW"]


def _seg_name(base):
    return base if SEG_TOTAL <= 1 else base.replace(".csv", "_seg{:02d}.csv".format(SEG_INDEX))


def classify_asset(name, cat):
    s = (name or "") + (cat or "")
    for k, v in [("貨幣", "貨幣市場"), ("債", "債券型"), ("REIT", "股票型"),
                 ("房地產", "股票型"), ("股票", "股票型"), ("平衡", "平衡型"),
                 ("多重資產", "平衡型"), ("組合", "組合型")]:
        if k in s:
            return v
    return "未分類"


def enumerate_universe():
    funds, page, last = [], 1, 1
    while page <= last:
        try:
            r = requests.get(LIST, params={"order": "priceDate", "sort": "desc",
                             "page": page, "institutional": 0, "fields": FIELDS},
                             headers=H, timeout=30)
            it = r.json().get("items", {}) or {}
            last = it.get("last_page", 1) or 1
            for d in it.get("data", []) or []:
                if d.get("sitca"):
                    continue
                code = (d.get("cnyesId") or "").replace(",", "")
                if not code:
                    continue
                funds.append({
                    "代碼": code, "0P": d.get("fundClassId") or "",
                    "isin": d.get("isin") or "",
                    "名稱": (d.get("displayNameLocal") or "")[:50],
                    "幣別": d.get("classCurrencyLocal") or "",
                    "投資區域": d.get("investmentArea") or "未分類",
                    "資產類型": classify_asset(d.get("displayNameLocal"), d.get("categoryAbbr")),
                })
        except Exception as e:
            print("  第{}頁失敗：{}".format(page, e))
        if page % 20 == 0:
            print("  已掃 {}/{} 頁，累計境外 {} 檔".format(page, last, len(funds)))
        page += 1
        time.sleep(0.25)
    return funds


def write_universe(funds):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(UNIVERSE_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=UNI_COLS)
        w.writeheader()
        for x in funds:
            w.writerow(x)
    print("→ 寫入 {}：{} 檔境外".format(UNIVERSE_CSV, len(funds)))


def read_universe():
    with open(UNIVERSE_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def yf_history(op):
    if not op or not _HAS_YF:
        return [], ""
    for suf in SUFFIXES:
        sym = op + suf
        try:
            df = yf.Ticker(sym).history(period="max", auto_adjust=True)
            if df is not None and len(df) > 1:
                out = []
                for idx, row in df.iterrows():
                    c = row.get("Close")
                    if c == c and c:
                        out.append((idx.strftime("%Y-%m-%d"), float(c)))
                if out:
                    return out, sym
        except Exception:
            pass
        time.sleep(0.2)
    return [], ""


def fetch_segment():
    uni = read_universe()
    mine = [f for i, f in enumerate(uni) if i % SEG_TOTAL == SEG_INDEX]
    print("本段 {}/{}：負責 {} / 全 {} 檔".format(SEG_INDEX, SEG_TOTAL, len(mine), len(uni)))
    out_path = _seg_name(os.path.join(DATA_DIR, "offshore_nav.csv"))
    os.makedirs(DATA_DIR, exist_ok=True)
    ok, total_rows = 0, 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NAV_COLS)
        w.writeheader()
        for n, fund in enumerate(mine):
            rows, sym = yf_history(fund["0P"])
            if rows:
                ok += 1
                for iso, nav in rows:
                    w.writerow({"代碼": fund["代碼"], "日期": iso, "淨值": nav,
                                "幣別": fund["幣別"], "名稱": fund["名稱"],
                                "來源": "yfinance", "資產類型": fund["資產類型"],
                                "投資區域": fund["投資區域"]})
                total_rows += len(rows)
            if (n + 1) % 50 == 0:
                print("  進度 {}/{}：命中 {} 檔、{:,} 筆".format(n + 1, len(mine), ok, total_rows))
            time.sleep(SLEEP)
    cov = ok / len(mine) if mine else 0
    print("=" * 60)
    print("本段完成：{}/{} 檔有歷史（{:.0%}）、共 {:,} 筆 → {}".format(
        ok, len(mine), cov, total_rows, out_path))
    print("=" * 60)


def main():
    print("境外建庫  MODE={} SEG={}/{}  yfinance={}".format(MODE, SEG_INDEX, SEG_TOTAL, _HAS_YF))
    if MODE == "enumerate":
        funds = enumerate_universe()
        if not funds:
            raise SystemExit("清單抓到 0 檔，cnyes 可能改版")
        write_universe(funds)
    else:
        if not _HAS_YF:
            raise SystemExit("需要 yfinance")
        fetch_segment()


if __name__ == "__main__":
    main()
