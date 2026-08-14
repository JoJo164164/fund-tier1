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


def domestic_cats():
    """境內類別碼枚舉：ET{major:03d}{sub:03d}，涵蓋股票/債券/平衡/貨幣/組合/指數等。"""
    cats = []
    for major in range(0, 8):        # 主類 0~7
        for sub in range(1, 41):     # 子類 1~40
            cats.append(("ET{:03d}{:03d}".format(major, sub), "806"))
    return cats


def offshore_cats():
    """境外類別碼枚舉：A=1~25, B=1~12（數字制）。"""
    return [(str(a), str(b)) for a in range(1, 26) for b in range(1, 13)]


def discover_categories(landing_url, page_key):
    return set()   # 保留介面；實際改用 domestic_cats/offshore_cats 枚舉


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


def _pick(d, *keys):
    """從 row dict 找第一個 key 名稱含指定關鍵字的值。"""
    for k, v in d.items():
        ks = str(k)
        if all(kw in ks for kw in keys):
            return v
    return None


def parse_perf_table(html):
    """抓績效表 → 統一乾淨欄位 list。境內單層/境外雙層表頭都轉成同一組。"""
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    tbl = None
    for t in tables:
        flat = "".join(str(c) for c in t.columns)
        if ("基金名稱" in flat or "名稱" in flat) and ("一個月" in flat or "報酬" in flat or "排名" in flat):
            tbl = t
            break
    if tbl is None:
        return []
    tbl.columns = ["|".join(str(x) for x in c) if isinstance(c, tuple) else str(c)
                   for c in tbl.columns]
    rows = []
    for _, r in tbl.iterrows():
        d = {str(k): v for k, v in r.items()}
        name = _pick(d, "基金名稱") or _pick(d, "名稱")
        name = str(name).strip() if name is not None else ""
        if not name or name in ("nan", "基金名稱"):
            continue
        rows.append({
            "名稱": name,
            "公司": str(_pick(d, "基金公司") or _pick(d, "公司") or "").strip(),
            "排名": _num(_pick(d, "排名")),
            "一個月%": _num(_pick(d, "一個月")),
            "三個月%": _num(_pick(d, "三個月")),
            "六個月%": _num(_pick(d, "六個月")),
            "一年%": _num(_pick(d, "一年")),
            "三年%": _num(_pick(d, "三年")),
            "五年%": _num(_pick(d, "五年")),
            "十年%": _num(_pick(d, "十年")),
            "年化標準差": _num(_pick(d, "標準差")),
            "Sharpe": _num(_pick(d, "Sharpe")),
            "Beta": _num(_pick(d, "Beta")),
            "投資區域": str(_pick(d, "投資區域") or "").strip(),
            "淨值日期": str(_pick(d, "日期") or "").strip(),
        })
    return rows


def main():
    all_rows = []
    seen = set()
    plan = [(DOMESTIC, "境內", domestic_cats()),
            (OFFSHORE, "境外", offshore_cats())]
    for page_url, region, cats in plan:
        hit = 0
        for i, (A, B) in enumerate(cats):
            url = "{}?A={}&B={}".format(page_url, A, B)
            try:
                html = get_big5(url)
                rows = parse_perf_table(html)
                new_in_cat = 0
                for d in rows:
                    key = (region, d["名稱"])
                    if key in seen:
                        continue
                    seen.add(key)
                    d["_境內外"] = region
                    d["_類別代碼"] = "{}/{}".format(A, B)
                    all_rows.append(d)
                    new_in_cat += 1
                if new_in_cat:
                    hit += 1
            except Exception:
                pass
            time.sleep(0.25)
            if (i + 1) % 50 == 0:
                print("  {} 掃描 {}/{} 類，已收 {} 檔".format(
                    region, i + 1, len(cats), len(all_rows)))
        print("{}：有資料類別 {} 個，累計 {} 檔".format(region, hit, len(all_rows)))

    if not all_rows:
        print("❌ 沒抓到任何績效資料，需檢查頁面結構")
        sys.exit(1)

    import os
    os.makedirs("data", exist_ok=True)
    keys = ["名稱", "公司", "_境內外", "_類別代碼", "投資區域", "排名",
            "一個月%", "三個月%", "六個月%", "一年%", "三年%", "五年%", "十年%",
            "年化標準差", "Sharpe", "Beta", "淨值日期", "_正規名"]
    for d in all_rows:
        d["_正規名"] = norm_name(d.get("名稱", ""))
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for d in all_rows:
            w.writerow(d)
    print("✅ 寫入 {}：{} 檔績效".format(OUT, len(all_rows)))
    dom = sum(1 for d in all_rows if d["_境內外"] == "境內")
    off = sum(1 for d in all_rows if d["_境內外"] == "境外")
    print("   境內 {} 檔 / 境外 {} 檔".format(dom, off))


if __name__ == "__main__":
    main()
