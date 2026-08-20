# -*- coding: utf-8 -*-
"""境外基金淨值建庫（cnyes 清單 → yfinance 長歷史 backfill）
=====================================================
定案架構（免費源實測最佳解）：
  清單源：cnyes v2/search/fund → 全市場基金，含 fundClassId(=Yahoo 0P)、isin、
          sitca(空=境外)、名稱、投資區域、幣別。用 sitca=="" 篩境外。
  歷史源：yfinance 抓 fundClassId(0P) 的 max 歷史。非美元類股 0P 裸碼常無資料，
          故「先試裸 0P、再試 .F/.SG/.DE/.MU 等交易所後綴」救援，最大化涵蓋率。
輸出：data/offshore_nav.csv（代碼,日期,淨值,幣別,名稱,來源,資產類型,投資區域）
      代碼=cnyesId(去逗號)；日期 ISO 對齊境內。

模式（環境變數 OFFSHORE_MODE）：
  enumerate → 只抓 cnyes 全境外清單，寫 data/offshore_universe.csv（prepare 用）
  fetch     → 讀 universe，做本段(SEGMENT_INDEX)的 yfinance backfill，寫 seg 檔
分段：SEGMENT_TOTAL / SEGMENT_INDEX（比照境內，按基金 index 間隔切）。
"""
import os
import csv
import time

import requests
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

DATA_DIR = "data"
UNIVERSE_CSV = os.path.join(DATA_DIR, "offshore_universe.csv")
NAV_COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "來源", "資產類型", "投資區域", "發行", "系列"]
UNI_COLS = ["代碼", "0P", "isin", "名稱", "幣別", "投資區域", "資產類型", "發行", "系列"]

LIST = "https://fund.api.cnyes.com/fund/api/v2/search/fund"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://fund.cnyes.com", "Referer": "https://fund.cnyes.com/"}
FIELDS = ("cnyesId,fundClassId,isin,sitca,displayNameLocal,investmentArea,"
          "classCurrencyLocal,categoryAbbr")

MODE = os.environ.get("OFFSHORE_MODE", "fetch")
SEG_TOTAL = int(os.environ.get("SEGMENT_TOTAL", "1"))
SEG_INDEX = int(os.environ.get("SEGMENT_INDEX", "0"))
SLEEP = float(os.environ.get("OFFSHORE_SLEEP", "0.5"))
SUFFIXES = ["", ".F", ".SG", ".DE", ".MU", ".L", ".SW"]


def _seg_name(base):
    return base if SEG_TOTAL <= 1 else base.replace(".csv", "_seg{:02d}.csv".format(SEG_INDEX))


def classify_asset(name, cat):
    s = (name or "") + (cat or "")
    for k, v in [("貨幣", "貨幣市場"), ("債", "債券型"), ("REIT", "股票型"),
                 ("房地產", "股票型"), ("股票", "股票型"), ("平衡", "平衡型"),
                 ("多重資產", "平衡型"), ("組合", "組合型")]:
        if k in s:
            return v
    return "未分類"



# ── TDCC 官方 ISIN→(總代理,機構) 對照（鐵律32：權威源，不猜品牌）──
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=3-4"

_INST2BRAND = [
    ("BNY MELLON","紐約梅隆"),("紐約梅隆","紐約梅隆"),("FRANKLIN","富蘭克林"),("TEMPLETON","富蘭克林"),
    ("富蘭克林","富蘭克林"),("坦伯頓","富蘭克林"),("ALLIANZ","安聯"),("安聯","安聯"),
    ("EURIZON","歐義銳榮"),("歐義銳榮","歐義銳榮"),("BLACKROCK","貝萊德"),("貝萊德","貝萊德"),
    ("SCHRODER","施羅德"),("施羅德","施羅德"),("INVESCO","景順"),("景順","景順"),
    ("JPMORGAN","摩根"),("J.P. MORGAN","摩根"),("FIDELITY","富達"),("FIL ","富達"),("富達","富達"),
    ("PIMCO","品浩"),("品浩","品浩"),("GOLDMAN","高盛"),("NINETY ONE","晉達"),("晉達","晉達"),
    ("JANUS","駿利亨德森"),("駿利","駿利亨德森"),("ROBECO","荷寶"),("荷寶","荷寶"),
    ("LION GLOBAL","利安資金"),("利安","利安資金"),("ABRDN","安本"),("安本","安本"),
    ("MANULIFE","宏利"),("宏利","宏利"),("AMUNDI","東方匯理"),("東方匯理","東方匯理"),("鋒裕","東方匯理"),
    ("DWS","DWS"),("德意志","DWS"),("NEUBERGER","路博邁"),("路博邁","路博邁"),
    ("MFS","MFS"),("PINEBRIDGE","柏瑞"),("柏瑞","柏瑞"),("NATIXIS","法盛"),("法盛","法盛"),
    ("BNP PARIBAS","法巴"),("法國巴黎","法巴"),("PICTET","百達"),("百達","百達"),
    ("BARING","霸菱"),("霸菱","霸菱"),("MUZINICH","Muzinich"),("VALUE PARTNERS","惠理"),("惠理","惠理"),
    ("AXA","安盛"),("M&G","M&G"),("JUPITER","木星"),("T. ROWE","普徕仕"),("普徕仕","普徕仕"),
    ("UBP","瑞聯"),("LOMBARD ODIER","瑞士隆奧"),("FIRST SENTIER","首源"),
    ("MSIM","摩根士丹利"),("MORGAN STANLEY","摩根士丹利"),("PGIM","保德信"),("GAM","GAM"),
    ("KBI","KBI"),("THORNBURG","尚渤"),("UOB","新加坡大華"),("VONTOBEL","Vontobel"),
    ("CAPITAL INTERNATIONAL","資本集團"),("資本國際","資本集團"),
    ("FUNDROCK","FundRock(野村愛爾蘭系列)"),("HSBC","匯豐"),("匯豐","匯豐"),("UBS","瑞銀"),("瑞銀","瑞銀"),
]
# 總代理名關鍵字 → (代碼, 顯示名)；投顧併投信規則已內含
_AGENT_KW = [
    ("合作金庫",("A0048","合庫投信")),("合庫",("A0048","合庫投信")),("野村",("A0032","野村投信")),
    ("富蘭克林",("A0045","富蘭克林華美投信")),("國泰",("A0037","國泰投信")),("永豐",("A0025","永豐投信")),
    ("第一金",("A0003","第一金投信")),("匯豐",("A0004","匯豐投信")),("滙豐",("A0004","匯豐投信")),
    ("景順",("A0006","景順投信")),("瀚亞",("A0007","瀚亞投信")),("玉山",("A0008","玉山投信")),
    ("摩根",("A0011","摩根投信")),("瑞銀",("A0015","瑞銀投信")),("台中銀",("A0017","台中銀投信")),
    ("聯博",("A0018","聯博投信")),("柏瑞",("A0021","柏瑞投信")),("中國信託",("A0026","中國信託投信")),
    ("宏利",("A0027","宏利投信")),("貝萊德",("A0031","貝萊德投信")),("東方匯理",("A0035","東方匯理投信")),
    ("安聯",("A0036","安聯投信")),("富達",("A0038","富達投信")),("德銀",("A0040","德銀遠東投信")),
    ("施羅德",("A0042","施羅德投信")),("台新",("A0047","台新投信")),("大華銀",("A0049","大華銀投信")),
    ("路博邁",("A0050","路博邁投信")),("康和",("B0015","康和投顧")),("萬寶",("B0034","萬寶投顧")),
    ("宏遠",("B0044","宏遠投顧")),("法銀巴黎",("B0049","法銀巴黎投顧")),("霸菱",("B0149","霸菱投顧")),
    ("全球證券",("B0162","全球投顧")),("富盛",("B0313","富盛投顧")),("百達",("B0328","百達投顧")),
    ("品浩",("B0351","品浩太平洋投顧")),("展新",("B0355","展新投顧")),
]


def _clean_series(inst):
    if not inst:
        return ""
    up = inst.upper()
    brand = None
    for kw, zh in _INST2BRAND:
        if kw.upper() in up:
            brand = zh; break
    if not brand:
        brand = inst.split("/")[0].strip()[:6] or inst[:6]
    import re as _re
    m = _re.search(r"[(（]([A-Za-z][A-Za-z0-9 ]{1,10})[)）]", inst)
    if m and brand != "FundRock(野村愛爾蘭系列)":
        suf = m.group(1).strip()
        if suf.upper() not in ("LUXEMBOURG", "IRELAND", "UK", "EUROPE"):
            brand = brand + suf.upper()
    return brand


def _resolve_agent(agent_raw):
    if not agent_raw:
        return ""
    for kw, (code, nm) in _AGENT_KW:
        if kw in agent_raw:
            return "{} {}".format(code, nm)
    return agent_raw.replace("證券投資信託股份有限公司", "投信").replace(
        "證券投資顧問股份有限公司", "投顧")[:12]


def fetch_tdcc_isin_map():
    """抓 TDCC 官方 → {ISIN大寫: (發行公司='代碼 名稱', 系列)}。失敗回空(不擋建庫)。"""
    out = {}
    try:
        r = requests.get(TDCC_URL, headers=H, timeout=90)
        r.encoding = "utf-8"
        import csv as _csv
        import io as _io
        rd = _csv.reader(_io.StringIO(r.text))
        header = next(rd, None)
        if not header:
            return out
        idx = {h.strip(): i for i, h in enumerate(header)}
        i_isin = idx.get("基金ISIN_CODE") or idx.get("ISINCODE")
        i_agent = idx.get("基金總代理名稱") or idx.get("總代理機構")
        i_inst = idx.get("境外基金機構")
        if i_isin is None:
            return out
        for row in rd:
            if len(row) <= max(i for i in [i_isin, i_agent, i_inst] if i is not None):
                continue
            iz = row[i_isin].strip().upper()
            if not iz:
                continue
            agent = _resolve_agent(row[i_agent].strip()) if i_agent is not None else ""
            series = _clean_series(row[i_inst].strip()) if i_inst is not None else ""
            out[iz] = (agent, series)
        print("  TDCC 對照：{} 檔 ISIN→總代理/機構".format(len(out)))
    except Exception as e:
        print("  TDCC 抓取失敗(用名稱備援)：{}".format(str(e)[:60]))
    return out


def enumerate_universe():
    tdcc = fetch_tdcc_isin_map()      # 官方 ISIN→(發行,系列)，鐵律32
    funds, page, last = [], 1, 1
    while page <= last:
        try:
            r = requests.get(LIST, params={"order": "priceDate", "sort": "desc",
                             "page": page, "institutional": 0, "fields": FIELDS},
                             headers=H, timeout=30)
            it = r.json().get("items", {}) or {}
            last = it.get("last_page", 1) or 1
            for d in it.get("data", []) or []:
                if d.get("sitca"):
                    continue
                code = (d.get("cnyesId") or "").replace(",", "")
                if not code:
                    continue
                _isin = (d.get("isin") or "").strip().upper()
                _agent, _series = tdcc.get(_isin, ("", ""))
                funds.append({
                    "代碼": code, "0P": d.get("fundClassId") or "",
                    "isin": d.get("isin") or "",
                    "名稱": (d.get("displayNameLocal") or "")[:50],
                    "幣別": d.get("classCurrencyLocal") or "",
                    "投資區域": d.get("investmentArea") or "未分類",
                    "資產類型": classify_asset(d.get("displayNameLocal"), d.get("categoryAbbr")),
                    "發行": _agent, "系列": _series,
                })
        except Exception as e:
            print("  第{}頁失敗：{}".format(page, e))
        if page % 20 == 0:
            print("  已掃 {}/{} 頁，累計境外 {} 檔".format(page, last, len(funds)))
        page += 1
        time.sleep(0.25)
    return funds


def write_universe(funds):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(UNIVERSE_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=UNI_COLS)
        w.writeheader()
        for x in funds:
            w.writerow(x)
    print("→ 寫入 {}：{} 檔境外".format(UNIVERSE_CSV, len(funds)))


def read_universe():
    with open(UNIVERSE_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def yf_history(op):
    if not op or not _HAS_YF:
        return [], ""
    for suf in SUFFIXES:
        sym = op + suf
        try:
            df = yf.Ticker(sym).history(period="max", auto_adjust=True)
            if df is not None and len(df) > 1:
                out = []
                for idx, row in df.iterrows():
                    c = row.get("Close")
                    if c == c and c:
                        out.append((idx.strftime("%Y-%m-%d"), float(c)))
                if out:
                    return out, sym
        except Exception:
            pass
        time.sleep(0.2)
    return [], ""


def fetch_segment():
    uni = read_universe()
    mine = [f for i, f in enumerate(uni) if i % SEG_TOTAL == SEG_INDEX]
    print("本段 {}/{}：負責 {} / 全 {} 檔".format(SEG_INDEX, SEG_TOTAL, len(mine), len(uni)))
    out_path = _seg_name(os.path.join(DATA_DIR, "offshore_nav.csv"))
    os.makedirs(DATA_DIR, exist_ok=True)
    ok, total_rows = 0, 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NAV_COLS)
        w.writeheader()
        for n, fund in enumerate(mine):
            rows, sym = yf_history(fund["0P"])
            if rows:
                ok += 1
                for iso, nav in rows:
                    w.writerow({"代碼": fund["代碼"], "日期": iso, "淨值": nav,
                                "幣別": fund["幣別"], "名稱": fund["名稱"],
                                "來源": "yfinance", "資產類型": fund["資產類型"],
                                "投資區域": fund["投資區域"],
                                "發行": fund.get("發行", ""), "系列": fund.get("系列", "")})
                total_rows += len(rows)
            if (n + 1) % 50 == 0:
                print("  進度 {}/{}：命中 {} 檔、{:,} 筆".format(n + 1, len(mine), ok, total_rows))
            time.sleep(SLEEP)
    cov = ok / len(mine) if mine else 0
    print("=" * 60)
    print("本段完成：{}/{} 檔有歷史（{:.0%}）、共 {:,} 筆 → {}".format(
        ok, len(mine), cov, total_rows, out_path))
    print("=" * 60)


def main():
    print("境外建庫  MODE={} SEG={}/{}  yfinance={}".format(MODE, SEG_INDEX, SEG_TOTAL, _HAS_YF))
    if MODE == "enumerate":
        funds = enumerate_universe()
        if not funds:
            raise SystemExit("清單抓到 0 檔，cnyes 可能改版")
        write_universe(funds)
    else:
        if not _HAS_YF:
            raise SystemExit("需要 yfinance")
        fetch_segment()


if __name__ == "__main__":
    main()
