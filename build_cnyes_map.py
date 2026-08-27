#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建立「我們庫代碼 → cnyes 明細代碼(fundYesId)」多鍵對映表。
cnyes 明細頁 = fund.cnyes.com/detail/{名稱}/{fundYesId}。
- 境內：我們代碼是 SITCA 代碼 → 用 by_sitca 對映。
- 境外：我們代碼 = cnyesId 去逗號 → 用 by_code 對映到現行 fundYesId
        （多數 fundYesId=cnyesId去逗號，少數不同如 B3ja88k→B200268，此表修正之）。
- 備援：by_isin（全球唯一碼）、by_name（正規化基金名）。
輸出 data/cnyes_map.json。app 由 Release 讀取後 O(1) 查表。
"""
import json
import os
import re
import sys
import time
import requests

API = "https://fund.api.cnyes.com/fund/api/v2/search/fund"
FIELDS = "cnyesId,fundYesId,isin,sitca,displayNameLocal"
HEADERS = {"User-Agent": "Mozilla/5.0 (fund-tier1 cnyes-map builder)"}
OUT = "data/cnyes_map.json"


def _norm_name(s):
    s = str(s or "")
    s = s.replace("（", "(").replace("）", ")").replace("　", "")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _fundyes_id(rec):
    fy = str(rec.get("fundYesId") or "").strip()
    if fy and fy.lower() != "none":
        return fy
    cid = str(rec.get("cnyesId") or "").replace(",", "").strip()
    return cid if cid and cid.lower() != "none" else ""


def main():
    os.makedirs("data", exist_ok=True)
    by_sitca, by_code, by_isin, by_name = {}, {}, {}, {}
    page, last_page, total_seen = 1, None, 0
    sess = requests.Session()
    sess.headers.update(HEADERS)
    while True:
        try:
            r = sess.get(API, params={"page": page, "fields": FIELDS,
                                      "institutional": 0}, timeout=30)
            j = r.json()
        except Exception as e:
            print("page {} 失敗: {}".format(page, e), flush=True)
            time.sleep(2.0)
            if page > 1:
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
            cid = str(rec.get("cnyesId") or "").replace(",", "").strip()
            if cid and cid.lower() != "none":
                by_code.setdefault(cid, fy)
            isin = str(rec.get("isin") or "").strip().upper()
            if isin and isin.lower() != "none":
                by_isin.setdefault(isin, fy)
            nm = _norm_name(rec.get("displayNameLocal"))
            if nm:
                by_name.setdefault(nm, fy)
        if page % 25 == 0 or page == last_page:
            print("  已處理 {}/{} 頁 | sitca {} / code {} / isin {} / name {}".format(
                page, last_page, len(by_sitca), len(by_code), len(by_isin), len(by_name)), flush=True)
        if page >= last_page or not data:
            break
        page += 1
        time.sleep(0.15)

    out = {"sitca": by_sitca, "code": by_code, "isin": by_isin, "name": by_name,
           "updated": time.strftime("%Y-%m-%d"),
           "counts": {"sitca": len(by_sitca), "code": len(by_code),
                      "isin": len(by_isin), "name": len(by_name), "seen": total_seen}}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("✅ 寫出 {}：sitca {} / code {} / isin {} / name {}（掃過 {} 檔）".format(
        OUT, len(by_sitca), len(by_code), len(by_isin), len(by_name), total_seen), flush=True)
    if len(by_code) < 3000:
        print("⚠️ code 對映偏少，請檢查 cnyes API 是否改版", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
