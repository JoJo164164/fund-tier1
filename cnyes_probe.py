# -*- coding: utf-8 -*-
"""cnyes 全市場建庫可行性探針
=====================================================
建全市場前必須先確認兩件事（決定可行性與正確性）：
  A. 淨值 API 能不能「一次要一大包」→ 決定是幾十分鐘還是幾十小時
  B. 清單 API 一筆基金完整長怎樣、總數多少、怎麼只篩「境外」
只印報告，不寫庫。
"""
import json
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://fund.cnyes.com", "Referer": "https://fund.cnyes.com/"}
NAV = "https://fund.api.cnyes.com/fund/api/v1/funds/{code}/nav"
LIST = "https://fund.api.cnyes.com/fund/api/v2/search/fund"


def sep(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


def main():
    # ── A. 淨值 API per_page 吃不吃大包 ──
    sep("A. 淨值 API 每頁能不能加大（B4AY7PY，total 應=1233）")
    for pp in [10, 500, 3000]:
        try:
            r = requests.get(NAV.format(code="B4AY7PY"),
                             params={"format": "table", "page": 1,
                                     "per_page": pp, "limit": pp}, headers=H, timeout=30)
            it = r.json().get("items", {}) or {}
            print("要 per_page={:>4} → 實回 {:>4} 筆 | per_page={} last_page={} total={}".format(
                pp, len(it.get("data", []) or []), it.get("per_page"),
                it.get("last_page"), it.get("total")))
        except Exception as e:
            print("per_page={} 失敗：{}".format(pp, e))

    # ── B. 清單 API：總數、每頁加大、單筆完整欄位 ──
    sep("B. 清單 API：總數 / 每頁加大 / 單筆完整欄位")
    fields = ("cnyesId,fundYesId,displayNameLocal,classCurrencyLocal,investmentArea,"
              "inceptionDate,forSale,saleStatus,categoryAbbr,isMainland,area,fundType,"
              "isOffshore,offshore,ISINCode,isinCode,attribute,attributes,region")
    for pp in [20, 2000]:
        try:
            r = requests.get(LIST, params={"order": "priceDate", "sort": "desc",
                             "page": 1, "per_page": pp, "institutional": 0,
                             "fields": fields}, headers=H, timeout=30)
            it = r.json().get("items", {}) or {}
            print("要 per_page={:>4} → 實回 {:>4} 筆 | per_page={} last_page={} total={}".format(
                pp, len(it.get("data", []) or []), it.get("per_page"),
                it.get("last_page"), it.get("total")))
        except Exception as e:
            print("list per_page={} 失敗：{}".format(pp, e))

    # 印前 3 檔完整欄位 → 看有沒有「境內外」分類欄位
    try:
        r = requests.get(LIST, params={"order": "priceDate", "sort": "desc",
                         "page": 1, "per_page": 3, "institutional": 0,
                         "fields": fields}, headers=H, timeout=30)
        data = (r.json().get("items", {}) or {}).get("data", []) or []
        print("\n前 3 檔完整欄位（找有沒有 境內外/offshore/area 之類）：")
        for d in data:
            print(json.dumps(d, ensure_ascii=False))
    except Exception as e:
        print("印欄位失敗：", e)


if __name__ == "__main__":
    main()
