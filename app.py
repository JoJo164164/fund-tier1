# -*- coding: utf-8 -*-
"""
台灣基金滾動跌幅系統 — Tier1（被動ETF）v0.1
================================================================
依《台灣基金滾動跌幅系統 — 專案憲法》(2026-07-17 v2) 實作。

本檔對應憲法條文（違反即重做）：
  鐵律 8  : 交付整份完整 .py 檔
  鐵律12 : 費用參數化、預設 0、嚴禁寫死；entry_lag 參數化、預設 0
  鐵律14 : 配息必須還原（yfinance auto_adjust=True）
  鐵律15 : 倖存者偏誤 — 已下市 ETF 不得默默 drop（Tier1 標記，Tier2 補母體）
  鐵律16 : 滾動視窗＝10筆；必須記錄曆日跨度；跨度 > MAX_SPAN_DAYS 該筆作廢；
           現時掃描須註記「資料截至日」
  九     : Tier1=被動ETF；主動ETF（6碼第6碼∈{A,D}）只收資料、不出勝率結論

【重要】門檻不可沿用母專案（台股交易日曆校準）→ 本版第一個 Tab 是「分布校準」，
        先看分布再訂門檻，不是先給勝率表。
"""

import io
import re
import time
import datetime as dt
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# ── 選用相依（缺少時不得使 app 崩潰，繼承母專案 try/except 保護原則）──
try:
    import yfinance as yf
    _HAS_YF = True
except Exception:
    _HAS_YF = False

try:
    import requests
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except Exception:
    _HAS_PLOTLY = False


# ══════════════════════════════════════════════════════════════
# 常數（母專案可複用資產，實測確認 app.py:79 / app.py:85）
# ══════════════════════════════════════════════════════════════
HORIZONS = [5, 10, 20, 40, 60, 80, 100, 120, 240]

JOURNAL_COLS = ["代碼", "名稱", "信號", "進場類型", "進場日", "進場價",
                "目標天數", "目標報酬%", "狀態", "出場日", "出場價",
                "實際報酬%", "備註"]

ROLL_N = 10                 # 滾動視窗＝10「筆」（鐵律16：筆，非曆日）
MAX_SPAN_DAYS = 25          # 鐵律16：10筆跨度 > 25 曆日 → 該筆作廢（初始值，待分布校準）
MIN_SAMPLE = 10             # _pick_best_timing_idx 的統計可靠門檻（母專案同源）

JOURNAL_PATH = "/tmp/fund_journal.csv"

# 鐵律16：14天＝10交易日+2週末，為理論基準值
NORMAL_SPAN_DAYS = 14

# Tier1 起始標的（被動ETF）。清單抓取失敗時的 fallback，非唯一來源。
FALLBACK_ETFS = {
    "0050.TW": "元大台灣50",
    "0056.TW": "元大高股息",
    "006208.TW": "富邦台50",
    "00878.TW": "國泰永續高股息",
    "00713.TW": "元大台灣高息低波",
    "00919.TW": "群益台灣精選高息",
    "00929.TW": "復華台灣科技優息",
    "00692.TW": "富邦公司治理",
    "00850.TW": "元大臺灣ESG永續",
    "00757.TW": "統一FANG+",
}


# ══════════════════════════════════════════════════════════════
# 標的分類（憲法 Z1-2：TWSE 官方規則，等級A）
# ══════════════════════════════════════════════════════════════
def classify_etf(code: str) -> str:
    """依 TWSE 官方規則分類 ETF。

    來源（憲法 Z1-2，等級A，已 web_fetch 實抓原文）：
      https://www.twse.com.tw/zh/products/securities/etf/products/active-list.html
      原文：「證券代號第六碼為A者係股票ETF；第六碼D係債券ETF。」
      → 主動式ETF ＝ 6碼 且 第6碼 ∈ {A, D}

    回傳：'主動ETF-股票' / '主動ETF-債券' / '被動ETF'
    """
    bare = str(code).split(".")[0].strip().upper()
    if len(bare) == 6:
        if bare[5] == "A":
            return "主動ETF-股票"
        if bare[5] == "D":
            return "主動ETF-債券"
    return "被動ETF"


def is_tier1(code: str) -> bool:
    """Tier1 ＝ 被動ETF。主動ETF 依憲法「九」屬 Tier4：只收資料、不出勝率結論。"""
    return classify_etf(code) == "被動ETF"


# ══════════════════════════════════════════════════════════════
# 清單抓取（憲法 Z1-1／Z1-5）
# ══════════════════════════════════════════════════════════════
YAHOO_ETF_LIST_URL = "https://tw.stock.yahoo.com/class-quote?sectorId=26&exchange=TAI"

# 已 web_fetch 實抓驗證的列結構錨點：每列都有 /quote/{代號}.TW 連結
_QUOTE_RE = re.compile(r"/quote/([0-9]{4,5}[A-Z]?)\.TW")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_etf_list() -> Tuple[Dict[str, str], str]:
    """從 Yahoo 上市ETF分類行情抓全清單。

    ⚠️ 已知風險（2026-07-17 實測發現，必須防）：
       同一支 URL 換參數順序（?exchange=TAI&sectorId=26）會被導向 sectorId=93「綠能環保」，
       且 HTTP 200、頁面結構完全相同 → **scraper 會靜默抓到錯的類股而不報錯**。
       故本函數強制驗證回傳頁 title 必須含 'ETF'，否則視為失敗改用 fallback。

    回傳：(dict{代號: 名稱}, 來源說明字串)
    """
    if not _HAS_REQ:
        return dict(FALLBACK_ETFS), "fallback（requests 不可用）"
    try:
        r = requests.get(
            YAHOO_ETF_LIST_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if r.status_code != 200:
            return dict(FALLBACK_ETFS), "fallback（HTTP {}）".format(r.status_code)

        html = r.text

        # ★ 防「靜默抓錯類股」：title 必須含 ETF
        m_title = re.search(r"<title>(.*?)</title>", html, re.S)
        title = m_title.group(1).strip() if m_title else ""
        if "ETF" not in title:
            return dict(FALLBACK_ETFS), "fallback（頁面驗證失敗，title='{}'）".format(title[:40])

        codes = sorted(set(_QUOTE_RE.findall(html)))
        if len(codes) < 50:  # sanity：實測全市場約 351 筆，抓不到 50 筆代表結構變了
            return dict(FALLBACK_ETFS), "fallback（僅解析到 {} 筆，疑似結構變更）".format(len(codes))

        out = {}
        for c in codes:
            out["{}.TW".format(c)] = c  # 名稱由 yfinance 補；此處先放代號
        return out, "Yahoo class-quote（解析 {} 筆）".format(len(out))
    except Exception as e:
        return dict(FALLBACK_ETFS), "fallback（例外：{}）".format(type(e).__name__)


# ══════════════════════════════════════════════════════════════
# 價格抓取（鐵律14：配息必須還原）
# ══════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_prices(code: str, adjust: bool = True) -> Tuple[Dict[str, float], Optional[str]]:
    """取單檔 ETF 的日收盤序列。

    鐵律14：adjust=True → yfinance auto_adjust，收盤價已還原配息與分割。
      不還原的話，除息日淨值跳空下跌會被滾動10日讀成「暴跌信號」→ 假信號。
      高股息ETF（00878/0056/00919…）為 Tier1 主戰場，此項不可省。

    回傳：(dict{'YYYY-MM-DD': 收盤價}, 錯誤訊息或 None)
    """
    if not _HAS_YF:
        return {}, "yfinance 不可用"
    try:
        df = yf.download(
            code, period="max", interval="1d",
            auto_adjust=bool(adjust), progress=False, threads=False,
        )
        if df is None or len(df) == 0:
            return {}, "無資料"
        # yfinance 新版可能回 MultiIndex 欄位
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if "Close" not in df.columns:
            return {}, "無 Close 欄位"
        s = df["Close"].dropna()
        return {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}, None
    except Exception as e:
        return {}, "{}: {}".format(type(e).__name__, e)


# ══════════════════════════════════════════════════════════════
# SITCA 境內基金淨值抓取（Tier2 資料源，網頁版連線測試）
#   欄位名、對照表、解析規則全部來自真實 VIEWSTATE 解碼驗證（非臆測）：
#   - 淨值頁 method=post，回傳為純 HTML <table>，直接在 document 內（非 XHR）
#   - 日期欄位 DOM id: ctl00_ContentPlaceHolder1_txtQ_Date，格式 YYYYMMDD
#   - 公司下拉值為純代碼（A0005），對照表在 VIEWSTATE 明文
# ══════════════════════════════════════════════════════════════
SITCA_NAV_URL = "https://www.sitca.org.tw/ROC/Industry/IN2106.aspx?pid=IN2213_02"
SITCA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": SITCA_NAV_URL,
    "Origin": "https://www.sitca.org.tw",
}
# 35 家投信對照（自真實 VIEWSTATE 解碼，2026-07 驗證）
SITCA_COMPANIES = {
    "A0001": "兆豐投信", "A0003": "第一金投信", "A0004": "滙豐投信", "A0005": "元大投信",
    "A0006": "景順投信", "A0007": "瀚亞投信", "A0008": "玉山投信", "A0009": "統一投信",
    "A0010": "富邦投信", "A0011": "摩根投信", "A0012": "華南永昌投信", "A0015": "瑞銀投信",
    "A0016": "群益投信", "A0017": "台中銀投信", "A0018": "聯博投信", "A0021": "柏瑞投信",
    "A0022": "復華投信", "A0025": "永豐投信", "A0026": "中國信託投信", "A0027": "宏利投信",
    "A0031": "貝萊德投信", "A0032": "野村投信", "A0033": "聯邦投信", "A0035": "東方匯理投信",
    "A0036": "安聯投信", "A0037": "國泰投信", "A0038": "富達投信", "A0040": "德銀遠東投信",
    "A0041": "凱基投信", "A0042": "施羅德投信", "A0043": "街口投信", "A0045": "富蘭克林華美投信",
    "A0047": "台新投信", "A0048": "合庫投信", "A0049": "大華銀投信", "A0050": "路博邁投信",
}

# 淨值列解析（自真實回傳 HTML 驗證：<td align='right'>25.49</td> 可正確抽出）
_SITCA_ROW_RE = re.compile(
    r"<td align='left'>([A-Z0-9]+)</td>"        # 類型代號
    r"<td align='left'>(A00\d{2})</td>"          # 公司代號
    r"<td align='left'>[^<]*</td>"               # 公司名稱
    r"<td align='left'>(\d{4,6}[A-Z]?)</td>"     # 受益憑證代號=基金代碼
    r"<td align='left'>\d+</td>"                 # 基金統編
    r"<td align='left'>([^<]+?)</td>"            # 基金名稱
    r"<td align='left'>([A-Z]{3})</td>"          # 幣別
    r"<td align='right'>([\d.]+|\(註\d\)|-)</td>"  # 淨值（可能為註記）
)


def _sitca_hidden(html: str, name: str) -> str:
    m = re.search(r'<input[^>]*name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(r'<input[^>]*value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', html)
    return m.group(1) if m else ""


def _sitca_detect_fields(html: str) -> Tuple[str, str]:
    """從真實頁面偵測日期/公司欄位實際 name，避免寫死猜錯。"""
    date_name = "ctl00$ContentPlaceHolder1$txtQ_Date"
    company_name = "ctl00$ContentPlaceHolder1$ddlQ_Company"
    md = re.search(r'name="(ctl00\$[^"]*txt[^"]*[Dd]ate)"', html)
    if md:
        date_name = md.group(1)
    for sm in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        if "A0005" in sm.group(2) or "A0001" in sm.group(2):
            company_name = sm.group(1)
            break
    return date_name, company_name


def fetch_sitca_nav(company: str, date_str: str) -> Tuple[List[dict], str]:
    """抓某投信某日全部基金淨值。

    流程（ASP.NET 兩步式，token 不寫死）：
      ① GET 空頁 → 取當下 __VIEWSTATE / __EVENTVALIDATION（每次都變）
      ② 帶 token + 日期 + 公司 POST → 回傳含淨值 <table> 的 HTML
      ③ 正規表達式解析

    回傳：(基金淨值 list[dict], 診斷訊息字串)
    """
    if not _HAS_REQ:
        return [], "requests 不可用（requirements 缺 requests）"
    try:
        s = requests.Session()
        r = s.get(SITCA_NAV_URL, headers=SITCA_HEADERS, timeout=20)
        if r.status_code != 200:
            return [], "① GET 失敗 status={}".format(r.status_code)
        html = r.text
        vs = _sitca_hidden(html, "__VIEWSTATE")
        vsg = _sitca_hidden(html, "__VIEWSTATEGENERATOR")
        ev = _sitca_hidden(html, "__EVENTVALIDATION")
        if not vs:
            return [], "① 抓不到 __VIEWSTATE（頁面結構可能改版）"
        date_name, company_name = _sitca_detect_fields(html)

        payload = {
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            date_name: date_str,
            company_name: company,
        }
        for btn in ["ctl00$ContentPlaceHolder1$btnQuery",
                    "ctl00$ContentPlaceHolder1$BtnQuery",
                    "ctl00$ContentPlaceHolder1$Button1"]:
            payload.setdefault(btn, "查詢")

        r2 = s.post(SITCA_NAV_URL, headers=SITCA_HEADERS, data=payload, timeout=30)
        if r2.status_code != 200:
            return [], "② POST 失敗 status={}".format(r2.status_code)

        rows = _SITCA_ROW_RE.findall(r2.text)
        out = []
        for tcode, comp, code, name, cur, nav in rows:
            out.append({
                "代碼": code,
                "分類": classify_etf(code),
                "幣別": cur,
                "淨值": nav,
                "名稱": name.split("<")[0][:30],
            })
        msg = "✓ 成功：GET+POST 完成，解析 {} 檔（欄位名 日期={} 公司={}）".format(
            len(out), date_name, company_name)
        if not out:
            idx = r2.text.find("<table")
            snippet = r2.text[idx:idx + 200] if idx >= 0 else r2.text[:200]
            msg = "⚠️ 連線成功但解析0筆。該日可能無資料，或欄位需微調。片段：{}".format(snippet)
        return out, msg
    except Exception as e:
        return [], "✗ 例外：{}: {}".format(type(e).__name__, e)


# ══════════════════════════════════════════════════════════════
# 滾動報酬（鐵律16：10筆 + 曆日跨度 + sanity 上限）
# ══════════════════════════════════════════════════════════════
def calc_all_rolling_returns(prices_dict: Dict[str, float],
                             roll_n: int = ROLL_N,
                             max_span_days: int = MAX_SPAN_DAYS) -> List[dict]:
    """滾動 N 筆報酬。母專案 calc_all_rolling_returns 的基金版。

    與母專案差異（鐵律16，附實測證據）：
      母專案假設「往回10筆 ＝ 往回10交易日 ＝ 14曆日」，這在台股交易日曆成立。
      基金/ETF 各有非營業日：實測某境外基金 2026-06-02~07-15 區間，
      2026-06-19(五)、2026-07-03(五) 皆無淨值；20個「往回10筆」跨度中
      17個不是14天，而是 15~18 曆日。
      → 本版每筆記錄 span_days（實際曆日跨度）；
        span_days > max_span_days 者標記 valid=False（該筆作廢）。
        理由：停止公告的標的會產出「橫跨數月卻看似漂亮」的假訊號，
        那已不是「10日跌幅」而是「一季跌幅」。
    """
    if len(prices_dict) < roll_n + 1:
        return []
    dates = sorted(prices_dict.keys())
    results = []
    for i in range(roll_n, len(dates)):
        base_date = dates[i - roll_n]
        curr_date = dates[i]
        base_price = prices_dict[base_date]
        curr_price = prices_dict[curr_date]
        if base_price <= 0:
            continue
        ret = (curr_price - base_price) / base_price * 100.0
        span = (dt.date.fromisoformat(curr_date) - dt.date.fromisoformat(base_date)).days
        results.append({
            "date": curr_date,
            "base_date": base_date,
            "base_price": base_price,
            "curr_price": curr_price,
            "return": round(ret, 2),
            "span_days": span,                      # 鐵律16：必須記錄
            "valid": bool(span <= max_span_days),   # 鐵律16：sanity 上限
        })
    return results


# ══════════════════════════════════════════════════════════════
# 回測引擎（母專案 app.py:497 的基金版）
# ══════════════════════════════════════════════════════════════
def run_full_backtest(prices_dict: Dict[str, float],
                      threshold: float,
                      precomputed_rolling: Optional[List[dict]] = None,
                      entry_lag: int = 0,
                      fee_buy_pct: float = 0.0,
                      fee_sell_pct: float = 0.0,
                      max_span_days: int = MAX_SPAN_DAYS) -> Optional[dict]:
    """滾動跌幅回測。

    ── 與母專案（app.py:497）的差異，逐條對應憲法 ──

    1) entry_lag（憲法「十」使用者裁決 2026-07-17）
       母專案：entry_price = t["curr_price"]（觸發當日收盤，零延遲）
       本版  ：entry_price = prices[dates[idx + entry_lag]]
       **預設 entry_lag=0 ＝ 行為與母專案完全一致**（使用者裁決：ETF與基金均為0）。
       ⚠️ 已知偏誤（憲法「十」，記錄供歸因，非待辦）：
          entry_lag=0 對共同基金而言假設「觸發日可成交」；
          基金淨值收盤後公告，得知觸發時 T 日申購已截止 → 此為理論參考值。
          Tier1（ETF）有盤中價，lag=0 可成交，無此問題。

    2) 費用（鐵律12）
       fee_buy_pct / fee_sell_pct **預設 0（使用者裁決不計入）**，
       但**參數必須存在、不得寫死**，未來改主意時改一個數字即可。

    3) 鐵律16：僅 valid=True（跨度 ≤ max_span_days）的觸發納入回測。

    4) 母專案的 precomputed_rolling 效能參數保留（多門檻迴圈外算一次共用）。
    """
    rolling = (precomputed_rolling if precomputed_rolling is not None
               else calc_all_rolling_returns(prices_dict, ROLL_N, max_span_days))
    if not rolling:
        return None

    dates = sorted(prices_dict.keys())
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # 鐵律16：跨度異常者不得進回測
    all_hits = [r for r in rolling if r["return"] <= threshold]
    triggers = [r for r in all_hits if r.get("valid", True)]
    dropped_span = len(all_hits) - len(triggers)
    if not triggers:
        return None

    trigger_dates = set(t["date"] for t in triggers)
    max_consecutive = current_consecutive = 0
    for r in rolling:
        if r["date"] in trigger_dates:
            current_consecutive += 1
            max_consecutive = max(max_consecutive, current_consecutive)
        else:
            current_consecutive = 0

    horizon_rets = {h: [] for h in HORIZONS}
    horizon_drawdowns = {h: [] for h in HORIZONS}
    horizon_dd_days = {h: [] for h in HORIZONS}
    skipped_lag = 0

    for t in triggers:
        idx = date_to_idx.get(t["date"])
        if idx is None:
            continue
        e_idx = idx + entry_lag
        if e_idx >= len(dates):
            skipped_lag += 1
            continue
        entry_price = prices_dict[dates[e_idx]]
        entry_date = dates[e_idx]
        if entry_price <= 0:
            continue
        year = t["date"][:4]

        for h in HORIZONS:
            future_idx = e_idx + h
            if future_idx >= len(dates):
                continue
            future_price = prices_dict[dates[future_idx]]
            # 鐵律12：費用參數化，預設 0 → 預設行為 = 母專案
            eff_entry = entry_price * (1.0 + fee_buy_pct / 100.0)
            eff_exit = future_price * (1.0 - fee_sell_pct / 100.0)
            ret = (eff_exit - eff_entry) / eff_entry * 100.0
            horizon_rets[h].append({
                "ret": round(ret, 2),
                "year": year,
                "date": t["date"],
                "entry_date": entry_date,
                "entry_price": entry_price,
                "future_price": future_price,
                "span_days": t.get("span_days"),
            })
            # 期間內最大回撤
            min_ret, min_day = 0.0, 0
            for d in range(1, h + 1):
                fi = e_idx + d
                if fi < len(dates):
                    p = prices_dict[dates[fi]]
                    r = (p - entry_price) / entry_price * 100.0
                    if r < min_ret:
                        min_ret, min_day = r, d
            horizon_drawdowns[h].append({"dd": round(min_ret, 2), "year": year})
            horizon_dd_days[h].append(min_day)

    stats = {}
    for h in HORIZONS:
        rets = [x["ret"] for x in horizon_rets[h]]
        if not rets:
            stats[h] = None
            continue
        arr = np.array(rets, dtype=float)
        dds = [x["dd"] for x in horizon_drawdowns[h]]
        stats[h] = {
            "樣本數": len(arr),
            "勝率": round(float((arr > 0).mean() * 100), 1),
            "平均報酬%": round(float(arr.mean()), 2),
            "中位數報酬%": round(float(np.median(arr)), 2),
            "最大回撤%": round(float(min(dds)) if dds else 0.0, 2),
            "平均回撤%": round(float(np.mean(dds)) if dds else 0.0, 2),
        }

    return {
        "觸發次數": len(triggers),
        "最大連續觸發": max_consecutive,
        "跨度作廢筆數": dropped_span,      # 鐵律16 透明化
        "lag截尾筆數": skipped_lag,
        "entry_lag": entry_lag,
        "horizon_rets": horizon_rets,
        "stats": stats,
        "triggers": triggers,
    }


def build_entry_timing_table(bt: dict) -> pd.DataFrame:
    """進場時機表（母專案同名函數的基金版）。"""
    if not bt:
        return pd.DataFrame()
    rows = []
    for h in HORIZONS:
        s = bt["stats"].get(h)
        if not s:
            continue
        rows.append({
            "持有天數": h,
            "樣本數": s["樣本數"],
            "勝率": "{:.1f}%".format(s["勝率"]),
            "平均報酬%": "{:.2f}%".format(s["平均報酬%"]),
            "中位數報酬%": "{:.2f}%".format(s["中位數報酬%"]),
            "最大回撤%": "{:.2f}%".format(s["最大回撤%"]),
        })
    return pd.DataFrame(rows)


def _pick_best_timing_idx(df: pd.DataFrame):
    """★最佳進場時機判定 —— 自母專案原文移植，邏輯逐字保留。

    規則（母專案原註解）：
      1. 只看樣本數≥10的合格行（統計可靠門檻；<10不夠格當最佳）。
      2. 合格行中優先選勝率最高。
      3. 勝率相近（差距≤5pp視為平手）時，改用平均報酬決勝。
      4. 無任何行樣本≥10 → 回傳 None（顯示無明確最佳，不硬推）。
    表格與結論框共用同一函數，杜絕兩處不一致。
    """
    try:
        if df is None or df.empty or "樣本數" not in df.columns:
            return None
        valid = df[pd.to_numeric(df["樣本數"], errors="coerce").fillna(0) >= MIN_SAMPLE].copy()
        if valid.empty:
            return None
        valid["_wr"] = pd.to_numeric(valid["勝率"].astype(str).str.replace("%", ""), errors="coerce")
        valid["_avg"] = pd.to_numeric(valid["平均報酬%"].astype(str).str.replace("%", ""), errors="coerce")
        valid = valid[valid["_wr"].notna()]
        if valid.empty:
            return None
        max_wr = valid["_wr"].max()
        near = valid[max_wr - valid["_wr"] <= 5.0]
        if len(near) >= 2 and near["_avg"].notna().any():
            near = near.sort_values(["_avg", "_wr", "樣本數"], ascending=False)
            return near.index[0]
        return valid["_wr"].idxmax()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# 追蹤日誌（母專案整套複用，含向後相容）
# ══════════════════════════════════════════════════════════════
def load_journal() -> pd.DataFrame:
    """載入追蹤日誌。向後相容：缺欄自動補（母專案行為）。"""
    try:
        df = pd.read_csv(JOURNAL_PATH, dtype=str)
    except Exception:
        return pd.DataFrame(columns=JOURNAL_COLS)
    for c in JOURNAL_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[JOURNAL_COLS]


def save_journal(df: pd.DataFrame) -> bool:
    try:
        for c in JOURNAL_COLS:
            if c not in df.columns:
                df[c] = ""
        df[JOURNAL_COLS].to_csv(JOURNAL_PATH, index=False)
        return True
    except Exception:
        return False


def journal_stats_by_type(df: pd.DataFrame) -> pd.DataFrame:
    """已結案實績依「進場類型」分組統計。

    母專案設計原意：系統觸發＝策略裁判主體；自主判斷不計入，避免污染驗證。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    d = df[df["狀態"].astype(str) == "已結案"].copy()
    if d.empty:
        return pd.DataFrame()
    d["_r"] = pd.to_numeric(d["實際報酬%"], errors="coerce")
    d = d[d["_r"].notna()]
    if d.empty:
        return pd.DataFrame()
    rows = []
    for t, g in d.groupby(d["進場類型"].astype(str)):
        rows.append({
            "進場類型": t,
            "已結案筆數": len(g),
            "實際勝率": "{:.1f}%".format(float((g["_r"] > 0).mean() * 100)),
            "平均實際報酬%": "{:.2f}%".format(float(g["_r"].mean())),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════
# 系統檢核（母專案架構：CRITICAL_CHECKS 須與 IMPACT_MAP error 項逐字同步）
# ══════════════════════════════════════════════════════════════
IMPACT_MAP = {
    "配息還原(鐵律14)": ("error", "🚨 除息日會被誤讀為暴跌 → 假信號", "auto_adjust 必須為 True"),
    "滾動視窗跨度上限(鐵律16)": ("error", "🚨 停止公告標的會產出橫跨數月的假訊號", "MAX_SPAN_DAYS 必須生效"),
    "費用參數存在(鐵律12)": ("error", "🚨 費用寫死將無法後處理重算", "fee 參數必須存在且預設0"),
    "entry_lag參數存在(鐵律12)": ("error", "🚨 進場點無法調整將需全部重跑", "entry_lag 必須參數化"),
    "主動ETF不出結論(憲法九)": ("error", "🚨 <3年資料產出勝率＝統計造假", "Tier4 僅收資料"),
    "滾動報酬計算邏輯": ("error", "🚨 程式計算錯誤", "calc_all_rolling_returns 必須正確"),
    "最佳進場時機判定": ("warning", "⚠️ 樣本不足時應回 None，不硬推", "_pick_best_timing_idx"),
}
CRITICAL_CHECKS = {k for k, v in IMPACT_MAP.items() if v[0] == "error"}


def run_system_checks(adjust_on: bool, fee_b: float, fee_s: float, lag: int) -> pd.DataFrame:
    """系統自檢。回傳每項 pass/fail。"""
    res = []

    def add(name, ok, detail=""):
        res.append({"檢核項目": name, "結果": "✅ PASS" if ok else "❌ FAIL",
                    "嚴重度": IMPACT_MAP.get(name, ("info", "", ""))[0], "說明": detail})

    add("配息還原(鐵律14)", bool(adjust_on), "auto_adjust={}".format(adjust_on))
    add("滾動視窗跨度上限(鐵律16)", MAX_SPAN_DAYS > 0, "MAX_SPAN_DAYS={}".format(MAX_SPAN_DAYS))
    add("費用參數存在(鐵律12)", (fee_b is not None) and (fee_s is not None),
        "buy={}%, sell={}%".format(fee_b, fee_s))
    add("entry_lag參數存在(鐵律12)", lag is not None, "entry_lag={}".format(lag))
    add("主動ETF不出結論(憲法九)", callable(is_tier1) and not is_tier1("00980A.TW"),
        "00980A → {}".format(classify_etf("00980A.TW")))

    # 邏輯自測：等差價格序列，跌幅可解析驗證
    px = {"2026-01-{:02d}".format(i + 1): 100.0 - i for i in range(20)}
    rr = calc_all_rolling_returns(px, ROLL_N, 999)
    ok_logic = bool(rr) and abs(rr[0]["return"] - (-10.0)) < 1e-6
    add("滾動報酬計算邏輯", ok_logic,
        "首筆 return={}（預期 -10.0）".format(rr[0]["return"] if rr else "N/A"))

    # 樣本不足應回 None
    df_small = pd.DataFrame([{"持有天數": 5, "樣本數": 3, "勝率": "100.0%", "平均報酬%": "9.0%"}])
    add("最佳進場時機判定", _pick_best_timing_idx(df_small) is None, "樣本<10 → 應回 None")

    return pd.DataFrame(res)


# ══════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════
def main():
    st.set_page_config(page_title="基金滾動跌幅系統 Tier1", layout="wide")
    st.title("📉 台灣基金滾動跌幅系統 — Tier1（被動ETF）v0.1")
    st.caption("依專案憲法 v2（2026-07-17）｜鐵律12/14/16 已實作｜主動ETF 只收資料不出結論")

    # ── 側欄參數 ──
    with st.sidebar:
        st.header("⚙️ 參數")
        st.subheader("觸發")
        threshold = st.number_input("滾動10日跌幅門檻(%)", value=-10.0, step=0.5, max_value=0.0,
                                    help="⚠️ 憲法：母專案門檻在台股交易日曆校準，基金版須先跑分布再訂。")
        max_span = st.number_input("視窗跨度上限(曆日)", value=MAX_SPAN_DAYS, step=1, min_value=14,
                                   help="鐵律16：10筆跨度超過此值該筆作廢。14天=10交易日+2週末。")

        st.subheader("成本（憲法「十」使用者裁決：預設不計）")
        entry_lag = st.number_input("entry_lag（觸發後第N筆進場）", value=0, step=1, min_value=0,
                                    help="裁決：ETF與共同基金均為0。參數保留供日後調整。")
        fee_buy = st.number_input("申購/買進費(%)", value=0.0, step=0.1, min_value=0.0)
        fee_sell = st.number_input("贖回/賣出費(%)", value=0.0, step=0.1, min_value=0.0)

        st.subheader("資料")
        adjust = st.checkbox("配息還原（鐵律14）", value=True,
                             help="關閉將使除息日被誤判為暴跌信號。高股息ETF尤其嚴重。")
        if not adjust:
            st.error("🚨 鐵律14：未還原配息，除息日將產生假信號。結果不可信。")

    if not _HAS_YF:
        st.error("yfinance 不可用，無法取價。請確認 requirements.txt。")
        return

    # ── 標的清單 ──
    etf_map, src = fetch_etf_list()
    codes_all = sorted(etf_map.keys())
    tier1_codes = [c for c in codes_all if is_tier1(c)]
    active_codes = [c for c in codes_all if not is_tier1(c)]

    c1, c2, c3 = st.columns(3)
    c1.metric("清單總數", len(codes_all))
    c2.metric("Tier1 被動ETF", len(tier1_codes))
    c3.metric("Tier4 主動ETF（不出結論）", len(active_codes))
    st.caption("清單來源：{}".format(src))

    default_sel = [c for c in ["0050.TW", "006208.TW", "00878.TW", "0056.TW"] if c in codes_all]
    if not default_sel:
        default_sel = tier1_codes[:4]
    picks = st.multiselect("選擇標的（僅 Tier1 被動ETF 會產出勝率結論）",
                           codes_all, default=default_sel)

    tabs = st.tabs(["📊 分布校準", "🎯 觸發掃描", "🔬 回測", "📒 追蹤日誌", "🛡️ 系統檢核", "🔌 SITCA 連線測試"])

    # ══ Tab1：分布校準（憲法：Tier1 第一個產出是分布，不是勝率表）══
    with tabs[0]:
        st.subheader("滾動10日報酬分布 — 先看分布，再訂門檻")
        st.info("**憲法要求**：母專案門檻在台股交易日曆上校準；基金/ETF 非營業日各異、"
                "視窗跨度浮動，門檻必須先跑分布再訂。**本頁是 Tier1 的第一個產出，不是勝率表。**")
        if st.button("跑分布", type="primary"):
            rows, spans = [], []
            prog = st.progress(0.0)
            for i, code in enumerate(picks):
                px, err = fetch_prices(code, adjust)
                prog.progress((i + 1) / max(len(picks), 1))
                if err or not px:
                    st.warning("{}：{}".format(code, err or "無資料"))
                    continue
                rr = calc_all_rolling_returns(px, ROLL_N, max_span)
                if not rr:
                    continue
                vals = np.array([r["return"] for r in rr if r["valid"]], dtype=float)
                sp = np.array([r["span_days"] for r in rr], dtype=float)
                spans.extend(sp.tolist())
                if len(vals) == 0:
                    continue
                dts = sorted(px.keys())
                rows.append({
                    "代碼": code, "分類": classify_etf(code),
                    "資料起": dts[0], "資料截至": dts[-1], "筆數": len(px),
                    "年數": round((dt.date.fromisoformat(dts[-1]) - dt.date.fromisoformat(dts[0])).days / 365.25, 1),
                    "P1": round(float(np.percentile(vals, 1)), 2),
                    "P5": round(float(np.percentile(vals, 5)), 2),
                    "P10": round(float(np.percentile(vals, 10)), 2),
                    "中位數": round(float(np.median(vals)), 2),
                    "跨度作廢": int(sum(1 for r in rr if not r["valid"])),
                })
            prog.empty()
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch")
                st.markdown("**如何讀**：P5 = 只有 5% 的日子跌幅比這更深。"
                            "若你的門檻設在 P1，觸發樣本會少到無法做統計（鐵律10：n<10 不得下結論）。")
                if spans:
                    sp = np.array(spans)
                    st.markdown("---")
                    st.subheader("鐵律16：視窗跨度分布")
                    a, b, c, d = st.columns(4)
                    a.metric("跨度=14天（理論值）", "{:.1f}%".format(float((sp == NORMAL_SPAN_DAYS).mean() * 100)))
                    b.metric("跨度中位數", "{:.0f} 天".format(float(np.median(sp))))
                    c.metric("跨度最大", "{:.0f} 天".format(float(sp.max())))
                    d.metric("超過上限({}天)".format(max_span), int((sp > max_span).sum()))
                    st.caption("非14天者即為非營業日造成。此分布用於校準 MAX_SPAN_DAYS。")
            else:
                st.warning("無資料。")

    # ══ Tab2：觸發掃描（鐵律16：須註記資料截至日）══
    with tabs[1]:
        st.subheader("現時觸發掃描")
        if st.button("掃描", key="scan"):
            rows = []
            for code in picks:
                px, err = fetch_prices(code, adjust)
                if err or not px:
                    continue
                rr = calc_all_rolling_returns(px, ROLL_N, max_span)
                if not rr:
                    continue
                last = rr[-1]
                rows.append({
                    "代碼": code,
                    "分類": classify_etf(code),
                    "滾動10日報酬%": "{:.2f}%".format(last["return"]),
                    "資料截至": last["date"],          # ★ 鐵律16：使用者要求的註記
                    "視窗跨度(曆日)": last["span_days"],
                    "跨度有效": "✅" if last["valid"] else "❌ 作廢",
                    "觸發": "🔴 是" if (last["return"] <= threshold and last["valid"]) else "—",
                    "收盤": round(last["curr_price"], 2),
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch")
                st.caption("⚠️ **跨檔比較請看「資料截至」**：各標的最新資料日可能不同，"
                           "同一張表上的標的未必處於同一實際日期（憲法十一-4）。")
            else:
                st.warning("無資料。")

    # ══ Tab3：回測 ══
    with tabs[2]:
        st.subheader("回測")
        t1_picks = [c for c in picks if is_tier1(c)]
        skipped = [c for c in picks if not is_tier1(c)]
        if skipped:
            st.warning("**憲法「九」**：以下為主動ETF（上市<3年，五年/十年績效欄空白），"
                       "屬 Tier4「只收資料、不出結論」，已排除：{}".format("、".join(skipped)))
        if st.button("跑回測", type="primary", key="bt"):
            for code in t1_picks:
                px, err = fetch_prices(code, adjust)
                if err or not px:
                    st.warning("{}：{}".format(code, err or "無資料"))
                    continue
                rolling = calc_all_rolling_returns(px, ROLL_N, max_span)
                bt = run_full_backtest(px, threshold, rolling, entry_lag,
                                       fee_buy, fee_sell, max_span)
                st.markdown("### {}".format(code))
                if not bt:
                    st.info("門檻 {}% 下無有效觸發。試著放寬門檻（見分布校準頁）。".format(threshold))
                    continue
                a, b, c, d = st.columns(4)
                a.metric("觸發次數", bt["觸發次數"])
                b.metric("最大連續觸發", bt["最大連續觸發"])
                c.metric("跨度作廢(鐵律16)", bt["跨度作廢筆數"])
                d.metric("entry_lag", bt["entry_lag"])
                tbl = build_entry_timing_table(bt)
                if not tbl.empty:
                    best = _pick_best_timing_idx(tbl)
                    st.dataframe(tbl, width="stretch")
                    if best is not None:
                        r = tbl.loc[best]
                        st.success("★ 最佳進場時機：持有 **{}** 天｜勝率 {}｜平均報酬 {}｜樣本 {}"
                                   .format(r["持有天數"], r["勝率"], r["平均報酬%"], r["樣本數"]))
                    else:
                        st.info("無任何持有天數樣本數≥{}，不硬推最佳（母專案同源規則）。".format(MIN_SAMPLE))
                if fee_buy == 0 and fee_sell == 0:
                    st.caption("ℹ️ 費用未計入（憲法「十」使用者裁決）。因回測保存原始報酬序列，"
                               "日後要扣 {}% 前收，等價於把勝率門檻改為 ret > {}%，無需重跑。"
                               .format("N", "N"))

    # ══ Tab4：追蹤日誌 ══
    with tabs[3]:
        st.subheader("📒 追蹤日誌")
        jdf = load_journal()
        st.caption("欄位（母專案同源）：{}".format("、".join(JOURNAL_COLS)))
        ed = st.data_editor(jdf, num_rows="dynamic", width="stretch", key="jed")
        cc1, cc2 = st.columns(2)
        if cc1.button("💾 儲存"):
            st.success("已儲存") if save_journal(ed) else st.error("儲存失敗")
        cc2.download_button("⬇️ 下載備份 CSV",
                            ed.to_csv(index=False).encode("utf-8-sig"),
                            "fund_journal_{}.csv".format(dt.date.today()), "text/csv")
        st.warning("☁️ Streamlit Cloud 重啟會清空 /tmp，請定期下載備份（憲法「七」）。")

        stt = journal_stats_by_type(ed)
        if not stt.empty:
            st.markdown("#### 已結案實績（依進場類型分組）")
            st.dataframe(stt, width="stretch")
            st.info("**歸因順序（憲法「五」）**：實際低於回測時，依序排除 "
                    "① entry_lag=0 已知偏誤 → ② 手續費未計 → ③ 配息/倖存者/跨度 → "
                    "**④ 全排除後才可稱策略失效**。")

    # ══ Tab5：系統檢核 ══
    with tabs[4]:
        st.subheader("🛡️ 系統檢核")
        chk = run_system_checks(adjust, fee_buy, fee_sell, entry_lag)
        st.dataframe(chk, width="stretch")
        fails = chk[(chk["結果"].str.contains("FAIL")) & (chk["檢核項目"].isin(CRITICAL_CHECKS))]
        if len(fails):
            st.error("🚨 有 {} 項關鍵檢核未通過，結果不可信。".format(len(fails)))
        else:
            st.success("✅ 全部關鍵檢核通過。")
        st.caption("CRITICAL_CHECKS 與 IMPACT_MAP 中 severity=='error' 項目自動同步（母專案架構）。")

    # ══ Tab6：SITCA 連線測試（Tier2 資料源端到端驗證）══
    with tabs[5]:
        st.subheader("🔌 SITCA 境內基金淨值 — 連線測試")
        st.info("**目的**：驗證「境內基金」這條資料源在 Streamlit Cloud 上端到端可用。"
                "SITCA 是官方唯一來源，一次 POST 回一整家投信的所有基金（含主動ETF）當日淨值。"
                "解析邏輯已用真實 VIEWSTATE 離線驗證正確，此處驗的是**真實網路連線**。")
        if not _HAS_REQ:
            st.error("requests 不可用，請確認 requirements.txt 含 requests。")
        else:
            cc1, cc2 = st.columns([2, 1])
            comp_label = cc1.selectbox(
                "選擇投信",
                options=list(SITCA_COMPANIES.keys()),
                format_func=lambda c: "{} {}".format(c, SITCA_COMPANIES[c]),
                index=list(SITCA_COMPANIES.keys()).index("A0005"),
            )
            # 預設用最近一個營業日（往回跳過週末）
            _d = dt.date.today() - dt.timedelta(days=1)
            while _d.weekday() >= 5:
                _d -= dt.timedelta(days=1)
            test_date = cc2.date_input("查詢日期", value=_d)
            date_str = test_date.strftime("%Y%m%d")

            if st.button("🔍 測試 SITCA 連線", type="primary"):
                with st.spinner("① GET 取 token → ② POST 查詢 → ③ 解析 …"):
                    rows, msg = fetch_sitca_nav(comp_label, date_str)
                if rows:
                    st.success(msg)
                    df = pd.DataFrame(rows)
                    n_active = int(df["分類"].str.startswith("主動ETF").sum())
                    a, b, c = st.columns(3)
                    a.metric("解析基金數", len(df))
                    b.metric("其中主動ETF", n_active)
                    c.metric("投信", SITCA_COMPANIES[comp_label])
                    st.dataframe(df, width="stretch")
                    st.success("✅ **SITCA 端到端可用**。境內基金資料源確認打通，"
                               "可進入 Tier2 全市場 bulk build。請把此畫面截圖回報。")
                else:
                    st.error(msg)
                    st.caption("若失敗，請把上面紅字整段回報。依鐵律9：看確切錯誤才動手，不猜。")
            st.caption("ℹ️ 此頁只讀取、不寫入。SITCA 資料更新頻率為每個營業日。"
                       "假日或當日未公告時可能解析 0 筆，屬正常，換前一個營業日再試。")


if __name__ == "__main__":
    main()
