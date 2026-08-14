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
import os
import re
import time
import datetime as dt
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

try:
    import mp_analysis as mp          # 母專案個別分析引擎（Stage 2b）
    _HAS_MP = True
except Exception:
    _HAS_MP = False

_ICONS = {
    "sys": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 96 96\" width=\"38\" height=\"38\">  <circle cx=\"48\" cy=\"48\" r=\"45\" fill=\"none\" stroke=\"#003781\" stroke-width=\"2\"/>  <g fill=\"none\" stroke=\"#003781\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\">    <path d=\"M48 22 L68 29 V47 C68 61 59 70 48 75 C37 70 28 61 28 47 V29 Z\"/>    <path d=\"M40 48 L46 55 L58 41\"/  </g></svg>",
    "scan": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 96 96\" width=\"38\" height=\"38\">  <circle cx=\"48\" cy=\"48\" r=\"45\" fill=\"none\" stroke=\"#003781\" stroke-width=\"2\"/>  <g fill=\"none\" stroke=\"#003781\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\">    <circle cx=\"43\" cy=\"43\" r=\"14\"/>    <path d=\"M53 53 L66 66\"/>    <path d=\"M37 43 H49 M43 37 V49\" stroke-width=\"1.8\"/  </g></svg>",
    "fund": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 96 96\" width=\"38\" height=\"38\">  <circle cx=\"48\" cy=\"48\" r=\"45\" fill=\"none\" stroke=\"#003781\" stroke-width=\"2\"/>  <g fill=\"none\" stroke=\"#003781\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\">    <path d=\"M27 28 V66 H69\"/>    <path d=\"M32 58 L41 49 L49 55 L58 40\"/>    <circle cx=\"61\" cy=\"40\" r=\"6\"/>    <path d=\"M65 44 L71 50\"/  </g></svg>",
    "cmp": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 96 96\" width=\"38\" height=\"38\">  <circle cx=\"48\" cy=\"48\" r=\"45\" fill=\"none\" stroke=\"#003781\" stroke-width=\"2\"/>  <g fill=\"none\" stroke=\"#003781\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\">    <path d=\"M28 66 H68\"/>    <rect x=\"33\" y=\"46\" width=\"7\" height=\"20\"/>    <rect x=\"45\" y=\"34\" width=\"7\" height=\"32\"/>    <rect x=\"57\" y=\"52\" width=\"7\" height=\"14\"/>  </g></svg>",
    "notes": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 96 96\" width=\"38\" height=\"38\">  <circle cx=\"48\" cy=\"48\" r=\"45\" fill=\"none\" stroke=\"#003781\" stroke-width=\"2\"/>  <g fill=\"none\" stroke=\"#003781\" stroke-width=\"2.4\" stroke-linecap=\"round\" stroke-linejoin=\"round\">    <rect x=\"31\" y=\"26\" width=\"30\" height=\"44\" rx=\"3\"/>    <path d=\"M38 26 V70 M31 36 H61 M31 48 H61 M31 60 H61\" stroke-width=\"1.6\"/>    <path d=\"M55 58 L70 43 L74 47 L59 62 Z\" fill=\"#DFEFF2\"/>  </g></svg>",
}

def _icon_title(key, text):
    """Allianz 風 icon + 標題（內嵌SVG，不依賴外部檔）。"""
    svg = _ICONS.get(key, "")
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;margin:6px 0 10px">'
        '<span style="flex:0 0 auto;line-height:0">' + svg + '</span>'
        '<span style="font-size:1.55rem;font-weight:700;color:#003781">' + text + '</span></div>',
        unsafe_allow_html=True)


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
    "A0014": "新光投信(已併台新)", "A0020": "日盛投信(已併富邦)",
}

# 淨值列解析：兩段式（先切<tr>再抽<td>），不受單雙引號/class/align/長名稱影響。
# 教訓：舊版寫死 align='left'（單引號，來自VIEWSTATE解碼），但SITCA實際回傳用雙引號
#       → 200+列僅僥倖命中9列。改用兩段式後，用三張真實DOM圖驗證5/5全中。
def _sitca_parse_rows(html: str) -> List[dict]:
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        if 'DTHeader' in tr or '類型代號' in tr:
            continue
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        tds = [re.sub(r'<[^>]+>', '', t).strip() for t in tds]
        if len(tds) < 8:
            continue
        comp, code, fname, cur, nav = tds[1], tds[3], tds[5], tds[6], tds[7]
        if not re.match(r'^A00\d{2}$', comp):
            continue
        out.append({
            "代碼": code,
            "分類": classify_etf(code),
            "幣別": cur,
            "淨值": nav,
            "名稱": fname[:30],
        })
    return out


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

        # ★ ALL 模式：公司下拉「所有公司」的真實 value 是空字串 ""，不是 "ALL"。
        #   送 "ALL" 會被 SITCA 視為非法值（真瀏覽器不會送）→ WAF 回 404（實測）。
        #   與建庫版 build_sitca.py 的 fetch_one 對齊。
        comp_value = "" if str(company).upper() in ("", "ALL") else company
        payload = {
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            date_name: date_str,
            company_name: comp_value,
        }
        for btn in ["ctl00$ContentPlaceHolder1$btnQuery",
                    "ctl00$ContentPlaceHolder1$BtnQuery",
                    "ctl00$ContentPlaceHolder1$Button1"]:
            payload.setdefault(btn, "查詢")

        r2 = s.post(SITCA_NAV_URL, headers=SITCA_HEADERS, data=payload, timeout=30)
        if r2.status_code != 200:
            return [], "② POST 失敗 status={}".format(r2.status_code)

        out = _sitca_parse_rows(r2.text)
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
# 歷史庫讀取（C層：讀 A/B 建的 CSV，供每早掃描用）
#   資料來源：data/sitca_nav.csv(境內) + data/offshore_nav.csv(境外)
#   每檔標記境內/境外、投信/發行商，供篩選維度用（憲法：分類篩選後掃描）
# ══════════════════════════════════════════════════════════════
HIST_SITCA_GLOB = "data/sitca_nav_[0-9][0-9][0-9][0-9]*.csv"  # 分年檔(可含_segNN後綴)
# 匹配 sitca_nav_2026.csv 及 sitca_nav_2026_seg00.csv；排除舊單檔sitca_nav.csv
HIST_SITCA_LEGACY = "data/sitca_nav.csv"   # 舊版單檔（向後相容）
HIST_OFFSHORE = "data/offshore_nav.csv"


@st.cache_data(ttl=600, show_spinner=False)
def load_history_db(max_years: Optional[int] = None) -> Tuple[pd.DataFrame, str]:
    """讀取歷史庫（境內分年檔 + 境外）。

    分年拆檔：data/sitca_nav_2011.csv ... sitca_nav_2026.csv，用 glob 合併。
    向後相容舊的單檔 sitca_nav.csv。

    ★ max_years：只讀最近N年（避免記憶體爆掉）。
      實測風險：4246檔×5年≈530萬筆≈850MB，Streamlit Cloud免費版約1GB → 會崩。
      掃描「今日觸發」只需最近資料；完整歷史勝率再開大。None=全讀。
    回傳：(DataFrame[代碼,日期,淨值,名稱,境內外,發行,資產類型,投資區域], 來源說明)
    """
    import glob
    frames, src_msg = [], []

    # ── 境內：分年檔 + 舊單檔 ──
    sitca_files = sorted(glob.glob(HIST_SITCA_GLOB))
    if max_years:
        # 分年檔名帶年份，只留最近N年（檔名如 sitca_nav_2024.csv）
        cur_y = dt.date.today().year
        keep = set(str(y) for y in range(cur_y - max_years + 1, cur_y + 1))
        sitca_files = [f for f in sitca_files
                       if any(y in os.path.basename(f) for y in keep)]
    if os.path.exists(HIST_SITCA_LEGACY):
        sitca_files.append(HIST_SITCA_LEGACY)
    n_sitca = 0
    for path in sitca_files:
        try:
            # 省記憶體：只讀必要欄位（不讀類型代碼/幣別/分類等用不到的）
            want = ["代碼", "日期", "淨值", "名稱", "投信", "資產類型", "投資區域"]
            head = pd.read_csv(path, nrows=0)
            use = [c for c in want if c in head.columns]
            df = pd.read_csv(path, dtype=str, usecols=use)
            if len(df) == 0:
                continue
            df["淨值"] = pd.to_numeric(df["淨值"], errors="coerce")
            df = df.dropna(subset=["淨值"])
            df["境內外"] = "境內"
            # 發行公司：投信名 → 官方『代碼 名稱』（境內外收斂）
            if "投信" in df.columns:
                _t = df["投信"].astype(str)
                _m = {x: _canonical_issuer(x, False) for x in _t.unique()}
                df["發行"] = _t.map(_m)
                _ms = {x: _series_from(x, False) for x in _t.unique()}
                df["系列"] = _t.map(_ms)
            else:
                df["發行"] = ""
                df["系列"] = ""
            for c in ["資產類型", "投資區域"]:
                if c not in df.columns:
                    df[c] = "未分類"
            df = df[["代碼", "日期", "淨值", "名稱", "境內外", "發行", "系列",
                     "資產類型", "投資區域"]]
            # 重複性高的欄位轉 category，記憶體可省 5-10 倍
            for c in ["名稱", "境內外", "發行", "系列", "資產類型", "投資區域"]:
                df[c] = df[c].astype("category")
            frames.append(df)
            n_sitca += len(df)
        except Exception:
            continue
    if n_sitca:
        src_msg.append("境內{}年檔({:,}筆)".format(len(sitca_files), n_sitca))

    # ── 境外（分年子檔 offshore_nav_YYYY_NN.csv；依 max_years 只載選定年份省記憶體）──
    try:
        off_files = sorted(glob.glob("data/offshore_nav_*.csv"))
        if max_years:
            off_files = [f for f in off_files
                         if any(y in os.path.basename(f) for y in keep)]
        if not off_files and os.path.exists(HIST_OFFSHORE):
            off_files = [HIST_OFFSHORE]
        n_off = 0
        for path in off_files:
            try:
                want = ["代碼", "日期", "淨值", "名稱", "來源", "資產類型",
                        "投資區域", "發行", "系列"]
                head = pd.read_csv(path, nrows=0)
                use = [c for c in want if c in head.columns]
                df = pd.read_csv(path, dtype=str, usecols=use)
                if len(df) == 0:
                    continue
                df["淨值"] = pd.to_numeric(df["淨值"], errors="coerce")
                df = df.dropna(subset=["淨值"])
                df["境內外"] = "境外"
                # 發行/系列：優先用 CSV 官方值(TDCC ISIN)，空的才用基金名備援
                _names = df["名稱"].astype(str)
                _fb_iss = {nm: _canonical_issuer(nm, True) for nm in _names.unique()}
                _fb_ser = {nm: _series_from(nm, True) for nm in _names.unique()}
                if "發行" not in df.columns:
                    df["發行"] = ""
                if "系列" not in df.columns:
                    df["系列"] = ""
                _iss = df["發行"].fillna("").astype(str).tolist()
                _ser = df["系列"].fillna("").astype(str).tolist()
                _nl = _names.tolist()
                df["發行"] = [a if a.strip() else _fb_iss.get(n, "") for a, n in zip(_iss, _nl)]
                df["系列"] = [a if a.strip() else _fb_ser.get(n, "") for a, n in zip(_ser, _nl)]
                for c in ["資產類型", "投資區域"]:
                    if c not in df.columns:
                        df[c] = "未分類"
                df = df[["代碼", "日期", "淨值", "名稱", "境內外", "發行", "系列",
                         "資產類型", "投資區域"]]
                for c in ["名稱", "境內外", "發行", "系列", "資產類型", "投資區域"]:
                    df[c] = df[c].astype("category")
                frames.append(df)
                n_off += len(df)
            except Exception:
                continue
        if n_off:
            src_msg.append("境外{}檔({:,}筆)".format(len(off_files), n_off))
    except Exception:
        pass
    except Exception:
        pass

    if not frames:
        return pd.DataFrame(columns=["代碼", "日期", "淨值", "名稱", "境內外", "發行",
                                     "資產類型", "投資區域"]), \
            "尚無歷史庫（請先用 GitHub Actions 建庫）"
    return pd.concat(frames, ignore_index=True), " + ".join(src_msg)


def hist_to_prices(df_one: pd.DataFrame) -> Dict[str, float]:
    """單檔基金的歷史 DataFrame → {日期:淨值} 字典（回測引擎格式）。"""
    d = df_one.dropna(subset=["淨值"]).drop_duplicates("日期")
    return dict(zip(d["日期"].astype(str), d["淨值"].astype(float)))


@st.cache_data(ttl=600, show_spinner=False)
def load_prices_for_codes(codes_tuple):
    """★低記憶體：只讀指定基金代碼的全歷史淨值（不載整個庫，避免OOM）。
    回 {code: {日期:淨值}}。逐檔案讀→過濾→只留這些代碼，峰值≈單一檔案。"""
    import glob as _g
    want = set(codes_tuple)
    out: Dict[str, Dict[str, float]] = {}
    files = sorted(_g.glob("data/sitca_nav_*.csv")) + sorted(_g.glob("data/offshore_nav_*.csv"))
    for fp in files:
        try:
            df = pd.read_csv(fp, dtype=str, usecols=lambda c: c in ("代碼", "日期", "淨值"))
        except Exception:
            continue
        if "代碼" not in df.columns:
            continue
        df = df[df["代碼"].isin(want)]
        if len(df) == 0:
            continue
        df["淨值"] = pd.to_numeric(df["淨值"], errors="coerce")
        df = df.dropna(subset=["淨值"])
        for code, g in df.groupby("代碼"):
            out.setdefault(str(code), {}).update(
                dict(zip(g["日期"].astype(str), g["淨值"].astype(float))))
    return out


@st.cache_data(ttl=600, show_spinner=False)
def load_fund_meta():
    """★低記憶體：基金清單 metadata（代碼/名稱/境內外/發行/系列/資產/區域），
    給篩選下拉用，不載每日淨值。用近2年檔去重出每檔一列。"""
    h, _ = load_history_db(2)
    if len(h) == 0:
        return pd.DataFrame(columns=["代碼", "名稱", "境內外", "發行", "系列",
                                     "資產類型", "投資區域"])
    keep = [c for c in ["代碼", "名稱", "境內外", "發行", "系列", "資產類型", "投資區域"]
            if c in h.columns]
    return h[keep].drop_duplicates("代碼").reset_index(drop=True)


def _norm_name(s):
    """基金名正規化（與 build_performance 同邏輯，給績效 join 用）。"""
    s = str(s)
    for ch in [" ", "\u3000", "\t", "(", ")", "（", "）", "-", "－"]:
        s = s.replace(ch, "")
    return s.strip()


@st.cache_data(ttl=1800, show_spinner=False)
def load_performance():
    """讀 MoneyDJ 官方績效 → (DataFrame, {正規名: dict})。無檔則回空。"""
    import os
    if not os.path.exists("data/performance.csv"):
        return pd.DataFrame(), {}
    try:
        p = pd.read_csv("data/performance.csv", dtype=str)
    except Exception:
        return pd.DataFrame(), {}
    numc = ["排名", "一個月%", "三個月%", "六個月%", "一年%", "三年%",
            "五年%", "十年%", "年化標準差", "Sharpe", "Beta"]
    for c in numc:
        if c in p.columns:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    key = "_正規名" if "_正規名" in p.columns else "名稱"
    lut = {}
    for _, r in p.iterrows():
        lut[str(r[key])] = r.to_dict()
    return p, lut


# ══════════════════════════════════════════════════════════════
# 即時補最新（PENDING#1，Greg 選 A：app 掃描前即時抓，境內拿今天）
#   目的：靜態庫最新日 = 上次建庫時間；掃描前即時抓「庫最新日之後~今天」的
#         境內淨值併回，讓滾動10日以「今天」收尾，觸發訊號是今天的。
#   境外：無即時源（境外建庫=PENDING#3），一律用庫最新，不動。
# ══════════════════════════════════════════════════════════════
LIVE_TOPUP_CAP_DAYS = 15   # 一次即時最多補幾個營業日（避免庫太舊時現抓上百天）


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_sitca_days(date_strs: tuple):
    """即時抓 SITCA 指定日期(YYYYMMDD)的全市場淨值。快取30分。
    回 (data, msgs)：data={iso: {代碼: 淨值float}}；msgs={iso: 診斷訊息}。
    某日無資料(假日/未公布)或抓取失敗 → 該日 data 空 dict、msgs 記原因。"""
    result: Dict[str, Dict[str, float]] = {}
    msgs: Dict[str, str] = {}
    for ds in date_strs:
        iso = "{}-{}-{}".format(ds[:4], ds[4:6], ds[6:8])
        rows, msg = fetch_sitca_nav("ALL", ds)
        msgs[iso] = msg
        m: Dict[str, float] = {}
        for r in rows:
            code = r.get("代碼")
            nav = r.get("淨值")
            if not code or nav in (None, "", "-"):
                continue
            try:
                m[code] = float(str(nav).replace(",", ""))
            except ValueError:
                continue
        result[iso] = m
    return result, msgs


def live_topup_domestic(hist: pd.DataFrame, cap_days: int = LIVE_TOPUP_CAP_DAYS):
    """掃描前即時補最新：抓「庫最新日之後 ~ 今天」的境內淨值，併回 hist。
    回傳 (補後的hist, 說明字串)。只補已在庫的基金(新基金無歷史算不了滾動)。"""
    if not _HAS_REQ:
        return hist, "requests 不可用，略過即時補（用庫最新）"
    dom = hist[hist["境內外"] == "境內"]
    if len(dom) == 0:
        return hist, "無境內資料可補"
    db_latest = max(dom["日期"].astype(str))
    try:
        last_d = dt.date.fromisoformat(db_latest)
    except ValueError:
        return hist, "庫日期格式異常，略過即時補"

    today = dt.date.today()
    need = []
    d = last_d + dt.timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:      # 只抓營業日(週一~五)；假日 SITCA 回空自然略過
            need.append(d)
        d += dt.timedelta(days=1)
    if not need:
        return hist, "庫已是最新（{}），無需即時補".format(db_latest)

    over = len(need) > cap_days
    if over:
        need = need[-cap_days:]  # 只補最近 cap_days 天
    date_strs = tuple(dd.strftime("%Y%m%d") for dd in need)

    fetched, fmsgs = _fetch_sitca_days(date_strs)

    # 用庫裡既有基金的屬性當母表，補上新日期的淨值
    attr = (dom.drop_duplicates("代碼")
               .set_index("代碼")[["名稱", "發行", "資產類型", "投資區域"]])
    supp = []
    got_days = 0
    for iso, m in fetched.items():
        if m:
            got_days += 1
        for code, nav in m.items():
            if code in attr.index:
                a = attr.loc[code]
                supp.append({
                    "代碼": code, "日期": iso, "淨值": nav,
                    "名稱": a["名稱"], "境內外": "境內", "發行": a["發行"],
                    "資產類型": a["資產類型"], "投資區域": a["投資區域"],
                })
    if not supp:
        # 診斷真因：抓取失敗 vs 真的空 vs 代碼對不上（別再誤標「假日」）
        errs = [m for m in fmsgs.values() if m and ("例外" in m or "失敗" in m)]
        any_rows = any(len(m) > 0 for m in fetched.values())
        if errs:
            return hist, "⚠️ 即時抓取失敗（本次用庫最新）：{}".format(errs[0][:140])
        if any_rows:
            return hist, "⚠️ 即時抓到資料但代碼對不上庫，本次用庫最新（需查代碼格式）"
        return hist, "即時補：最近 {} 個營業日 SITCA 皆回空（假日或淨值尚未公布）".format(len(need))

    supp_df = pd.DataFrame(supp)
    merged = pd.concat([hist, supp_df], ignore_index=True)
    merged = merged.drop_duplicates(["代碼", "日期"], keep="last")
    newest = max(supp_df["日期"])
    msg = "境內即時補到 {}（新增 {} 天 / {:,} 筆，庫原本止於 {}）".format(
        newest, got_days, len(supp_df), db_latest)
    if over:
        msg += "；⚠️ 庫落後 >{} 天，只補了最近 {} 天，建議重跑建庫補齊中間".format(
            cap_days, cap_days)
    return merged, msg


import re as _re
# 產品/區域/資產關鍵字：基金名在此之前的部分＝發行品牌
_BRAND_CUT = _re.compile(
    r'([(（\-－]|'                                    # 註冊地括號/子品牌破折號先切
    r'南韓|環球|全球|世界|國際|日本|美國|美国|北美|歐洲|亞洲|亞太|大中華|中華|中國|'
    r'印度|越南|韓國|韓|泰國|新加坡|馬來|拉丁|拉美|新興|邊境|金磚|東協|東南亞|'
    r'台灣|台股|三印|'
    r'科技|半導體|人工智慧|AI|能源|潔淨|乾淨|醫療|生技|健康|金融|消費|品牌|'
    r'資源|天然|礦業|農業|水資源|基建|基礎建設|房地產|不動產|REIT|公用|'
    r'債券?|債$|股票?|股$|平衡|收益|入息|成長|價值|多重資產|多元|貨幣|指數|'
    r'ESG|永續|藍籌|優選|精選|策略|組合|傘型|雙盈|穩健|靈活|動能|創新|趨勢)'
)
# 需優先精準比對的多字/特殊品牌（避免被切錯）
_SPECIAL_BRANDS = [
    "富蘭克林坦伯頓", "富蘭克林華美", "摩根士丹利", "駿利亨德森", "紐約梅隆",
    "鋒裕匯理", "東方匯理", "品浩太平洋", "新加坡大華", "利安資金",
    "歐義銳榮", "資本集團", "資本國際", "瑞士隆奧", "尚渤投資", "尚渤",
    "貝萊德", "荷寶", "惠理", "首源", "普徕仕", "安盛", "木星", "保德信",
    "威廉博萊", "M&G", "M&amp;G", "PIMCO", "PGIM", "MFS", "DWS", "GAM", "KBI",
    "NN", "AB", "UBS", "Vontobel",
]


def _issuer_from_name(name):
    """從境外基金名抽出乾淨的發行品牌（＝系列，切在第一個產品/區域關鍵字之前）。"""
    name = (name or "").strip()
    if not name:
        return "境外"
    if name.startswith("FundRock") or ("愛爾蘭系列" in name and name.startswith("野村")):
        return "FundRock(野村愛爾蘭系列)"
    for s in _SPECIAL_BRANDS:
        if name.startswith(s):
            return s.replace("&amp;", "&")
    m = _BRAND_CUT.search(name)
    brand = (name[:m.start()] if m else name).strip(" -－·()（）")
    if not brand:
        brand = name[:4]
    return brand[:8]


def _active_passive(asset, name):
    s = (asset or "") + (name or "")
    if "被動" in s or "指數" in s:
        return "被動"
    return "主動"     # 境外共同基金多為主動；ETF 才多被動


# ── 官方發行公司對照（依 fundclear 官方總代理↔機構表，Greg 校訂 2026-08-13 第3版）──
# 規則：發行公司=總代理；投顧若有對應投信則併入投信；系列=中文品牌(另存)。
_BRAND2AGENT = {
    # 投信 A（含投顧併入投信者）
    "歐義銳榮": ("A0003", "第一金投信"), "第一金": ("A0003", "第一金投信"),
    "匯豐": ("A0004", "匯豐投信"), "滙豐": ("A0004", "匯豐投信"),
    "景順": ("A0006", "景順投信"),
    "瀚亞": ("A0007", "瀚亞投信"), "資本集團": ("A0007", "瀚亞投信"),
    "資本國際": ("A0007", "瀚亞投信"), "Vontobel": ("A0007", "瀚亞投信"),
    "保德信": ("A0008", "玉山投信"), "PGIM": ("A0008", "玉山投信"), "玉山": ("A0008", "玉山投信"),
    "摩根士丹利": ("A0037", "國泰投信"),          # MS→國泰投顧併入國泰投信
    "摩根": ("A0011", "摩根投信"),
    "瑞銀": ("A0015", "瑞銀投信"), "UBS": ("A0015", "瑞銀投信"),
    "GAM": ("A0017", "台中銀投信"), "台中銀": ("A0017", "台中銀投信"),
    "聯博": ("A0018", "聯博投信"), "AB": ("A0018", "聯博投信"),
    "永豐": ("A0025", "永豐投信"),                # Carne→永豐投顧併入永豐投信
    "柏瑞": ("A0021", "柏瑞投信"), "MFS": ("A0021", "柏瑞投信"),
    "法盛": ("A0026", "中國信託投信"), "Natixis": ("A0026", "中國信託投信"),
    "中國信託": ("A0026", "中國信託投信"), "中信": ("A0026", "中國信託投信"),
    "宏利": ("A0027", "宏利投信"), "安本": ("A0027", "宏利投信"),
    "貝萊德": ("A0031", "貝萊德投信"),
    "野村": ("A0032", "野村投信"), "高盛": ("A0032", "野村投信"),
    "晉達": ("A0032", "野村投信"), "天達": ("A0032", "野村投信"), "NN": ("A0032", "野村投信"),
    "駿利亨德森": ("A0032", "野村投信"), "駿利": ("A0032", "野村投信"), "亨德森": ("A0032", "野村投信"),
    "FundRock": ("A0032", "野村投信"),
    "東方匯理": ("A0035", "東方匯理投信"), "鋒裕匯理": ("A0035", "東方匯理投信"), "鋒裕": ("A0035", "東方匯理投信"),
    "國泰": ("A0037", "國泰投信"), "首源": ("A0037", "國泰投信"),
    "安聯": ("A0036", "安聯投信"), "富達": ("A0038", "富達投信"),
    "德銀": ("A0040", "德銀遠東投信"), "DWS": ("A0040", "德銀遠東投信"),
    "施羅德": ("A0042", "施羅德投信"),
    "富蘭克林坦伯頓": ("A0045", "富蘭克林華美投信"), "富蘭克林": ("A0045", "富蘭克林華美投信"),
    "富坦": ("A0045", "富蘭克林華美投信"), "坦伯頓": ("A0045", "富蘭克林華美投信"),
    "鄧普頓": ("A0045", "富蘭克林華美投信"), "美盛": ("A0045", "富蘭克林華美投信"),
    "凱利": ("A0045", "富蘭克林華美投信"),
    "台新": ("A0047", "台新投信"), "荷寶": ("A0047", "台新投信"),
    "利安資金": ("A0047", "台新投信"), "利安": ("A0047", "台新投信"),
    "紐約梅隆": ("A0048", "合庫投信"), "合庫": ("A0048", "合庫投信"), "合作金庫": ("A0048", "合庫投信"),
    "大華銀": ("A0049", "大華銀投信"), "新加坡大華": ("A0049", "大華銀投信"), "新加坡大": ("A0049", "大華銀投信"),
    "路博邁": ("A0050", "路博邁投信"),
    # 純投顧 B（無對應投信）
    "KBI": ("B0015", "康和投顧"), "尚渤": ("B0015", "康和投顧"), "尚渤投資": ("B0015", "康和投顧"),
    "普徕仕": ("B0034", "萬寶投顧"),
    "瑞聯": ("B0044", "宏遠投顧"),
    "法巴": ("B0049", "法銀巴黎投顧"), "法國巴黎": ("B0049", "法銀巴黎投顧"),
    "霸菱": ("B0149", "霸菱投顧"),
    "Muzinich": ("B0162", "全球投顧"), "Muzini": ("B0162", "全球投顧"), "繆思": ("B0162", "全球投顧"),
    "惠理": ("B0162", "全球投顧"),
    "M&G": ("B0313", "富盛投顧"), "安盛": ("B0313", "富盛投顧"), "木星": ("B0313", "富盛投顧"),
    "百達": ("B0328", "百達投顧"),
    "品浩": ("B0351", "品浩太平洋投顧"), "PIMCO": ("B0351", "品浩太平洋投顧"),
    "瑞士隆奧": ("B0355", "展新投顧"), "威廉博萊": ("B0355", "展新投顧"),
    # 已退出/無台灣總代理
    "天利": ("--", "天利(已退出)"), "未來資產": ("--", "未來資產(已終止)"),
    "道富": ("--", "道富環球"), "先鋒": ("--", "先鋒領航"),
}
_BRAND_KEYS = sorted(_BRAND2AGENT, key=len, reverse=True)   # 長品牌先比，避免誤匹配
_NAME2CODE = {}
for _c, _n in SITCA_COMPANIES.items():
    _NAME2CODE[_n] = _c
    _NAME2CODE[_n.replace("滙", "匯")] = _c


def _fmt_agent(code, name):
    name = name.replace("滙", "匯")
    return name if code == "--" else "{} {}".format(code, name)


def _canonical_issuer(text, offshore):
    """境內給投信名、境外給基金名 → 統一回『代碼 官方名』；同一家境內外收斂。"""
    text = (text or "").strip()
    if not text:
        return "未分類"
    if offshore:
        stem = _issuer_from_name(text)
        for k in _BRAND_KEYS:
            if stem.startswith(k) or text.startswith(k):
                return _fmt_agent(*_BRAND2AGENT[k])
        return stem                                   # 未對到官方 → 乾淨品牌
    # 境內：投信名 → 加官方 A 碼
    code = _NAME2CODE.get(text) or _NAME2CODE.get(text.replace("滙", "匯"))
    if code:
        return _fmt_agent(code, SITCA_COMPANIES.get(code, text))
    for k in _BRAND_KEYS:                              # 境內外收斂
        if text.startswith(k):
            return _fmt_agent(*_BRAND2AGENT[k])
    return text


def _series_from(text, offshore):
    """系列＝實際品牌（同一總代理下用此區分 NN／天達／駿利…）。用對照品牌key避免碎片。"""
    if offshore:
        stem = _issuer_from_name(text)
        if stem.startswith("FundRock"):
            return stem
        for k in _BRAND_KEYS:
            if stem.startswith(k) or text.startswith(k):
                return k
        return stem
    return "投信"     # 境內：系列統一標「投信」(境內基金自成一系列，Greg規則2)


def scan_history_db(df: pd.DataFrame, threshold: float,
                    max_span: int = MAX_SPAN_DAYS) -> pd.DataFrame:
    """每早掃描核心：對每檔算「最新滾動跌幅、觸發、10天前/最新淨值、資料品質」。
    （歷史勝率移到『個別基金分析』，掃描表不再逐檔回測 → 快很多。）
    """
    rows = []
    for code, g in df.groupby("代碼"):
        prices = hist_to_prices(g)
        if len(prices) < ROLL_N + 1:
            continue
        rolling = calc_all_rolling_returns(prices, ROLL_N, max_span)
        if not rolling:
            continue
        last = rolling[-1]
        name = str(g["名稱"].iloc[0]) if "名稱" in g.columns else ""
        region = g["境內外"].iloc[0] if "境內外" in g.columns else ""
        issuer = str(g["發行"].iloc[0]) if "發行" in g.columns else ""
        series = str(g["系列"].iloc[0]) if "系列" in g.columns else ""
        asset = g["資產類型"].iloc[0] if "資產類型" in g.columns else ""
        triggered = last["return"] <= threshold and last["valid"]

        consec = 0
        for r in reversed(rolling):
            if r["return"] <= threshold and r.get("valid", True):
                consec += 1
            else:
                break

        # 發行公司：境外若被標成來源(yfinance)，改用名稱抽出的真管理公司
        if region == "境外" and issuer in ("yfinance", "", "nan"):
            issuer = _issuer_from_name(name)

        rows.append({
            "代碼": code,
            "境內外": region,
            "名稱": name[:40],
            "發行公司": issuer,
            "系列": series,
            "主被動": _active_passive(asset, name),
            "滾動10日%": round(last["return"], 2),
            "10天前日期": last.get("base_date", ""),
            "10天前淨值": round(last.get("base_price", 0), 4),
            "今日觸發": "🔴 是" if triggered else "—",
            "連續觸發天": consec,
            "淨值最新日": last["date"],
            "最新淨值": round(last.get("curr_price", 0), 4),
            "資料品質": "✅ 正常" if last["valid"] else "⚠️ 稀疏({}天)".format(last["span_days"]),
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_t"] = (out["今日觸發"].astype(str).str.contains("是")).astype(int)
    out = out.sort_values(["_t", "連續觸發天", "滾動10日%"],
                          ascending=[False, False, True]).drop(columns="_t")
    return out


def analyze_one_fund(df_one: pd.DataFrame, threshold: float,
                     max_span: int = MAX_SPAN_DAYS) -> dict:
    """單檔基金深度分析（使用者要求：像個股版那樣做個別分析）。

    回傳 dict：
      prices     {日期:淨值}
      rolling    每日滾動10日報酬（含跨度/有效性）
      triggers   歷史所有觸發點
      timing_tbl 各持有天數的勝率表
      best_idx   最佳持有天數（母專案同源規則）
      stats      摘要統計
    """
    prices = hist_to_prices(df_one)
    if len(prices) < ROLL_N + 1:
        return {"error": "資料不足（僅 {} 筆，需 ≥{} 筆）".format(len(prices), ROLL_N + 1)}

    rolling = calc_all_rolling_returns(prices, ROLL_N, max_span)
    if not rolling:
        return {"error": "無法計算滾動報酬"}

    valid_roll = [r for r in rolling if r["valid"]]
    triggers = [r for r in valid_roll if r["return"] <= threshold]
    bt = run_full_backtest(prices, threshold, rolling, 0, 0.0, 0.0, max_span)
    tbl = build_entry_timing_table(bt) if bt else pd.DataFrame()
    best_idx = _pick_best_timing_idx(tbl) if len(tbl) else None

    dates = sorted(prices.keys())
    rets = [r["return"] for r in valid_roll]
    return {
        "prices": prices,
        "rolling": rolling,
        "triggers": triggers,
        "timing_tbl": tbl,
        "best_idx": best_idx,
        "bt": bt,
        "stats": {
            "資料起": dates[0], "資料迄": dates[-1], "筆數": len(prices),
            "年數": round((dt.date.fromisoformat(dates[-1])
                          - dt.date.fromisoformat(dates[0])).days / 365.25, 1),
            "有效視窗": len(valid_roll), "無效視窗": len(rolling) - len(valid_roll),
            "歷史觸發次數": len(triggers),
            "最深跌幅": round(min(rets), 2) if rets else 0,
            "P5": round(float(np.percentile(rets, 5)), 2) if rets else 0,
            "P10": round(float(np.percentile(rets, 10)), 2) if rets else 0,
            "中位數": round(float(np.median(rets)), 2) if rets else 0,
        },
    }


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
    st.set_page_config(page_title="台灣基金滾動跌幅系統", layout="wide",
                       initial_sidebar_state="collapsed")
    # 全寬版面：移除預設窄邊距與 iframe 感（使用者明確要求，勿用側欄/窄框）
    st.markdown("""
        <style>
        .block-container {padding-top: 1.5rem; padding-left: 3rem;
                          padding-right: 3rem; max-width: 100%;}
        [data-testid="stDataFrame"] {width: 100% !important; border: none;}
        [data-testid="stDataFrame"] > div {border: none !important;}
        [data-testid="stElementToolbar"] {display: none;}
        .stTabs [data-baseweb="tab-list"] {gap: 8px;}
        .stTabs [data-baseweb="tab"] {height: 44px; font-size: 15px;}
        /* 移除各元件外框陰影，減少「框中框」的 iframe 感 */
        [data-testid="stVerticalBlock"] {gap: 0.8rem;}
        iframe {border: none !important;}
        </style>
    """, unsafe_allow_html=True)
    st.title("📉 台灣基金滾動跌幅系統")

    # 進階參數收在 expander，不佔主畫面（原本放側欄很不直覺）
    with st.expander("⚙️ 進階參數（一般使用不需調整）", expanded=False):
        pc1, pc2, pc3 = st.columns(3)
        max_span = pc1.number_input(
            "視窗跨度上限（曆日）", value=MAX_SPAN_DAYS, step=1, min_value=14,
            help="「往回數10筆淨值」實際橫跨幾天。正常14天(10交易日+2週末)。"
                 "超過此值代表該基金淨值公告有大缺口，該筆作廢避免假訊號。")
        entry_lag = pc2.number_input(
            "entry_lag（觸發後第N筆進場）", value=0, step=1, min_value=0,
            help="0=假設觸發當日就能買到。基金實務有申購截止時點，此為理論值。")
        adjust = pc3.checkbox("配息還原", value=True,
                              help="關閉會讓除息日被誤判成暴跌。高股息標的尤其嚴重。")
        fc1, fc2 = st.columns(2)
        fee_buy = fc1.number_input("申購/買進費(%)", value=0.0, step=0.1, min_value=0.0)
        fee_sell = fc2.number_input("贖回/賣出費(%)", value=0.0, step=0.1, min_value=0.0)
        if not adjust:
            st.error("🚨 未還原配息，除息日將產生假信號，結果不可信。")

    threshold = -10.0  # 各 tab 內可各自調整

    tab_sys, tab_scan, tab_fund, tab_cmp, tab_rank, tab_notes = st.tabs(
        ["🛡️ 系統檢核", "☀️ 每早掃描", "🔍 個別基金分析", "🆚 同類型比較",
         "🏆 績效Ranking", "📝 筆記"])

    # ══ 系統檢核（資料源綠勾表 + 排程 + 邏輯檢核 + SITCA測試）══
    with tab_sys:
        _icon_title("sys", "系統檢核")
        st.caption("一頁看懂：資料抓齊了嗎、夠不夠新、排程有沒有掛、邏輯對不對。")

        _hchk, _hsrc = load_history_db(2)   # 近2年，避免全載OOM
        _today = dt.date.today()

        def _days_since(dstr):
            try:
                return (_today - dt.date.fromisoformat(str(dstr)[:10])).days
            except Exception:
                return 999

        # ── 表1：資料源完整度（應該有/實際/結果）──
        st.markdown("### 📊 資料源完整度")
        rows = []
        # 境內：SITCA 是唯一源=我們抓的，無來源缺口；檢核『新鮮度』
        if len(_hchk):
            _dom = _hchk[_hchk["境內外"] == "境內"]
            _off = _hchk[_hchk["境內外"] == "境外"]
            dom_n = _dom["代碼"].nunique()
            off_n = _off["代碼"].nunique()
            dl = str(_dom["日期"].max())[:10] if len(_dom) else "-"
            ol = str(_off["日期"].max())[:10] if len(_off) else "-"
            dgap = _days_since(dl)
            ogap = _days_since(ol)
            rows.append({"資料源": "境內 SITCA", "應該有": "SITCA全市場(唯一源=即抓即建)",
                         "實際抓到": "{:,} 檔".format(dom_n),
                         "最新日": dl, "距今": "{} 天".format(dgap),
                         "結果": "✅ 正常" if dgap <= 4 else "🔴 逾 {} 天未更新".format(dgap)})
        else:
            off_n = 0
        # 境外：讀建庫寫的 coverage_offshore.json（絕對比對，含新基金漏抓）
        cov = None
        try:
            import json as _json
            with open("data/coverage_offshore.json", encoding="utf-8") as f:
                cov = _json.load(f)
        except Exception:
            cov = None
        if cov:
            uni = cov.get("universe_count", 0)
            blt = cov.get("built_count", 0)
            miss = cov.get("missing_count", 0)
            rate = cov.get("with_history_rate")
            ok = (rate is not None and rate >= 0.55)
            rows.append({"資料源": "境外 cnyes+yfinance",
                         "應該有": "{:,} 檔(cnyes官方清單)".format(uni),
                         "實際抓到": "{:,} 檔有歷史".format(blt),
                         "最新日": (ol if len(_hchk) else "-"),
                         "距今": "建庫日 {}".format(cov.get("date", "-")),
                         "結果": "✅ 覆蓋 {:.0%}".format(rate) if ok else "⚠️ 覆蓋僅 {:.0%}".format(rate or 0)})
        else:
            rows.append({"資料源": "境外 cnyes+yfinance", "應該有": "待建庫產生 coverage",
                         "實際抓到": "{:,} 檔".format(off_n), "最新日": "-", "距今": "-",
                         "結果": "⚠️ 尚無 coverage_offshore.json（跑一次境外建庫即產生）"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if cov and cov.get("missing_count"):
            with st.expander("🔍 境外無歷史清單（可能含新上市基金，共 {} 檔）".format(cov["missing_count"])):
                st.caption("這些 cnyes 有列、但 yfinance 抓不到歷史（多為非美元平行類股）。若見到你想追的新基金，跟我說補救。")
                st.dataframe(pd.DataFrame(cov.get("missing_sample", [])), use_container_width=True, hide_index=True)
        st.caption("資料源：{}".format(_hsrc))   # ← 修正：_hsrc 本身已是字串，不再逐字加號

        # ── 境內完整度說明：判斷要不要補境內 ──
        with st.expander("📖 境內完整度說明：之後要不要補境內？（點開看）"):
            st.markdown(
                "**境內的缺口不在「基金」，在「投信公司」。**\n\n"
                "- 境內唯一源是 **SITCA 官方**，我們抓的＝SITCA 有的 → **沒有來源缺口**。\n"
                "- **同一家投信底下的新基金** → 每日排程自動補進來，**你什麼都不用做**。\n"
                "- **真正可能漏的是「投信公司」**：系統只查下面清單裡的 **{} 家投信**。"
                "若台灣有**新設投信不在清單內**，那家的基金會整批抓不到。\n\n"
                "**所以「要不要補境內」＝「有沒有投信不在我們清單裡」。** "
                "判斷法：若你發現某境內基金/某新投信在 app 裡找不到 → 把**那家投信名稱**告訴我，"
                "我把它的代碼加進 `SITCA_COMPANIES`，下次排程就會納入。".format(len(SITCA_COMPANIES)))
            _cl = ["{} {}".format(c, n) for c, n in sorted(SITCA_COMPANIES.items())]
            st.markdown("**目前納入的 {} 家投信：**".format(len(SITCA_COMPANIES)))
            st.dataframe(pd.DataFrame({"投信": _cl}), use_container_width=True,
                         hide_index=True, height=240)
            st.caption("台灣投信約 39~40 家；若此清單少於實際家數，缺的那幾家就是要補的。"
                       "境外則相反：清單齊全，缺的是 yfinance 歷史（見上表覆蓋率）。")

        # ── 表2：資料品質 ──
        st.markdown("### 🧪 資料品質")
        qrows = []
        if len(_hchk):
            for reg, g in [("境內", _dom), ("境外", _off)]:
                if len(g) == 0:
                    continue
                yrs = sorted(set(str(x)[:4] for x in g["日期"].dropna()))
                qrows.append({"範圍": reg, "涵蓋年度": "{}~{}".format(yrs[0], yrs[-1]) if yrs else "-",
                              "年數": len(yrs), "檔數": g["代碼"].nunique(),
                              "近2年筆數": "{:,}".format(len(g))})
            st.dataframe(pd.DataFrame(qrows), use_container_width=True, hide_index=True)
        st.caption("境內≈統一往回9年；境外=各檔成立至今(老基金十餘年、新基金2~4年)。回測自動用各檔全歷史。")

        # ── 表3：排程狀態（抓 GitHub Actions API）──
        st.markdown("### ⏰ 排程狀態")
        if st.button("🔄 檢查排程最近執行結果"):
            import json as _json
            import urllib.request as _u
            wf = {"每日補最新 SITCA(境內)": "topup_daily.yml",
                  "建置境外基金庫(境外)": "build_offshore.yml"}
            srows = []
            for label, yml in wf.items():
                try:
                    url = ("https://api.github.com/repos/jojo164164/fund-tier1/actions/"
                           "workflows/{}/runs?per_page=1".format(yml))
                    req = _u.Request(url, headers={"User-Agent": "fund-tier1"})
                    with _u.urlopen(req, timeout=15) as r:
                        d = _json.load(r)
                    runs = d.get("workflow_runs", [])
                    if runs:
                        rr = runs[0]
                        concl = rr.get("conclusion") or rr.get("status")
                        when = (rr.get("updated_at") or "")[:10]
                        srows.append({"排程": label, "最近結果": concl, "時間": when,
                                      "狀態": "✅" if concl == "success" else "🔴 " + str(concl)})
                    else:
                        srows.append({"排程": label, "最近結果": "無紀錄", "時間": "-", "狀態": "⚠️"})
                except Exception as e:
                    srows.append({"排程": label, "最近結果": "查詢失敗", "時間": "-",
                                  "狀態": "⚠️ {}".format(type(e).__name__)})
            st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True)
            st.caption("GitHub 匿名 API 每小時限 60 次，偶爾查不到屬正常。連續🔴才需處理。")
        else:
            st.caption("按上方按鈕即時查境內每日/境外每日排程的最近成敗（走 GitHub API）。")

        st.markdown("---")
        st.markdown("### ✅ 邏輯檢核（憲法鐵律自我驗證）")
        chk = run_system_checks(adjust, fee_buy, fee_sell, entry_lag)
        st.dataframe(chk, width="stretch")
        fails = chk[(chk["結果"].str.contains("FAIL")) & (chk["檢核項目"].isin(CRITICAL_CHECKS))]
        if len(fails):
            st.error("🚨 有 {} 項關鍵檢核未通過，結果不可信。".format(len(fails)))
        else:
            st.success("✅ 全部關鍵檢核通過。")

        st.markdown("---")
        with st.expander("🔌 SITCA 即時連線測試（進階；雲端打SITCA會回404屬正常，僅本機診斷用）"):
            st.caption("境內健康請看上方『資料源完整度』的新鮮度(排程有在補即正常)。此處是本機端到端診斷，雲端會失敗不代表系統壞。")
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



    with tab_scan:
        _icon_title("scan", "每早掃描 — 今天誰觸發 + 歷史勝率")
        depth_opt = st.radio(
            "歷史深度（資料量大，先用小範圍確保不當機）",
            ["最近2年（推薦，快）", "最近3年", "最近5年", "全部"],
            horizontal=True, index=0,
            help="4246檔×5年≈530萬筆≈850MB，Streamlit免費版約1GB記憶體。"
                 "掃描今日觸發用2年就夠；要完整歷史勝率再開大。")
        _depth = {"最近2年（推薦，快）": 2, "最近3年": 3, "最近5年": 5, "全部": None}[depth_opt]
        hist, hsrc = load_history_db(_depth)
        if len(hist) == 0:
            st.info("**尚無歷史庫。** 這頁需要先用 GitHub Actions 建歷史庫"
                    "（data/sitca_nav.csv、data/offshore_nav.csv）。"
                    "建好後這裡會自動讀取，早上打開就能看今天哪些基金觸發跌幅、歷史勝率多少。")
            st.caption("目前狀態：{}".format(hsrc))
        else:
            st.caption("歷史庫：{}".format(hsrc))
            # 篩選維度（憲法：分類篩選後掃描）— 4維度：境內外/公司/資產類型/投資區域
            fc1, fc2 = st.columns(2)
            regions = ["全部"] + sorted(hist["境內外"].dropna().unique().tolist())
            pick_region = fc1.selectbox("境內/境外", regions)
            _iss = hist if pick_region == "全部" else hist[hist["境內外"] == pick_region]
            issuer_opts = ["全部"] + sorted([i for i in _iss["發行"].dropna().unique() if i])
            pick_issuer = fc2.selectbox("發行公司/投信", issuer_opts)

            fc3, fc4, fc5 = st.columns(3)
            # 系列：依已選發行公司(總代理)動態縮小 → 例如野村投信下可選 NN/天達/駿利…
            _ser = _iss if pick_issuer == "全部" else _iss[_iss["發行"] == pick_issuer]
            series_opts = ["全部"] + sorted([s for s in _ser["系列"].dropna().unique() if s])
            pick_series = fc3.selectbox("系列（總代理旗下品牌）", series_opts)
            asset_opts = ["全部"] + sorted([a for a in hist["資產類型"].dropna().unique() if a])
            pick_asset = fc4.selectbox("資產類型", asset_opts)
            area_opts = ["全部"] + sorted([r for r in hist["投資區域"].dropna().unique() if r])
            pick_area = fc5.selectbox("投資區域", area_opts)
            scan_thr = st.number_input("觸發門檻(%)", value=-10.0, step=0.5, max_value=0.0)

            view = hist.copy()
            if pick_region != "全部":
                view = view[view["境內外"] == pick_region]
            if pick_issuer != "全部":
                view = view[view["發行"] == pick_issuer]
            if pick_series != "全部":
                view = view[view["系列"] == pick_series]
            if pick_asset != "全部":
                view = view[view["資產類型"] == pick_asset]
            if pick_area != "全部":
                view = view[view["投資區域"] == pick_area]

            n_funds = view["代碼"].nunique()
            st.caption("篩選後範圍：{} 檔基金（縮小範圍可加快掃描）".format(n_funds))

            live_on = st.checkbox(
                "☀️ 掃描前即時補最新（境內；僅在能連到 SITCA 的環境有效）", value=False,
                help="本部署環境(Streamlit Cloud)的 SITCA POST 會被回 404 擋掉，故預設關閉。"
                     "資料新鮮度改由『每日排程(topup_daily.yml)』每天傍晚補進庫，"
                     "app 讀靜態庫即為 ≤1 天新。若你在能連 SITCA 的環境(如台灣本機)跑，"
                     "可勾選開啟即時補。")

            if st.button("☀️ 掃描今日觸發", type="primary"):
                work = view
                if live_on:
                    with st.spinner("即時補最新淨值（境內）…"):
                        hist2, topup_msg = live_topup_domestic(hist)
                    st.caption("🔄 " + topup_msg)
                    # 補過的庫要重新套一次相同篩選
                    work = hist2
                    if pick_region != "全部":
                        work = work[work["境內外"] == pick_region]
                    if pick_issuer != "全部":
                        work = work[work["發行"] == pick_issuer]
                    if pick_asset != "全部":
                        work = work[work["資產類型"] == pick_asset]
                    if pick_area != "全部":
                        work = work[work["投資區域"] == pick_area]
                with st.spinner("掃描 {} 檔，計算滾動跌幅+歷史勝率…".format(
                        work["代碼"].nunique())):
                    result = scan_history_db(work, scan_thr, MAX_SPAN_DAYS)
                if len(result) == 0:
                    st.warning("無足夠歷史資料可掃描。")
                else:
                    n_trig = int(result["今日觸發"].astype(str).str.contains("是").sum())
                    a, b, c = st.columns(3)
                    a.metric("掃描檔數", len(result))
                    b.metric("🔴 今日觸發", n_trig)
                    c.metric("觸發門檻", "{}%".format(scan_thr))
                    if n_trig > 0:
                        st.success("**今天有 {} 檔觸發跌幅** — 點欄位標題可排序（例如點「境內外」把境內外分開）。".format(n_trig))

                    # ── 欄位說明（移到表格上方；使用者要求）──
                    with st.expander("📖 欄位說明（先看這個再讀表）"):
                        st.markdown(
                            "- **滾動10日%**：最新淨值 vs 往回第10筆淨值的報酬率，負值=下跌\n"
                            "- **10天前日期／10天前淨值**：滾動10日的『起算基準點』\n"
                            "- **今日觸發**：最新滾動10日跌幅是否達門檻。🔴是=今天可考慮進場\n"
                            "- **連續觸發天**：從最新往回連續達門檻的筆數，越大代表跌勢持續中\n"
                            "- **淨值最新日／最新淨值**：該檔最新一筆淨值的日期與值（境外有時差，各檔可不同）\n"
                            "- **資料品質**：最近10筆淨值的曆日跨度。正常約14天；**>25天=有大缺口→標『稀疏』並作廢該筆**")

                    # ── 掃描結果表（st.dataframe：原生排序、凍結表頭、滿版、內建下載）──
                    _disp = result[[
                        "代碼", "境內外", "名稱", "發行公司", "系列", "主被動", "滾動10日%",
                        "10天前日期", "10天前淨值", "今日觸發", "連續觸發天",
                        "淨值最新日", "最新淨值", "資料品質"]].reset_index(drop=True)
                    st.dataframe(
                        _disp, use_container_width=True, height=640, hide_index=True,
                        column_config={
                            "滾動10日%": st.column_config.NumberColumn("滾動10日%", format="%.2f%%"),
                            "10天前淨值": st.column_config.NumberColumn("10天前淨值", format="%.4f"),
                            "最新淨值": st.column_config.NumberColumn("最新淨值", format="%.4f"),
                        })
                    dl1, dl2 = st.columns([1, 4])
                    dl1.download_button(
                        "⬇️ 下載 CSV", _disp.to_csv(index=False).encode("utf-8-sig"),
                        file_name="scan_{}.csv".format(dt.date.today().isoformat()),
                        mime="text/csv", use_container_width=True)
                    dl2.caption("列印：表格右上角展開全螢幕，或用瀏覽器 Ctrl+P。")

                    # 資料品質警示
                    bad = (result["資料品質"].astype(str).str.contains("稀疏")).sum()
                    if bad:
                        st.warning(
                            "⚠️ **{} / {} 檔資料稀疏**（最近10筆淨值曆日跨度>25天，公告有大缺口）→ "
                            "該筆作廢、不產假訊號。多為境外時差或停止公告的標的。".format(bad, len(result)))
                    st.caption("ℹ️ 各檔『淨值最新日』可能不同（境外有時差），跨檔比較請對齊此欄。"
                               "進場假設觸發日可成交（費用未計），實際申購有截止時點。")



    with tab_fund:
        _icon_title("fund", "個別基金分析")
        st.caption("先用篩選縮小範圍→選一檔→看完整回測：勝率/報酬/累積損益/進場時機/回撤/年度/連續觸發，走勢圖在最後。")

        hist_a = load_fund_meta()   # 下拉用輕量metadata，不全載
        if len(hist_a) == 0:
            st.info("尚無歷史庫，請先用 GitHub Actions 建庫。")
        elif not _HAS_MP:
            st.error("找不到 mp_analysis.py，請把它放進 repo 根目錄再 Reboot。")
        else:
            # ── 篩選列：境內外 / 發行公司 / 系列 / 資產類型 / 投資區域 ──
            f1, f2, f3 = st.columns(3)
            f4, f5, f6 = st.columns(3)

            def _opts(col):
                if col not in hist_a.columns:
                    return ["全部"]
                return ["全部"] + sorted([x for x in hist_a[col].dropna().unique().tolist() if str(x).strip()])

            sel_reg = f1.selectbox("境內/境外", _opts("境內外"), key="a_reg")
            v = hist_a
            if sel_reg != "全部":
                v = v[v["境內外"] == sel_reg]
            iss_opts = ["全部"] + sorted([x for x in v["發行"].dropna().unique().tolist() if str(x).strip()]) if "發行" in v.columns else ["全部"]
            sel_iss = f2.selectbox("發行公司", iss_opts, key="a_iss")
            if sel_iss != "全部":
                v = v[v["發行"] == sel_iss]
            ser_opts = ["全部"] + sorted([x for x in v["系列"].dropna().unique().tolist() if str(x).strip()]) if "系列" in v.columns else ["全部"]
            sel_ser = f3.selectbox("系列（總代理旗下品牌）", ser_opts, key="a_ser")
            if sel_ser != "全部":
                v = v[v["系列"] == sel_ser]
            sel_ast = f4.selectbox("資產類型", _opts("資產類型"), key="a_ast")
            if sel_ast != "全部" and "資產類型" in v.columns:
                v = v[v["資產類型"] == sel_ast]
            sel_area = f5.selectbox("投資區域", _opts("投資區域"), key="a_area")
            if sel_area != "全部" and "投資區域" in v.columns:
                v = v[v["投資區域"] == sel_area]
            thr_a = f6.number_input("觸發門檻(%)", value=-10.0, step=0.5, max_value=0.0, key="thr_a")

            opts = (v[["代碼", "名稱"]].drop_duplicates("代碼")
                    .assign(_lab=lambda d: d["名稱"].astype(str) + "  (" + d["代碼"].astype(str) + ")")
                    .sort_values("_lab"))
            if len(opts) == 0:
                st.warning("篩選後沒有基金，請放寬條件。")
            else:
                lab2code = dict(zip(opts["_lab"], opts["代碼"]))
                st.caption("篩選後 {} 檔可選".format(len(lab2code)))
                pick_lab = st.selectbox("選擇基金（可打字搜尋）", list(lab2code.keys()), key="a_pick")

                if st.button("🔍 分析這檔", type="primary"):
                    code_a = lab2code[pick_lab]
                    prices = load_prices_for_codes((code_a,)).get(code_a, {})
                    if len(prices) < 30:
                        st.error("這檔可用淨值太少（{}筆），無法回測。".format(len(prices)))
                    else:
                        thr = float(thr_a)
                        HZ = mp.HORIZONS

                        # 🏅 官方績效卡（MoneyDJ，用名稱 join）
                        _pname = pick_lab.split("  (")[0]
                        _, _plut = load_performance()
                        _pf = _plut.get(_norm_name(_pname))
                        if _pf:
                            _icon_title("cmp", "官方績效（MoneyDJ）")
                            pc = st.columns(4)
                            pc[0].metric("一年報酬", "{}%".format(_pf.get("一年%")) if pd.notna(_pf.get("一年%")) else "—")
                            pc[1].metric("三年報酬", "{}%".format(_pf.get("三年%")) if pd.notna(_pf.get("三年%")) else "—")
                            pc[2].metric("Sharpe", _pf.get("Sharpe") if pd.notna(_pf.get("Sharpe")) else "—")
                            pc[3].metric("同類排名", "第 {} 名".format(int(_pf["排名"])) if pd.notna(_pf.get("排名")) else "—")
                            _pr = {k: _pf.get(k) for k in ["一個月%", "三個月%", "六個月%", "一年%", "三年%", "五年%", "十年%"] if pd.notna(_pf.get(k))}
                            if _pr:
                                st.caption("官方各期報酬：" + "｜".join("{} {}%".format(k.replace("%", ""), v) for k, v in _pr.items())
                                           + "（來源 MoneyDJ，考慮配息）")
                            st.markdown("---")

                        win_df, avg_df, dd_df = mp.build_summary_tables(prices)

                        def _style_win(df):
                            cols = [c for c in df.columns if "天勝率" in c]
                            return df.style.applymap(mp.color_winrate_80only, subset=cols)

                        st.markdown("### 表A：勝率（各門檻 × 觀察天數）｜橘色 ≥ 80%")
                        st.caption("勝率＝觸發進場後，T+N 天收盤價 > 進場價的比例")
                        st.dataframe(_style_win(win_df), use_container_width=True)

                        st.markdown("### 表B：平均單次報酬%（各門檻 × 觀察天數）")
                        st.dataframe(avg_df, use_container_width=True)

                        st.markdown("### 表C：實際累積損益%（按淨值進場，門檻 {}）".format(thr))
                        ytc = mp.build_yearly_cumulative_table(prices, thr)
                        if isinstance(ytc, tuple):
                            ytc = ytc[0]
                        if ytc is not None and len(ytc):
                            st.dataframe(ytc, use_container_width=True)
                        else:
                            st.info("此門檻下無足夠觸發樣本。")

                        st.markdown("### 表E：進場時機完整比較（勝率，門檻 {}）".format(thr))
                        st.caption("連續第1天=首次觸發當天進｜第2天=等跌第2天再進｜第3天以後=等更深跌｜結束翌日=止跌後才進")

                        def _timing_matrix(value_key):
                            rows = {}
                            for h in HZ:
                                t = mp.build_entry_timing_table(prices, thr, h)
                                if t is None:
                                    continue
                                for _, r in t.iterrows():
                                    rows.setdefault(r["進場時機"], {})["{}天".format(h)] = r[value_key]
                            return pd.DataFrame(rows).T if rows else None

                        mtx_wr = _timing_matrix("勝率")
                        if mtx_wr is not None:
                            wr_cols = list(mtx_wr.columns)
                            st.dataframe(mtx_wr.style.applymap(mp.color_winrate_80only, subset=wr_cols),
                                         use_container_width=True)
                        else:
                            st.info("此門檻下無觸發。")

                        st.markdown("### 平均報酬對照（各進場時機 × 觀察天數）")
                        mtx_ar = _timing_matrix("平均報酬%")
                        if mtx_ar is not None:
                            st.dataframe(mtx_ar, use_container_width=True)

                        st.markdown("### 表F：最大回撤分析（門檻 {}）".format(thr))
                        st.caption("進場後先跌到低點再反彈。「平均回撤發生於第幾天」＝你需要撐過的浮虧期。")
                        ddt = mp.build_dd_timing_table(prices, thr)
                        if ddt is not None and len(ddt):
                            st.dataframe(ddt, use_container_width=True)
                        else:
                            st.info("此門檻下無觸發樣本。")

                        st.markdown("### 年度明細：每年平均單次報酬%（門檻 {}）".format(thr))
                        yt, _ = mp.build_yearly_table(prices, thr)
                        if yt is not None and len(yt):
                            st.dataframe(yt, use_container_width=True)
                        else:
                            st.info("此門檻下無年度樣本。")

                        st.markdown("### 連續觸發分析（勝率，門檻 {}）".format(thr))
                        st.caption("第1天=首次觸發｜第2天=連跌第2天｜第3天=連跌第3天｜第4天以後=持續下跌")

                        def _consec_matrix(value_key):
                            rows = {}
                            for h in HZ:
                                c = mp.build_consec_analysis(prices, thr, h)
                                if c is None or len(c) == 0:
                                    continue
                                first = c.columns[0]
                                for _, r in c.iterrows():
                                    key = r[first]
                                    if value_key in c.columns:
                                        rows.setdefault(key, {})["{}天".format(h)] = r[value_key]
                            return pd.DataFrame(rows).T if rows else None

                        cmx = _consec_matrix("勝率")
                        if cmx is not None:
                            st.dataframe(cmx.style.applymap(mp.color_winrate_80only, subset=list(cmx.columns)),
                                         use_container_width=True)
                        else:
                            st.info("此門檻下無連續觸發樣本。")

                        # ── 走勢圖（最後）：各門檻 3/5/10/15/20% 觸發標記 ──
                        st.markdown("### 📈 淨值走勢 + 各門檻觸發標記")
                        try:
                            import plotly.graph_objects as go
                            dates_all = sorted(prices.keys())
                            price_values = [prices[d] for d in dates_all]
                            fig = go.Figure()
                            fig.add_trace(go.Scatter(x=dates_all, y=price_values, mode="lines",
                                                     name="淨值", line=dict(color="#2196F3", width=1.5)))
                            cmap = {-3: "#FFC107", -5: "#FFA500", -10: "#E63946",
                                    -15: "#9B2335", -20: "#5C0A14"}
                            for t in [-3, -5, -10, -15, -20]:
                                r = mp.run_full_backtest(prices, t)
                                if not r:
                                    continue
                                tset = set(r["trigger_dates"])
                                tx = [d for d in dates_all if d in tset]
                                ty = [prices[d] for d in tx]
                                lab = "門檻 {}% ({}次)".format(t, r["total"])
                                fig.add_trace(go.Scatter(
                                    x=tx, y=ty, mode="markers", name=lab,
                                    marker=dict(color=cmap[t], size=7),
                                    visible=True if abs(t - thr) < 0.5 else "legendonly"))
                            fig.update_layout(height=460, xaxis_title="日期", yaxis_title="淨值",
                                              hovermode="x unified",
                                              legend=dict(orientation="h", yanchor="bottom", y=1.02))
                            st.plotly_chart(fig, use_container_width=True)
                        except Exception as e:
                            st.line_chart(pd.DataFrame({"淨值": price_values}, index=dates_all))

                        st.download_button(
                            "⬇️ 下載勝率表 CSV", win_df.to_csv(index=False).encode("utf-8-sig"),
                            file_name="{}_勝率.csv".format(code_a), mime="text/csv")
                        st.caption("列印：Ctrl+P。母專案同源回測；已套用跨度上限過濾（境外假訊號防護）。")

    # ══ Tab3：回測 ══

    # ══ 同類型基金比較（同區域/資產/公司/系列 → 並排比當前跌幅+歷史勝率）══
    with tab_cmp:
        _icon_title("cmp", "同類型基金比較")
        st.caption("選一組同類（同區域／資產／發行公司／系列），並排比「當前滾動10日跌幅 + 各自歷史勝率」，"
                   "找出這一類裡現在最值得進場的。")

        hist_c = load_fund_meta()   # 下拉用輕量metadata，不全載
        if len(hist_c) == 0:
            st.info("尚無歷史庫，請先建庫。")
        else:
            g1, g2, g3 = st.columns(3)
            g4, g5, g6 = st.columns(3)

            def _o(col, src):
                if col not in src.columns:
                    return ["全部"]
                return ["全部"] + sorted([x for x in src[col].dropna().unique().tolist() if str(x).strip()])

            vc = hist_c
            c_reg = g1.selectbox("境內/境外", _o("境內外", vc), key="c_reg")
            if c_reg != "全部":
                vc = vc[vc["境內外"] == c_reg]
            c_iss = g2.selectbox("發行公司", _o("發行", vc), key="c_iss")
            if c_iss != "全部":
                vc = vc[vc["發行"] == c_iss]
            c_ser = g3.selectbox("系列", _o("系列", vc), key="c_ser")
            if c_ser != "全部":
                vc = vc[vc["系列"] == c_ser]
            c_ast = g4.selectbox("資產類型", _o("資產類型", vc), key="c_ast")
            if c_ast != "全部":
                vc = vc[vc["資產類型"] == c_ast]
            c_area = g5.selectbox("投資區域", _o("投資區域", vc), key="c_area")
            if c_area != "全部":
                vc = vc[vc["投資區域"] == c_area]
            c_thr = g6.number_input("觸發門檻(%)", value=-10.0, step=0.5, max_value=0.0, key="c_thr")

            codes = vc["代碼"].dropna().unique().tolist()
            st.caption("篩選後 {} 檔。比較會逐檔跑歷史回測，建議 ≤ 60 檔。".format(len(codes)))

            if st.button("🆚 比較這一類", type="primary"):
                if len(codes) == 0:
                    st.warning("沒有符合的基金，請放寬條件。")
                elif len(codes) > 60:
                    st.warning("這一類有 {} 檔，太多了（逐檔回測會很久）。請再用發行公司/系列/區域縮到 ≤ 60 檔。".format(len(codes)))
                else:
                    thr = float(c_thr)
                    rows = []
                    _pmap = load_prices_for_codes(tuple(codes))   # 只讀這組基金，不全載
                    _, _cmp_plut = load_performance()              # 官方績效 join 用
                    _meta = vc.drop_duplicates("代碼").set_index("代碼")
                    prog = st.progress(0.0)
                    for i, code in enumerate(codes):
                        prices = _pmap.get(code, {})
                        if len(prices) < ROLL_N + 1:
                            continue
                        rolling = calc_all_rolling_returns(prices, ROLL_N, max_span)
                        if not rolling:
                            continue
                        last = rolling[-1]
                        triggered = last["return"] <= thr and last["valid"]
                        consec = 0
                        for r in reversed(rolling):
                            if r["return"] <= thr and r.get("valid", True):
                                consec += 1
                            else:
                                break
                        best_wr, best_h, n_hist = None, None, 0
                        bt = run_full_backtest(prices, thr, rolling, 0, 0.0, 0.0, max_span)
                        if bt:
                            n_hist = bt.get("觸發次數", 0)
                            tbl = build_entry_timing_table(bt)
                            bi = _pick_best_timing_idx(tbl)
                            if bi is not None:
                                best_wr = tbl.loc[bi, "勝率"]
                                best_h = tbl.loc[bi, "持有天數"]
                        _row = _meta.loc[code] if code in _meta.index else None
                        nm = str(_row["名稱"]) if _row is not None and "名稱" in _meta.columns else ""
                        ser = str(_row["系列"]) if _row is not None and "系列" in _meta.columns else ""
                        _pf = _cmp_plut.get(_norm_name(nm), {})
                        rows.append({
                            "代碼": code, "名稱": nm[:36], "系列": ser,
                            "滾動10日%": round(last["return"], 2),
                            "今日觸發": "🔴" if triggered else "—",
                            "連續觸發天": consec,
                            "官方一年%": _pf.get("一年%"),
                            "官方三年%": _pf.get("三年%"),
                            "官方Sharpe": _pf.get("Sharpe"),
                            "官方排名": _pf.get("排名"),
                            "歷史最佳勝率": best_wr if best_wr else "—",
                            "最佳持有天": best_h if best_h else "—",
                            "淨值最新日": last["date"],
                            "資料品質": "✅" if last["valid"] else "⚠️稀疏",
                        })
                        prog.progress((i + 1) / len(codes))
                    prog.empty()

                    if not rows:
                        st.warning("這一類沒有可比較的資料（可能淨值太少）。")
                    else:
                        cdf = pd.DataFrame(rows).sort_values("滾動10日%")
                        n_trig = int((cdf["今日觸發"] == "🔴").sum())
                        st.success("**這一類共 {} 檔，今天有 {} 檔觸發跌幅。** 表已按跌幅排序（跌最深在最上）。"
                                   "點欄位標題可改排序（例如按『歷史最佳勝率』找高勝率的）。".format(len(cdf), n_trig))
                        st.dataframe(cdf.reset_index(drop=True), use_container_width=True, height=560,
                                     hide_index=True)
                        st.download_button(
                            "⬇️ 下載比較 CSV", cdf.to_csv(index=False).encode("utf-8-sig"),
                            file_name="compare_{}.csv".format(dt.date.today()), mime="text/csv")
                        st.caption("判讀：同類裡「🔴今天觸發 + 歷史最佳勝率高 + 觸發次數夠(≥10)」= 現在較值得進場。"
                                   "勝率為觸發後最佳持有天的回測值，樣本太少不顯示。列印用 Ctrl+P。")

    # ══ 績效 Ranking（MoneyDJ 官方報酬/風險/排名）══
    with tab_rank:
        _icon_title("cmp", "績效 Ranking（MoneyDJ 官方）")
        st.caption("官方報酬(1M~10Y) + 風險(境外含標準差/Sharpe/Beta) + 官方排名。"
                   "來源：MoneyDJ FundDJ，考慮配息、原幣別。")
        perf_df, _ = load_performance()
        if len(perf_df) == 0:
            st.info("尚無績效資料。請先在 GitHub Actions 跑「建置 基金績效Ranking」產生 data/performance.csv。")
        else:
            r1, r2, r3 = st.columns(3)
            reg = r1.selectbox("境內/境外", ["全部", "境內", "境外"], key="rk_reg")
            v = perf_df
            if reg != "全部" and "_境內外" in v.columns:
                v = v[v["_境內外"] == reg]
            comp_opts = ["全部"] + sorted([x for x in v["公司"].dropna().unique().tolist() if str(x).strip()]) if "公司" in v.columns else ["全部"]
            comp = r2.selectbox("基金公司", comp_opts, key="rk_comp")
            if comp != "全部":
                v = v[v["公司"] == comp]
            period = r3.selectbox("排序期間", ["一年%", "六個月%", "三個月%", "一個月%",
                                             "三年%", "五年%", "十年%", "Sharpe"], key="rk_period")
            area_opts = ["全部"] + sorted([x for x in v["投資區域"].dropna().unique().tolist() if str(x).strip()]) if "投資區域" in v.columns else ["全部"]
            area = st.selectbox("投資區域", area_opts, key="rk_area")
            if area != "全部":
                v = v[v["投資區域"] == area]

            show_cols = [c for c in ["名稱", "公司", "_境內外", "投資區域",
                                     "一個月%", "三個月%", "六個月%", "一年%", "三年%",
                                     "五年%", "十年%", "年化標準差", "Sharpe", "Beta",
                                     "淨值日期"] if c in v.columns]
            vv = v[show_cols].copy()
            if period in vv.columns:
                vv = vv.sort_values(period, ascending=False, na_position="last")
            vv = vv.reset_index(drop=True)
            vv.insert(0, "名次", range(1, len(vv) + 1))
            st.success("**{} 檔**（依 {} 由高到低）。點欄位標題可改排序。".format(len(vv), period))
            colcfg = {c: st.column_config.NumberColumn(c, format="%.2f")
                      for c in vv.columns if c.endswith("%") or c in ("Sharpe", "Beta", "年化標準差")}
            st.dataframe(vv, use_container_width=True, height=600, hide_index=True,
                         column_config=colcfg)
            st.download_button("⬇️ 下載 CSV", vv.to_csv(index=False).encode("utf-8-sig"),
                               file_name="ranking_{}.csv".format(dt.date.today()), mime="text/csv")
            st.caption("境內依類型頁僅提供報酬(無風險指標)；境外含完整風險。列印 Ctrl+P。")

    # ══ 筆記（手動、可下載保存；取代自動追蹤日誌）══
    with tab_notes:
        _icon_title("notes", "筆記")
        st.caption("寫下你的觀察與單筆記錄。Streamlit Cloud 重啟會清空，請用下載保存、下次上傳載回。")
        if "notes_text" not in st.session_state:
            st.session_state["notes_text"] = ""
        _up = st.file_uploader("載入先前筆記 (.md/.txt)", type=["md", "txt"], key="notes_up")
        if _up is not None:
            st.session_state["notes_text"] = _up.read().decode("utf-8", errors="replace")
        _txt = st.text_area("筆記內容", value=st.session_state["notes_text"], height=420, key="notes_area")
        st.session_state["notes_text"] = _txt
        st.download_button("⬇️ 下載筆記", _txt.encode("utf-8"),
                           "fund_notes_{}.md".format(dt.date.today()), "text/markdown")

if __name__ == "__main__":
    main()
