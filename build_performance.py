# -*- coding: utf-8 -*-
"""MoneyDJ FundDJ 官方績效+排名 → data/performance.csv
=====================================================
抓現成官方數字（報酬各期間 + 標準差/Sharpe/Beta + 官方排名），不自算(鐵律：現成優先)。
跑在 GitHub Actions（MoneyDJ 是公開站，Actions 連得到）。
境內依類型 yp401000、境外依類型 yp401001(含風險)。Big5 編碼。
"""
import csv
import io
import re
import sys
import time

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
BASE = "https://www.moneydj.com/funddj/ya/"
DOMESTIC = BASE + "yp401000.djhtm"     # 境內依類型（報酬 1M~10Y）
OFFSHORE = BASE + "yp401001.djhtm"     # 境外依類型（報酬 + 風險）
OUT = "data/performance.csv"


def get_big5(url):
    r = requests.get(url, headers=H, timeout=60)
    r.encoding = "big5"
    return r.text


def discover_categories(landing_url, page_key):
    """從排名頁抽出所有類別參數 (A,B)。"""
    try:
        html = get_big5(landing_url)
    except Exception as e:
        print("  類別探索失敗 {}: {}".format(page_key, e))
        return set()
    pat = re.escape(page_key) + r"\.djhtm\?A=([A-Za-z0-9]+)&B=([0-9]+)"
    cats = set(re.findall(pat, html))
    # 也抓大小寫變體
    cats |= set(re.findall(page_key.upper() + r"\.DJHTM\?A=([A-Za-z0-9]+)&B=([0-9]+)", html))
    return cats


def _num(x):
    """'1,380.89' / 'N/A' → float 或 None。"""
    s = str(x).replace(",", "").strip()
    if s in ("", "N/A", "--", "nan"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def norm_name(name):
    """基金名正規化（給跟我們的庫 join 用）：去空白/全形/常見雜訊。"""
    s = str(name)
    for ch in [" ", "\u3000", "\t", "(", ")", "（", "）", "-", "－"]:
        s = s.replace(ch, "")
    return s.strip()


def parse_perf_table(html):
    """從頁面抓績效表 → list of dict。欄位自動偵測(報酬/風險)。"""
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    tbl = None
    for t in tables:
        header = "".join(str(c) for c in t.columns) + "".join(str(c) for c in t.iloc[0].tolist()) if len(t) else ""
        if "排名" in header or "一個月" in header or "基金名稱" in header:
            tbl = t
            break
    if tbl is None:
        return []
    # 攤平欄名
    tbl.columns = [re.sub(r"\s+", "", str(c)) for c in tbl.columns]
    rows = []
    for _, r in tbl.iterrows():
        d = {re.sub(r"\s+", "", str(k)): v for k, v in r.items()}
        name = None
        for k in d:
            if "基金名稱" in k or "名稱" in k:
                name = str(d[k])
                break
        if not name or name in ("nan", "基金名稱"):
            continue
        rows.append(d)
    return rows


def main():
    all_rows = []
    for page_url, page_key, region in [(DOMESTIC, "yp401000", "境內"),
                                        (OFFSHORE, "yp401001", "境外")]:
        cats = discover_categories(page_url, page_key)
        print("{}：發現 {} 個類別".format(region, len(cats)))
        for i, (A, B) in enumerate(sorted(cats)):
            url = "{}?A={}&B={}".format(page_url, A, B)
            try:
                html = get_big5(url)
                rows = parse_perf_table(html)
                for d in rows:
                    d["_類別代碼"] = "{}/{}".format(A, B)
                    d["_境內外"] = region
                    all_rows.append(d)
            except Exception as e:
                print("  類別 {} 失敗: {}".format(A, str(e)[:50]))
            time.sleep(0.4)
            if (i + 1) % 20 == 0:
                print("  {} 已抓 {}/{} 類".format(region, i + 1, len(cats)))

    if not all_rows:
        print("❌ 沒抓到任何績效資料，可能頁面結構改版，需檢查")
        sys.exit(1)

    # 統一欄位輸出
    import os
    os.makedirs("data", exist_ok=True)
    keys = set()
    for d in all_rows:
        keys |= set(d.keys())
    keys = sorted(keys)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for d in all_rows:
            w.writerow(d)
    print("✅ 寫入 {}：{} 檔績效、{} 欄".format(OUT, len(all_rows), len(keys)))
    print("欄位：", keys)


if __name__ == "__main__":
    main()
