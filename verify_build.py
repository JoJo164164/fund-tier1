#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建庫驗證（合併後、commit 前執行）。

任何一條斷言不過就 sys.exit(1) → 整個 workflow run 失敗 → 不准 commit 壞資料。
專門堵住「分段檔互相覆蓋 / 某段 job 掉了 / 資料稀疏 / 單日抓取斷掉」等悄悄回 partial 的路。

只用標準函式庫，merge job 不必 pip install。

環境變數（由 workflow 餵入，對齊 build 參數）：
  SEG_TOTAL       平行分段數，需與 workflow 的 parallel 一致（預設 4）
  MAX_SPAN_DAYS   鐵律16 的跨度上限，需與 app.py 的 MAX_SPAN_DAYS 一致（預設 25）
  MIN_COVERAGE    日期覆蓋率下限（實得日期 / 區間營業日）（預設 0.80）
  DAY_HARD_RATIO  單日筆數 < 中位數×此值 → 判「抓取斷掉」硬性 fail（預設 0.05）
  DAY_WARN_RATIO  單日筆數 < 中位數×此值 → 提示（多為境外市場休市，不 fail）（預設 0.80）

★斷言4設計說明★：境內外混合母體中，遇美股/他國休市日，投資該市場的境外基金
  當天無淨值可算 → 該日筆數合理掉到 ~40%（實測：2026-01-19 MLK Day、
  2026-07-03 美國國慶，皆只剩 ~40% 且缺口集中在美國區基金）。這是合法資料，
  不可 fail。真正的「抓取斷掉」會趨近 0，故只在 <5% 中位數時硬擋。
"""
import os
import sys
import csv
import glob
import datetime as dt
from collections import Counter

SEG_TOTAL = int(os.environ.get("SEG_TOTAL", "4"))
MAX_SPAN_DAYS = int(os.environ.get("MAX_SPAN_DAYS", "25"))
MIN_COVERAGE = float(os.environ.get("MIN_COVERAGE", "0.80"))
DAY_HARD_RATIO = float(os.environ.get("DAY_HARD_RATIO", "0.05"))
DAY_WARN_RATIO = float(os.environ.get("DAY_WARN_RATIO", "0.80"))
DATA_DIR = "data"


def fail(msg):
    print("❌ 驗證失敗：" + msg)
    sys.exit(1)


def main():
    print("=" * 60)
    print("建庫驗證  SEG_TOTAL={} | MAX_SPAN_DAYS={} | MIN_COVERAGE={}".format(
        SEG_TOTAL, MAX_SPAN_DAYS, MIN_COVERAGE))
    print("=" * 60)

    # 依模式決定要驗哪些檔：多段驗 _segNN 檔；單段驗無後綴分年檔
    if SEG_TOTAL > 1:
        files = sorted(glob.glob(os.path.join(DATA_DIR, "sitca_nav_*_seg*.csv")))
    else:
        files = [f for f in sorted(glob.glob(os.path.join(DATA_DIR, "sitca_nav_*.csv")))
                 if "_seg" not in os.path.basename(f)]
    if not files:
        fail("data/ 找不到任何建庫 CSV（sitca_nav_*）。建庫可能整個沒產出。")
    print("找到 {} 個檔案：".format(len(files)))
    for f in files:
        print("  " + os.path.basename(f))

    # 讀出所有日期與每日筆數
    dates = set()
    per_date = Counter()
    for path in files:
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                header = fh.readline().rstrip("\n").split(",")
                if "日期" not in header:
                    continue
                di = header.index("日期")
                for line in fh:
                    parts = line.rstrip("\n").split(",")
                    if len(parts) > di and len(parts[di]) == 10:
                        d = parts[di]
                        dates.add(d)
                        per_date[d] += 1
        except Exception as e:
            fail("讀取 {} 失敗：{}".format(path, e))

    if not dates:
        fail("所有 CSV 都沒有有效日期資料。")

    # ── 斷言 1：SEG_TOTAL 段全到齊（防某段 job 掉 artifact → 悄悄回 partial）──
    if SEG_TOTAL > 1:
        seg_ids = set()
        for f in files:
            base = os.path.basename(f)
            try:
                seg_ids.add(base.split("_seg")[1][:2])
            except IndexError:
                pass
        if len(seg_ids) != SEG_TOTAL:
            fail("只找到 {}/{} 段（seg={}）→ 有段沒產出或 artifact 掉了。".format(
                len(seg_ids), SEG_TOTAL, sorted(seg_ids)))
        print("✅ 斷言1：{} 段全到齊 {}".format(SEG_TOTAL, sorted(seg_ids)))
    else:
        print("• 斷言1：單段模式，略過分段檢查")

    # ── 斷言 2：日期覆蓋率（防覆蓋復發 → 只剩 1/N 日期）──
    sd = sorted(dt.date.fromisoformat(x) for x in dates)
    lo, hi = sd[0], sd[-1]
    biz = 0
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            biz += 1
        d += dt.timedelta(days=1)
    coverage = len(dates) / biz if biz else 0.0
    print("  區間 {} ~ {}：營業日 {} 個、實得日期 {} 個、覆蓋率 {:.1%}".format(
        lo, hi, biz, len(dates), coverage))
    if coverage < MIN_COVERAGE:
        fail("日期覆蓋率 {:.1%} < {:.0%} → 疑似分段覆蓋/大量缺日（1/N 徵兆）。".format(
            coverage, MIN_COVERAGE))
    print("✅ 斷言2：日期覆蓋率 {:.1%} ≥ {:.0%}".format(coverage, MIN_COVERAGE))

    # ── 斷言 3：最近 10 筆曆日跨度 ≤ MAX_SPAN_DAYS（鐵律16；直接對應「資料稀疏」）──
    if len(sd) < 10:
        fail("最新資料不足 10 筆（僅 {} 個日期）→ 無法算滾動視窗。".format(len(sd)))
    span10 = (sd[-1] - sd[-10]).days
    if span10 > MAX_SPAN_DAYS:
        fail("最近10筆跨度 {} 天 > {} 天 → 近端資料仍稀疏，鐵律16 會判無效、勝率算不出。".format(
            span10, MAX_SPAN_DAYS))
    print("✅ 斷言3：最近10筆跨度 {} 天 ≤ {} 天".format(span10, MAX_SPAN_DAYS))

    # ── 斷言 4：單日筆數健檢（境外休市日的合理稀疏放行、抓取斷掉才擋）──
    #   硬性 fail：<中位數×DAY_HARD_RATIO（趨近0，只有抓取真的斷掉才會這麼低）
    #   提示 warn：中位數×DAY_HARD_RATIO ~ ×DAY_WARN_RATIO（多為美股/他國休市日）
    counts = sorted(per_date.values())
    med = counts[len(counts) // 2]
    hard_floor = med * DAY_HARD_RATIO
    warn_floor = med * DAY_WARN_RATIO
    broken = {k: v for k, v in sorted(per_date.items()) if v < hard_floor}
    thin = {k: v for k, v in sorted(per_date.items()) if hard_floor <= v < warn_floor}
    if broken:
        preview = dict(list(broken.items())[:10])
        fail("單日筆數趨近零（中位數 {}，硬底 {:.0f}）→ 抓取斷掉：{}{}".format(
            med, hard_floor, preview, " …等" if len(broken) > 10 else ""))
    if thin:
        preview = dict(list(thin.items())[:15])
        print("⚠️  提示：{} 個日期筆數偏低（中位數 {} 的 {:.0%}~{:.0%} 之間），"
              "多為境外市場休市日、屬合法稀疏，未擋：".format(
                  len(thin), med, DAY_HARD_RATIO, DAY_WARN_RATIO))
        print("     " + str(preview) + (" …等" if len(thin) > 15 else ""))
    print("✅ 斷言4：無單日趨近零（抓取未斷）；偏低日 {} 個（境外休市，放行）".format(len(thin)))

    print("=" * 60)
    print("✅ 全數通過：{} 段、{} 個日期、覆蓋率 {:.1%}、最近10筆跨度 {} 天".format(
        SEG_TOTAL if SEG_TOTAL > 1 else 1, len(dates), coverage, span10))
    print("=" * 60)


if __name__ == "__main__":
    main()
