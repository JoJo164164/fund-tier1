# -*- coding: utf-8 -*-
"""合併境外分段檔 → data/offshore_nav.csv（merge job 用）。"""
import csv
import glob
import os
import sys

COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域"]


def main():
    seen = set()
    rows = []
    for fp in sorted(glob.glob("_segs/*/offshore_nav_seg*.csv")):
        with open(fp, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                k = (r.get("代碼"), r.get("日期"))
                if k in seen:
                    continue
                seen.add(k)
                rows.append(r)
    os.makedirs("data", exist_ok=True)
    with open("data/offshore_nav.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})
    funds = len(set(r["代碼"] for r in rows))
    print("合併：{} 檔有歷史、{:,} 筆".format(funds, len(rows)))
    if funds < 100:
        print("❌ 只有 {} 檔，疑似大量失敗，不 commit".format(funds))
        sys.exit(1)


if __name__ == "__main__":
    main()
