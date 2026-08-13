# -*- coding: utf-8 -*-
"""合併境外分段檔 → 按「年」切檔，超過上限的年再切子檔（檔名帶年份供 app 省記憶體篩選）。
輸出 data/offshore_nav_YYYY_NN.csv；app 依年份 glob，只載選定年份。
"""
import csv
import glob
import io
import os
import sys
from collections import defaultdict

COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域"]
MAX_BYTES = 80 * 1024 * 1024   # 每檔上限 80MB


def _row_bytes(r):
    buf = io.StringIO()
    csv.writer(buf).writerow([r[c] for c in COLS])
    return len(buf.getvalue().encode("utf-8"))


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
                by_year[d[:4]].append({c: r.get(c, "") for c in COLS})

    os.makedirs("data", exist_ok=True)
    for old in glob.glob("data/offshore_nav_*.csv"):
        os.remove(old)
    if os.path.exists("data/offshore_nav.csv"):
        os.remove("data/offshore_nav.csv")

    header_bytes = len((",".join(COLS) + "\r\n").encode("utf-8"))
    total = 0
    funds = set()
    written = []
    for year, rows in sorted(by_year.items()):
        part = 1
        f = None
        w = None
        cur = 0
        n_in = 0

        def _open(y, p):
            fn = "data/offshore_nav_{}_{:02d}.csv".format(y, p)
            ff = open(fn, "w", encoding="utf-8-sig", newline="")
            ww = csv.DictWriter(ff, fieldnames=COLS)
            ww.writeheader()
            return fn, ff, ww

        fn, f, w = _open(year, part)
        cur = header_bytes
        for r in rows:
            rb = _row_bytes(r)
            if cur + rb > MAX_BYTES and n_in > 0:
                f.close(); written.append(fn)
                part += 1
                fn, f, w = _open(year, part)
                cur = header_bytes; n_in = 0
            w.writerow(r); cur += rb; n_in += 1
            funds.add(r["代碼"]); total += 1
        f.close(); written.append(fn)

    print("=" * 60)
    for fn in written:
        print("  {} → {:.1f} MB".format(fn, os.path.getsize(fn) / 1024 / 1024))
    print("合併：{} 檔基金、{:,} 筆、{} 個年度(子)檔".format(len(funds), total, len(written)))

    # ── 絕對比對：cnyes 官方清單 N 檔 vs 實際建進庫 M 檔 → coverage_offshore.json ──
    import json
    import datetime as _dt
    uni_files = glob.glob("_segs/*/offshore_universe.csv") + glob.glob("data/offshore_universe.csv")
    universe = {}
    for uf in uni_files:
        try:
            with open(uf, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    c = (r.get("代碼") or "").strip()
                    if c:
                        universe[c] = r.get("名稱", "")
            break
        except Exception:
            continue
    cov = {
        "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d"),
        "source": "cnyes清單 vs yfinance歷史",
        "universe_count": len(universe),          # 官方應有(cnyes 全境外類股)
        "built_count": len(funds),                # 實際建進庫(有yfinance歷史)
        "with_history_rate": round(len(funds) / len(universe), 4) if universe else None,
    }
    if universe:
        missing = [c for c in universe if c not in funds]
        cov["missing_count"] = len(missing)
        cov["missing_sample"] = [{"代碼": c, "名稱": universe[c]} for c in missing[:50]]
        print("覆蓋比對：官方 {} 檔 / 建庫 {} 檔 / 無歷史 {} 檔 ({:.0%})".format(
            len(universe), len(funds), len(missing), cov["with_history_rate"]))
    else:
        print("（找不到 universe 清單，coverage 只記建庫數）")
    with open("data/coverage_offshore.json", "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=2)
    print("→ data/coverage_offshore.json 已寫入")
    print("=" * 60)
    if len(funds) < 100:
        print("❌ 只有 {} 檔，疑似大量失敗，不 commit".format(len(funds)))
        sys.exit(1)


if __name__ == "__main__":
    main()
