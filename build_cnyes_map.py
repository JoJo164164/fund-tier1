#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建立「SITCA 代碼 → cnyes 明細代碼(fundYesId)」對映表。
cnyes 明細頁網址 = fund.cnyes.com/detail/{名稱}/{fundYesId}。
- 境內基金：我們庫代碼是 SITCA 代碼，≠ cnyes 代碼 → 需要本對映。
- 境外基金：我們庫代碼已是 cnyesId 去逗號 = fundYesId → 不需對映(app 直接用原碼)。
輸出 data/cnyes_map.json：{ "sitca": {SITCA碼: fundYesId}, "isin": {ISIN: fundYesId},
  "updated": "YYYY-MM-DD" }。app 由 Release 讀取後 O(1) 查表。
"""
import json
import os
import sys
import time
import requests

API = "https://fund.api.cnyes.com/fund/api/v2/search/fund"
FIELDS = "cnyesId,fundYesId,sitca,isin,displayNameLocal"
HEADERS = {"User-Agent": "Mozilla/5.0 (fund-tier1 cnyes-map builder)"}
OUT = "data/cnyes_map.json"


def _fundyes_id(rec):
    """取 fundYesId；沒有就用 cnyesId 去逗號。"""
    fy = str(rec.get("fundYesId") or "").strip()
    if fy and fy.lower() != "none":
        return fy
    cid = str(rec.get("cnyesId") or "").replace(",", "").strip()
    return cid if cid and cid.lower() != "none" else ""


def main():
    os.makedirs("data", exist_ok=True)
    by_sitca, by_isin = {}, {}
    page, last_page = 1, None
    total_seen = 0
    sess = requests.Session()
    sess.headers.update(HEADERS)
    while True:
        params = {"page": page, "fields": FIELDS, "institutional": 0}
        try:
            r = sess.get(API, params=params, timeout=30)
            j = r.json()
        except Exception as e:
            print("page {} 失敗: {}".format(page, e), flush=True)
            time.sleep(2.0)
            if page > 1:      # 中途失敗：已收集的仍寫出
                break
            sys.exit(1)
        items = j.get("items", {})
        data = items.get("data", []) or []
        if last_page is None:
            last_page = int(items.get("last_page", 1) or 1)
            print("cnyes 總頁數: {}，總筆數: {}".format(last_page, items.get("total")), flush=True)
        for rec in data:
            total_seen += 1
            fy = _fundyes_id(rec)
            if not fy:
                continue
            sitca = str(rec.get("sitca") or "").strip()
            if sitca and sitca.lower() != "none":
                by_sitca[sitca] = fy
            isin = str(rec.get("isin") or "").strip()
            if isin and isin.lower() != "none":
                by_isin.setdefault(isin, fy)
        if page % 25 == 0 or page == last_page:
            print("  已處理 {}/{} 頁，SITCA對映 {} 筆".format(page, last_page, len(by_sitca)), flush=True)
        if page >= last_page or not data:
            break
        page += 1
        time.sleep(0.15)

    out = {"sitca": by_sitca, "isin": by_isin,
           "updated": time.strftime("%Y-%m-%d"),
           "count_sitca": len(by_sitca), "count_isin": len(by_isin),
           "total_seen": total_seen}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("✅ 寫出 {}：SITCA {} 筆 / ISIN {} 筆（掃過 {} 檔）".format(
        OUT, len(by_sitca), len(by_isin), total_seen), flush=True)
    if len(by_sitca) < 500:
        print("⚠️ SITCA 對映偏少，請檢查 cnyes API 是否改版", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
