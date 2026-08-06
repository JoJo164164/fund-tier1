# -*- coding: utf-8 -*-
"""境外歷史來源探針（yfinance 可行性驗證）
=====================================================
目的：在寫全市場 backfill 前，先實測「TDCC 的 ISIN → Yahoo 0P 代碼 → yfinance 抓幾年」
      這整條鏈能不能撐起 5 年回測。沙盒無網路，必須在 GitHub Actions 實跑。

流程（每檔）：
  ① 用 ISIN 打 Yahoo 搜尋 API 反查符號(通常是共同基金 0P 代碼)
  ② 用該符號 yfinance.history(period="5y") 實抓，數回傳幾年、幾筆
輸出：逐檔結果 + 彙總(解析率、≥3年比例、≥5年比例、平均年數) → 決定要不要走 yfinance。
"""
import time
import datetime as dt

import requests
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

YH_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# 25 檔知名境外基金（真實 ISIN，取自 TDCC 開放資料；涵蓋美/歐/亞、股/債/產業）
SEED = [
    ("US8801991048", "富蘭克林坦伯頓成長基金A"),
    ("US8801961009", "富蘭克林坦伯頓世界基金A"),
    ("US3534965088", "富蘭克林成長基金A"),
    ("US3534962010", "富蘭克林高科技基金A"),
    ("US3534964099", "富蘭克林公用事業基金A1"),
    ("LU0231203729", "富坦印度基金美元A"),
    ("LU0316494557", "富坦全球核心策略美元A"),
    ("LU0195948665", "富坦美國機會基金美元I"),
    ("LU0195948822", "富坦生技領航基金美元I"),
    ("LU0109392836", "富坦科技基金美元A"),
    ("LU0128522744", "富坦新興國家基金美元A"),
    ("LU0195951024", "富坦大中華基金美元I"),
    ("LU0229945570", "富坦金磚國家基金美元A"),
    ("LU0300736062", "富坦天然資源基金美元A"),
    ("LU0170475312", "富坦全球債券總報酬美元A"),
    ("LU0252652382", "富坦全球債券基金美元A"),
    ("LU0390134368", "富坦吉富世界基金美元A"),
    ("LU0061475181", "天利(盧森堡)北美基金美元"),
    ("LU0061476155", "天利(盧森堡)泛歐洲股票歐元"),
    ("LU0061474614", "天利(盧森堡)新興市場債券美元"),
    ("LU0096353940", "天利(盧森堡)歐洲策略債券歐元"),
    ("IE00BD5M6819", "紐約梅隆美國股票收益美元A累積"),
    ("LU0426895305", "瑞銀(盧森堡)新興市場債券美元"),
    ("LU0464244333", "瑞銀(盧森堡)亞洲靈活債券美元"),
    ("LU0108066076", "瑞銀(盧森堡)歐洲可轉換債券歐元"),
]


def resolve_symbol(isin):
    """ISIN → Yahoo 符號。回 (symbol, quoteType) 或 (None, 原因)。"""
    try:
        r = requests.get(YH_SEARCH, params={"q": isin, "quotesCount": 5},
                         headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None, "search HTTP {}".format(r.status_code)
        quotes = r.json().get("quotes", [])
        if not quotes:
            return None, "無搜尋結果"
        # 優先取共同基金 / 0P 代碼
        for q in quotes:
            sym = q.get("symbol", "")
            if q.get("quoteType") == "MUTUALFUND" or sym.startswith("0P"):
                return sym, q.get("quoteType", "?")
        return quotes[0].get("symbol"), quotes[0].get("quoteType", "?")
    except Exception as e:
        return None, "{}: {}".format(type(e).__name__, str(e)[:40])


def years_of(symbol):
    """yfinance 抓 5 年，回 (年數, 筆數, 最早, 最新) 或 (0,0,錯誤)。"""
    try:
        df = yf.Ticker(symbol).history(period="5y", auto_adjust=True)
        if df is None or len(df) == 0:
            return 0, 0, "empty", ""
        idx = df.index
        yrs = round((idx.max() - idx.min()).days / 365.25, 1)
        return yrs, len(df), str(idx.min().date()), str(idx.max().date())
    except Exception as e:
        return 0, 0, "{}: {}".format(type(e).__name__, str(e)[:40]), ""


def main():
    print("=" * 72)
    print("境外歷史來源探針  yfinance可用:", _HAS_YF, " 測試", len(SEED), "檔")
    print("=" * 72)
    if not _HAS_YF:
        raise SystemExit("yfinance 未安裝（workflow 需 pip install yfinance）")

    resolved = 0
    ge3 = ge5 = 0
    yrs_list = []
    print("{:14} {:14} {:>5} {:>7}  {}".format("ISIN", "→ 符號", "年數", "筆數", "名稱"))
    print("-" * 72)
    for isin, name in SEED:
        sym, info = resolve_symbol(isin)
        if not sym:
            print("{:14} {:14} {:>5} {:>7}  {}  [{}]".format(
                isin, "✗未對到", "-", "-", name, info))
            time.sleep(0.6)
            continue
        resolved += 1
        yrs, n, d0, d1 = years_of(sym)
        yrs_list.append(yrs)
        if yrs >= 3:
            ge3 += 1
        if yrs >= 5:
            ge5 += 1
        flag = "✓" if yrs >= 3 else "△"
        print("{:14} {:14} {:>5} {:>7}  {} {}  ({}~{})".format(
            isin, sym[:14], yrs, n, flag, name, d0, d1))
        time.sleep(0.8)

    n = len(SEED)
    avg = round(sum(yrs_list) / len(yrs_list), 1) if yrs_list else 0
    print("=" * 72)
    print("彙總：{}/{} 對到符號({:.0%})；有資料者 ≥3年 {} 檔、≥5年 {} 檔；平均 {} 年".format(
        resolved, n, resolved / n, ge3, ge5, avg))
    print("判讀：≥3年比例高(如 >70%) → yfinance 可撐回測，寫全市場 backfill；"
          "偏低 → 改晨星或別條源。")
    print("=" * 72)


if __name__ == "__main__":
    main()
