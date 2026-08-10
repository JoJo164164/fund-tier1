# -*- coding: utf-8 -*-
"""合併境外分段檔 → 依「檔案大小」切成多檔（每檔 <上限，避開 GitHub 100MB）。
輸出 data/offshore_nav_001.csv, 002.csv…；app 用 glob offshore_nav_*.csv 一起讀。
"""
import csv
import glob
import io
import os
import sys

COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域"]
MAX_BYTES = 80 * 1024 * 1024   # 每檔上限 80MB（留安全邊際，遠低於 GitHub 100MB）


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
                rows.append({c: r.get(c, "") for c in COLS})

    os.makedirs("data", exist_ok=True)
    for old in glob.glob("data/offshore_nav_*.csv"):
        os.remove(old)
    if os.path.exists("data/offshore_nav.csv"):
        os.remove("data/offshore_nav.csv")

    # 依累積 bytes 換檔
    def _row_bytes(r):
        buf = io.StringIO()
        csv.writer(buf).writerow([r[c] for c in COLS])
        return len(buf.getvalue().encode("utf-8"))

    header_bytes = len((",".join(COLS) + "\r\n").encode("utf-8"))
    part = 1
    written = []

    def _open(p):
        f = open("data/offshore_nav_{:03d}.csv".format(p), "w",
                 encoding="utf-8-sig", newline="")
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        return f, w

    f, w = _open(part)
    cur = header_bytes
    n_in_part = 0
    for r in rows:
        rb = _row_bytes(r)
        if cur + rb > MAX_BYTES and n_in_part > 0:
            f.close()
            written.append(part)
            part += 1
            f, w = _open(part)
            cur = header_bytes
            n_in_part = 0
        w.writerow(r)
        cur += rb
        n_in_part += 1
    f.close()
    written.append(part)

    funds = len(set(r["代碼"] for r in rows))
    print("=" * 60)
    for p in written:
        path = "data/offshore_nav_{:03d}.csv".format(p)
        print("  {} → {:.1f} MB".format(path, os.path.getsize(path) / 1024 / 1024))
    print("合併：{} 檔基金、{:,} 筆、切成 {} 個檔（每檔<80MB）".format(
        funds, len(rows), len(written)))
    print("=" * 60)
    if funds < 100:
        print("❌ 只有 {} 檔，疑似大量失敗，不 commit".format(funds))
        sys.exit(1)


if __name__ == "__main__":
    main()
