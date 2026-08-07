# -*- coding: utf-8 -*-
"""合併境外分段檔 → 按「年」切成多檔（避免單檔超過 GitHub 100MB 上限）。
輸出 data/offshore_nav_YYYY.csv 一組；app 用 glob 一起讀。
"""
import csv
import glob
import os
import sys
from collections import defaultdict

COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域"]


def main():
    seen = set()
    by_year = defaultdict(list)
    for fp in sorted(glob.glob("_segs/*/offshore_nav_seg*.csv")):
        with open(fp, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                d = r.get("日期") or ""
                if len(d) < 4:
                    continue
                k = (r.get("代碼"), d)
                if k in seen:
                    continue
                seen.add(k)
                by_year[d[:4]].append(r)

    os.makedirs("data", exist_ok=True)
    # 先清掉舊的分年境外檔(避免殘留)
    for old in glob.glob("data/offshore_nav_*.csv"):
        os.remove(old)

    total = 0
    funds = set()
    for year, rows in sorted(by_year.items()):
        path = "data/offshore_nav_{}.csv".format(year)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in COLS})
                funds.add(r["代碼"])
        total += len(rows)
        sz = os.path.getsize(path) / 1024 / 1024
        print("  {} → {:,} 筆 ({:.1f} MB)".format(path, len(rows), sz))

    print("合併：{} 檔有歷史、{:,} 筆、分 {} 個年度檔".format(len(funds), total, len(by_year)))
    if len(funds) < 100:
        print("❌ 只有 {} 檔，疑似大量失敗，不 commit".format(len(funds)))
        sys.exit(1)


if __name__ == "__main__":
    main()
