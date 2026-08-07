# -*- coding: utf-8 -*-
"""cnyes 探針 v2：找「一次給全部淨值」的端點 + 確認 8374 是否全境外
=====================================================
關鍵：table 端點鎖10筆/頁→全市場逐頁翻不可行。
     圖表通常一次載完整序列→找出那個端點，一檔一請求，全市場才做得起來。
"""
import json
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://fund.cnyes.com", "Referer": "https://fund.cnyes.com/"}
BASE = "https://fund.api.cnyes.com/fund/api"
CODE = "B4AY7PY"   # total 應=1233


def try_url(label, url, params=None):
    try:
        r = requests.get(url, params=params or {}, headers=H, timeout=30)
        ct = r.headers.get("content-type", "")
        n = "-"
        try:
            j = r.json()
            # 嘗試數出點數
            for path in [("items", "data"), ("data",), ("items",)]:
                cur = j
                ok = True
                for k in path:
                    if isinstance(cur, dict) and k in cur:
                        cur = cur[k]
                    else:
                        ok = False; break
                if ok and isinstance(cur, list):
                    n = len(cur); break
            preview = json.dumps(j, ensure_ascii=False)[:160]
        except Exception:
            preview = r.text[:160]
        print("[{}] HTTP {} | 點數={} | {}\n     {}\n".format(
            label, r.status_code, n, ct, preview))
    except Exception as e:
        print("[{}] 例外 {}: {}\n".format(label, type(e).__name__, str(e)[:80]))


def main():
    print("=" * 72)
    print("A. 找『一次給全部』的淨值端點（目標：一次回 ~1233 筆，非 10 筆）")
    print("=" * 72)
    nav = BASE + "/v1/funds/{}/nav".format(CODE)
    try_url("format=chart", nav, {"format": "chart"})
    try_url("format=daily", nav, {"format": "daily"})
    try_url("format=json", nav, {"format": "json"})
    try_url("no format", nav, {})
    try_url("chart&range=all", nav, {"format": "chart", "range": "all"})
    # 可能的圖表專用路徑
    for p in ["/v1/funds/{}/nav-chart", "/v1/funds/{}/chart",
              "/v1/funds/{}/performance", "/v2/funds/{}/nav", "/v1/funds/{}/nav-history"]:
        u = BASE + p.format(CODE)
        try_url("path " + p.split("funds/{}")[1], u, {})

    print("=" * 72)
    print("B. 確認 8374 檔是不是全境外（抽 3 頁看基金品牌）")
    print("=" * 72)
    LIST = BASE + "/v2/search/fund"
    fields = "cnyesId,fundYesId,displayNameLocal,investmentArea,inceptionDate,forSale"
    for pg in [1, 200, 419]:
        try:
            r = requests.get(LIST, params={"order": "priceDate", "sort": "desc",
                             "page": pg, "institutional": 0, "fields": fields},
                             headers=H, timeout=30)
            data = (r.json().get("items", {}) or {}).get("data", []) or []
            names = [d.get("displayNameLocal", "")[:22] for d in data[:6]]
            print("第{}頁前6檔：".format(pg))
            for nm in names:
                print("   ", nm)
        except Exception as e:
            print("第{}頁失敗：{}".format(pg, e))
    # 有沒有明顯的台灣境內基金(元大/國泰/富邦/群益…投信)
    print("\n若上面全是外資品牌(景順/施羅德/富坦/貝萊德…)→ 8374=境外，免篩選")
    print("若混到 元大/國泰/富邦/群益/統一…台灣投信 → 需再找境內外篩法")


if __name__ == "__main__":
    main()
