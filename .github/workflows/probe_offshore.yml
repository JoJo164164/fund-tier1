# -*- coding: utf-8 -*-
"""境外歷史來源探針 v2（yfinance 可行性 · 改進反查）
=====================================================
v1 教訓：ISIN 反查常挑到德國交易所次級掛牌(.F/.MU/.SG/.HM)→0~1筆垃圾；
        該嚴格優先 Yahoo 的 0P 共同基金代碼(有完整NAV歷史)。
v2 反查順序：① 0P 代碼 ② 美國乾淨ticker(無交易所後綴) ③ 其他非次級掛牌符號。
   抓 period="max" 取最大深度；失敗時印候選符號，判斷是「漏挑」還是「Yahoo無此檔」。
"""
import time
import requests
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

YH_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
# 不信任的交易所次級掛牌後綴（這些沒NAV序列）
BAD_SUFFIX = (".F", ".MU", ".SG", ".HM", ".DE", ".BE", ".DU", ".HA", ".L",
              ".VI", ".SW", ".MI", ".PA", ".AS", ".BR", ".MA", ".IR")

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


def search_candidates(isin):
    try:
        r = requests.get(YH_SEARCH, params={"q": isin, "quotesCount": 15},
                         headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return [], "search HTTP {}".format(r.status_code)
        return r.json().get("quotes", []), None
    except Exception as e:
        return [], "{}: {}".format(type(e).__name__, str(e)[:40])


def pick_symbol(quotes):
    syms = [q.get("symbol", "") for q in quotes if q.get("symbol")]
    # ① 0P 代碼最優先（Yahoo 共同基金 NAV 序列）
    for s in syms:
        if s.startswith("0P"):
            return s, "0P"
    # ② 美國乾淨 ticker（MUTUALFUND 且無交易所後綴）
    for q in quotes:
        s = q.get("symbol", "")
        if q.get("quoteType") == "MUTUALFUND" and "." not in s and s:
            return s, "US基金"
    # ③ 任何非次級掛牌後綴的符號
    for s in syms:
        if s and not any(s.endswith(suf) for suf in BAD_SUFFIX):
            return s, "其他"
    return None, "只有交易所次級掛牌"


def years_of(symbol):
    try:
        df = yf.Ticker(symbol).history(period="max", auto_adjust=True)
        if df is None or len(df) == 0:
            return 0, 0, "empty", ""
        idx = df.index
        yrs = round((idx.max() - idx.min()).days / 365.25, 1)
        return yrs, len(df), str(idx.min().date()), str(idx.max().date())
    except Exception as e:
        return 0, 0, "{}: {}".format(type(e).__name__, str(e)[:30]), ""


def main():
    print("=" * 76)
    print("境外歷史探針 v2  yfinance:", _HAS_YF, " 測", len(SEED), "檔（改進反查+抓max）")
    print("=" * 76)
    if not _HAS_YF:
        raise SystemExit("yfinance 未安裝")

    usable = ge3 = ge5 = 0
    yrs_list = []
    print("{:14} {:12} {:>6} {:>6} {:>5}  {}".format("ISIN", "符號", "來源", "年數", "筆數", "名稱"))
    print("-" * 76)
    for isin, name in SEED:
        quotes, err = search_candidates(isin)
        if err:
            print("{:14} {:12} {:>6} {:>6} {:>5}  {}  [{}]".format(isin, "-", "-", "-", "-", name, err))
            time.sleep(0.6); continue
        sym, how = pick_symbol(quotes)
        if not sym:
            cand = ",".join(q.get("symbol", "") for q in quotes[:5]) or "無"
            print("{:14} {:12} {:>6} {:>6} {:>5}  {}  [候選:{}]".format(
                isin, "✗", how, "-", "-", name, cand))
            time.sleep(0.7); continue
        yrs, n, d0, d1 = years_of(sym)
        if n > 1:
            usable += 1; yrs_list.append(yrs)
            if yrs >= 3: ge3 += 1
            if yrs >= 5: ge5 += 1
        flag = "✓" if yrs >= 3 else ("·" if n > 1 else "✗")
        print("{:14} {:12} {:>6} {:>6} {:>5}  {} {} ({}~{})".format(
            isin, sym[:12], how, yrs, n, flag, name, d0, d1))
        time.sleep(0.9)

    N = len(SEED)
    avg = round(sum(yrs_list) / len(yrs_list), 1) if yrs_list else 0
    print("=" * 76)
    print("彙總：可用(>1筆) {}/{} ({:.0%})；其中 ≥3年 {} 檔、≥5年 {} 檔；可用檔平均 {} 年".format(
        usable, N, usable / N, ge3, ge5, avg))
    print("判讀：可用率 >70% 且平均≥4年 → 寫全市場 backfill；仍偏低 → 看失敗候選決定補救")
    print("=" * 76)


if __name__ == "__main__":
    main()
