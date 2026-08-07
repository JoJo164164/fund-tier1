# -*- coding: utf-8 -*-
"""cnyes 探針 v3（決定性）：cnyes 0P 代碼 → yfinance 的真實涵蓋率
=====================================================
發現：cnyes metadata 有 fundClassId(=Yahoo 0P) + isin + sitca(空=境外)。
驗證：① 清單能否直接帶 fundClassId/isin/sitca ② sitca 是否=境內外篩法
     ③ 用 cnyes 給的 0P 丟 yfinance，真實涵蓋率與歷史年數(定案關鍵)
"""
import json
import time
import requests
try:
    import yfinance as yf
    HAS = True
except ImportError:
    HAS = False

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://fund.cnyes.com", "Referer": "https://fund.cnyes.com/"}
BASE = "https://fund.api.cnyes.com/fund/api"
LIST = BASE + "/v2/search/fund"


def meta(code):
    """單檔 metadata：抓 fundClassId(0P)/isin/sitca。"""
    try:
        r = requests.get(BASE + "/v1/funds/{}/nav".format(code), headers=H, timeout=20)
        it = r.json().get("items", {})
        return it if isinstance(it, dict) else {}
    except Exception:
        return {}


def yfyears(sym):
    try:
        df = yf.Ticker(sym).history(period="max", auto_adjust=True)
        if df is None or len(df) == 0:
            return 0, 0
        idx = df.index
        return round((idx.max() - idx.min()).days / 365.25, 1), len(df)
    except Exception:
        return 0, 0


def main():
    print("=" * 72)
    print("① 清單能否直接帶 fundClassId/isin/sitca？")
    print("=" * 72)
    fields = ("cnyesId,fundYesId,fundClassId,isin,sitca,displayNameLocal,"
              "investmentArea,classCurrencyLocal,inceptionDate,forSale")
    r = requests.get(LIST, params={"order": "priceDate", "sort": "desc", "page": 1,
                     "institutional": 0, "fields": fields}, headers=H, timeout=30)
    data = (r.json().get("items", {}) or {}).get("data", []) or []
    list_has_0p = any(d.get("fundClassId") for d in data)
    for d in data[:3]:
        print("  ", json.dumps(d, ensure_ascii=False))
    print("→ 清單直接帶 fundClassId：", list_has_0p)

    print("\n" + "=" * 72)
    print("②③ 抽樣 25 檔：classify(sitca) + cnyes 0P → yfinance 年數  yf可用:", HAS)
    print("=" * 72)
    # 抽跨頁樣本
    sample = []
    for pg in [1, 100, 250, 400]:
        rr = requests.get(LIST, params={"order": "priceDate", "sort": "desc", "page": pg,
                          "institutional": 0, "fields": fields}, headers=H, timeout=30)
        sample += (rr.json().get("items", {}) or {}).get("data", []) or []
        time.sleep(0.3)
    sample = sample[:25]

    offshore = onshore = with0p = usable = 0
    yrs_list = []
    print("{:10} {:8} {:14} {:>5} {:>6}  {}".format("cnyesId", "境內外", "0P碼", "年數", "筆數", "名稱"))
    print("-" * 72)
    for d in sample:
        code = (d.get("cnyesId") or "").replace(",", "")
        fc = d.get("fundClassId")
        sitca = d.get("sitca")
        isin = d.get("isin")
        # 清單沒帶就補抓 metadata
        if fc is None and sitca is None and isin is None:
            m = meta(code)
            fc = m.get("fundClassId"); sitca = m.get("sitca"); isin = m.get("isin")
            time.sleep(0.3)
        is_off = (not sitca)   # sitca 空 → 境外
        if is_off:
            offshore += 1
        else:
            onshore += 1
        if fc:
            with0p += 1
        yy = nn = 0
        if is_off and fc and HAS:
            yy, nn = yfyears(fc)
            if nn > 1:
                usable += 1; yrs_list.append(yy)
            time.sleep(0.7)
        tag = "境外" if is_off else "境內(sitca=%s)" % sitca
        print("{:10} {:8} {:14} {:>5} {:>6}  {}".format(
            code, "境外" if is_off else "境內", (fc or "-")[:14], yy, nn,
            (d.get("displayNameLocal") or "")[:24]))

    N = len(sample)
    avg = round(sum(yrs_list) / len(yrs_list), 1) if yrs_list else 0
    print("=" * 72)
    print("樣本 {}：境外 {}、境內 {}；有0P碼 {}；境外中 yfinance 可用 {}、平均 {} 年".format(
        N, offshore, onshore, with0p, usable, avg))
    print("定案判讀：境外可用率 = usable / offshore。>70% → cnyes清單0P→yfinance 全市場建庫")
    print("=" * 72)


if __name__ == "__main__":
    main()
