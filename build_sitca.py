# -*- coding: utf-8 -*-
"""
SITCA 境內基金淨值歷史建庫腳本（GitHub Actions 版）
=====================================================
依《台灣基金滾動跌幅系統 — 專案憲法》。設計給 GitHub Actions 執行，
零終端機操作：使用者在 repo 的 Actions 分頁按「Run workflow」即可。

核心設計（對應 GitHub Actions 6 小時上限）：
  - 斷點續傳：每抓完一批寫入 CSV + 進度檔，下次自動從斷點接續
  - 禮貌延遲：每次請求間隔 SLEEP 秒，不打爆 SITCA
  - 時間預算：跑到 TIME_BUDGET_MIN 分鐘就主動存檔停止，避免被強制砍斷

環境變數（由 workflow 傳入，使用者改 workflow 檔即可調整）：
  SITCA_COMPANIES   逗號分隔投信代碼，如 "A0036,A0032"（預設安聯+野村）
  SITCA_START       起始日 YYYYMMDD（預設：今天往回推 DAYS_BACK 天）
  SITCA_DAYS_BACK   往回抓幾個營業日（預設 5，驗證用；建庫時改大）
  SITCA_TIME_BUDGET 本次最多跑幾分鐘（預設 300=5小時，留 1 小時緩衝）

輸出：
  data/sitca_nav.csv       淨值長表（代碼,日期,淨值,幣別,名稱,投信,分類）
  data/sitca_progress.json 已完成的 (公司,日期) 清單，供斷點續傳
"""

import os
import re
import csv
import json
import time
import datetime as dt

try:
    import requests
except ImportError:
    raise SystemExit("需要 requests：workflow 會自動 pip install")

# ── 設定（可由環境變數覆蓋）──
URL = "https://www.sitca.org.tw/ROC/Industry/IN2106.aspx?pid=IN2213_02"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": URL,
    "Origin": "https://www.sitca.org.tw",
}
SLEEP = float(os.environ.get("SITCA_SLEEP", "0.3"))  # 降至0.3秒(原1.0)加速            # 每請求間隔秒
TIME_BUDGET_MIN = int(os.environ.get("SITCA_TIME_BUDGET", "300"))
DAYS_BACK = int(os.environ.get("SITCA_DAYS_BACK", "250"))  # 預設約1年；15年填3800

# ★ 平行化分段（GitHub Actions matrix）：把日期切成 N 段同時跑，總時間 ÷ N
#   SEGMENT_TOTAL=20 + SEGMENT_INDEX=0..19 → 20個job平行，29小時→1.5小時
#   每段寫自己的CSV（避免多job寫同檔衝突），app讀取時自動合併所有分段檔
SEGMENT_TOTAL = int(os.environ.get("SEGMENT_TOTAL", "1"))
SEGMENT_INDEX = int(os.environ.get("SEGMENT_INDEX", "0"))

DATA_DIR = "data"
# 分段檔名：多job平行時各寫各的，避免衝突；SEGMENT_TOTAL=1時維持原檔名
_seg_suffix = "" if SEGMENT_TOTAL <= 1 else "_seg{:02d}".format(SEGMENT_INDEX)
CSV_PATH = os.path.join(DATA_DIR, "sitca_nav{}.csv".format(_seg_suffix))
PROGRESS_PATH = os.path.join(DATA_DIR, "sitca_progress{}.json".format(_seg_suffix))
CSV_COLS = ["代碼", "日期", "淨值", "幣別", "名稱", "投信代碼", "投信",
            "類型代號", "資產類型", "投資區域", "分類"]

# ── 篩選參數（縮小存檔範圍；抓取成本不變，但檔案小、app跑得快）──
# SITCA 一次請求回全市場，抓取時間與檔數無關；篩選是為了「存得下、跑得動」
FILTER_COMPANIES = [c.strip() for c in
                    os.environ.get("FILTER_COMPANIES", "").split(",") if c.strip()]
FILTER_ASSETS = [a.strip() for a in
                 os.environ.get("FILTER_ASSETS", "").split(",") if a.strip()]
FILTER_REGIONS = [r.strip() for r in
                  os.environ.get("FILTER_REGIONS", "").split(",") if r.strip()]


def csv_path_for(date_obj):
    """★分年拆檔★：每年一個CSV，避開 GitHub 單檔 100MB 限制。

    全市場一天約4400筆，一年約110萬筆≈80-100MB → 剛好一年一檔最適當。
    app 讀取時用 glob 合併所有年度檔。
    """
    return os.path.join(DATA_DIR, "sitca_nav_{}.csv".format(date_obj.year))

COMPANIES = {
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


def classify_etf(code):
    c = str(code).strip().upper()
    if len(c) == 6 and c[5] == "A":
        return "主動ETF-股"
    if len(c) == 6 and c[5] == "D":
        return "主動ETF-債"
    return "被動ETF/共同基金"  # Tier2 再用SITCA類型代號欄細分


def parse_rows(html):
    """兩段式解析（已用三張真實DOM圖驗證：不受單雙引號/class/長名稱影響）。

    保留「類型代號」欄（tds[0]，如 AA1/AG/AH21/AL21）——這是 SITCA 官方分類鑰匙，
    存原始值不丟；官方「基金類型代號說明」對照表取得後可隨時精準翻譯，無需重抓。
    """
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        if 'DTHeader' in tr or '類型代號' in tr:
            continue
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        tds = [re.sub(r'<[^>]+>', '', t).strip() for t in tds]
        if len(tds) < 8:
            continue
        tcode, comp, code, fname, cur, nav = tds[0], tds[1], tds[3], tds[5], tds[6], tds[7]
        if not re.match(r'^A00\d{2}$', comp):
            continue
        out.append({"代碼": code, "名稱": fname, "幣別": cur, "淨值": nav,
                    "公司代碼": comp, "類型代號": tcode})
    return out




# ══════════════════════════════════════════════════════════════
# 分類推斷（篩選維度用）
#   ⚠️ 標明為「推斷值」：官方「基金類型代號說明」對照表尚未取得（鐵律5）。
#      原始「類型代號」已存入CSV，取得官方對照後可精準重標，無需重抓資料。
# ══════════════════════════════════════════════════════════════
# 類型代號前綴 → 資產類型（自實際資料反推，待官方對照表確認）
TYPE_PREFIX = [
    ("AL", "主動式ETF"),      # AL21: 元大全球AI新經濟主動式ETF
    ("AK", "ETF連結基金"),    # AK2: 元大標普500ETF連結基金
    ("AH", "ETF"),            # AH11/AH21/AH22: 0050/0056/債券ETF
    ("AG", "不動產證券化"),   # AG: 全球不動產證券化基金
    ("AD", "貨幣市場"),       # AD1/AD2: 得寶貨幣市場、萬泰貨幣市場
    ("AC", "債券型"),         # AC21: 0至2年投資級企業債
    ("AB", "平衡型"),         # AB2: 新東協平衡
    ("AE", "組合型"),         # AE21/AE23: 全球新興市場精選組合、ETF穩健組合
    ("AI", "指數型"),         # AI2: 大中華價值指數、印尼指數
    ("AA", "股票型"),         # AA1/AA2: 元大2001、多福、全球農業商機
]

# 名稱關鍵字 → 投資區域（雙軌推斷，名稱比代號更能反映投資區域）
REGION_KEYWORDS = [
    ("台灣", ["台灣", "臺灣", "台股", "上市", "上櫃", "店頭", "中型100", "50", "高股息"]),
    ("中國", ["中國", "大陸", "A股", "滬深", "上證", "中華", "人民幣"]),
    ("美國", ["美國", "美股", "標普", "S&P", "納斯達克", "道瓊", "費城", "美元"]),
    ("日本", ["日本", "日經", "東證"]),
    ("印度", ["印度"]),
    ("越南", ["越南"]),
    ("韓國", ["韓國", "南韓"]),
    ("亞洲", ["亞洲", "亞太", "東協", "新興亞洲", "泰國", "印尼", "菲律賓", "馬來"]),
    ("歐洲", ["歐洲", "德國", "法國", "英國", "歐元"]),
    ("拉丁美洲", ["巴西", "拉丁", "墨西哥"]),
    ("新興市場", ["新興"]),
    ("全球", ["全球", "環球", "世界", "國際"]),
]


def classify_asset(type_code, name=""):
    """資產類型推斷：類型代號前綴優先，無法判斷時用名稱關鍵字。"""
    tc = str(type_code).strip().upper()
    for prefix, label in TYPE_PREFIX:
        if tc.startswith(prefix):
            return label
    n = str(name)
    if any(k in n for k in ["貨幣市場"]):
        return "貨幣市場"
    if any(k in n for k in ["債", "bond"]):
        return "債券型"
    if "平衡" in n or "多重資產" in n:
        return "平衡型"
    if "ETF" in n:
        return "ETF"
    return "其他"


def classify_region(name):
    """投資區域推斷：依名稱關鍵字（順序重要，特定區域優先於「全球」）。"""
    n = str(name)
    for region, kws in REGION_KEYWORDS:
        if any(k in n for k in kws):
            return region
    return "未分類"


def _hidden(html, name):
    m = re.search(r'<input[^>]*name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(r'<input[^>]*value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', html)
    return m.group(1) if m else ""


def _detect_fields(html):
    date_name = "ctl00$ContentPlaceHolder1$txtQ_Date"
    company_name = "ctl00$ContentPlaceHolder1$ddlQ_Comid"
    md = re.search(r'name="(ctl00\$[^"]*txt[^"]*[Dd]ate)"', html)
    if md:
        date_name = md.group(1)
    for sm in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        if "A0005" in sm.group(2) or "A0001" in sm.group(2):
            company_name = sm.group(1)
            break
    return date_name, company_name


def fetch_one(session, company, date_str, cached=None):
    """抓某投信某日全部基金淨值。回傳 (list[dict], 錯誤或None, 新cached)。

    ★ 效能優化：重用 VIEWSTATE
      ASP.NET 的 POST 回應本身也帶新的 __VIEWSTATE/__EVENTVALIDATION，
      可直接拿來下一次用 → 省掉每次的 GET，請求數減半、時間減半。
      cached=(vs, vsg, ev, date_name, company_name)；首次或失效時才重新 GET。

    ★ ALL 模式（company="" 或 "ALL"）：
      公司下拉第一個選項「所有公司」value為空字串（真實VIEWSTATE解碼確認）。
      一次POST回全市場某日所有基金（實測4400檔/36家投信）。
    """
    if cached is None:
        r = session.get(URL, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return [], "GET {}".format(r.status_code), None
        html = r.text
        vs = _hidden(html, "__VIEWSTATE")
        if not vs:
            return [], "no VIEWSTATE", None
        date_name, company_name = _detect_fields(html)
        cached = (vs, _hidden(html, "__VIEWSTATEGENERATOR"),
                  _hidden(html, "__EVENTVALIDATION"), date_name, company_name)

    vs, vsg, ev, date_name, company_name = cached
    comp_value = "" if str(company).upper() in ("", "ALL") else company
    payload = {
        "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg, "__EVENTVALIDATION": ev,
        date_name: date_str, company_name: comp_value,
    }
    for btn in ["ctl00$ContentPlaceHolder1$btnQuery",
                "ctl00$ContentPlaceHolder1$BtnQuery",
                "ctl00$ContentPlaceHolder1$Button1"]:
        payload.setdefault(btn, "查詢")
    r2 = session.post(URL, headers=HEADERS, data=payload, timeout=60)
    if r2.status_code != 200:
        return [], "POST {}".format(r2.status_code), None  # token可能失效→下次重取

    rows = parse_rows(r2.text)
    # 從POST回應提取新token供下次使用（省掉GET）
    new_vs = _hidden(r2.text, "__VIEWSTATE")
    if new_vs:
        cached = (new_vs, _hidden(r2.text, "__VIEWSTATEGENERATOR"),
                  _hidden(r2.text, "__EVENTVALIDATION"), date_name, company_name)
    elif not rows:
        cached = None  # 解析不到且無token→下次重新GET
    return rows, None, cached


def load_progress():
    try:
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return set(tuple(x) for x in json.load(f))
    except Exception:
        return set()


def save_progress(done):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def append_csv(records, date_obj):
    """依日期寫入對應年度的CSV（分年拆檔）。"""
    if not records:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    path = csv_path_for(date_obj)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        if not exists:
            w.writeheader()
        for r in records:
            w.writerow(r)


def business_days(start, days_back):
    """從 start 往回取 days_back 個工作日（週末跳過；國定假日靠SITCA回空自然略過）。"""
    out = []
    d = start
    while len(out) < days_back:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def main():
    # 留空 = 自動抓全部 36 家投信（正式建庫）；填代碼 = 只抓指定家（驗證用）
    # 填 "ALL" = 一次查「所有公司」（若SITCA支援，請求數變1/36）
    companies_env = os.environ.get("SITCA_COMPANIES", "").strip()
    all_mode = companies_env.upper() == "ALL"
    if all_mode:
        companies = ["ALL"]
    elif companies_env:
        companies = [c.strip() for c in companies_env.split(",") if c.strip()]
    else:
        companies = list(COMPANIES.keys())  # 全 36 家逐一抓

    # 跳過最近 N 個營業日：境外基金淨值有時差（隔天下午4點後才齊，使用者提供）
    # 不猜「殘缺」、不丟資料，只是不抓「太新、境外還沒補齊」的日期。預設跳2天緩衝。
    skip_recent = int(os.environ.get("SITCA_SKIP_RECENT", "2"))

    start_env = os.environ.get("SITCA_START", "").strip()
    if start_env:
        start = dt.datetime.strptime(start_env, "%Y%m%d").date()
    else:
        # 從「今天往回跳 skip_recent 個營業日」當起點，避開境外未齊的最新日
        start = dt.date.today()
        skipped = 0
        while skipped < skip_recent:
            start -= dt.timedelta(days=1)
            if start.weekday() < 5:
                skipped += 1
    days = business_days(start, DAYS_BACK)

    # ★ 平行化：只跑本 segment 負責的日期（用 index 間隔切，讓各段負載均勻）
    if SEGMENT_TOTAL > 1:
        days = [d for i, d in enumerate(days) if i % SEGMENT_TOTAL == SEGMENT_INDEX]
        print("【平行分段】第 {}/{} 段，本段負責 {} 天".format(
            SEGMENT_INDEX + 1, SEGMENT_TOTAL, len(days)))
    if not days:
        print("本段無日期可抓，結束。")
        return

    print("=" * 60)
    print("SITCA 建庫  投信:", len(companies), "家 | 日數:", len(days),
          "| 跳最近:", skip_recent, "營業日")
    print("區間:", days[0], "~", days[-1], "| 時間預算:", TIME_BUDGET_MIN, "分")
    print("=" * 60)

    done = load_progress()
    print("已完成(斷點續傳):", len(done), "個(公司,日)組合")

    session = requests.Session()
    tok_cache = None  # VIEWSTATE快取，重用以省掉每次的GET
    t0 = time.time()
    new_records = 0
    tasks_done = 0

    for company in companies:
        cname = "所有公司" if company == "ALL" else COMPANIES.get(company, company)
        for d in days:
            date_str = d.strftime("%Y%m%d")
            key = (company, date_str)
            if key in done:
                continue
            # 時間預算檢查（留緩衝，避免被6小時上限強砍）
            if (time.time() - t0) / 60 > TIME_BUDGET_MIN:
                print("⏸ 達時間預算，存檔停止。下次 Run 自動續傳。")
                save_progress(done)
                _summary(new_records, tasks_done, done)
                return

            rows, err, tok_cache = fetch_one(session, company, date_str, tok_cache)
            if err:
                print("  ✗ {} {} → {}".format(cname, date_str, err))
                tok_cache = None  # 失敗時清快取，下次重新GET
                time.sleep(SLEEP)
                continue

            recs = []
            for x in rows:
                # ALL 模式下 company="ALL"，真實投信要取自資料列本身的公司代碼
                c_code = x.get("公司代碼") or company
                tcode = x.get("類型代號", "")
                fname = x["名稱"]
                asset = classify_asset(tcode, fname)
                region = classify_region(fname)

                # ── 套用篩選（縮小存檔範圍，抓取成本不變）──
                if FILTER_COMPANIES and c_code not in FILTER_COMPANIES:
                    continue
                if FILTER_ASSETS and asset not in FILTER_ASSETS:
                    continue
                if FILTER_REGIONS and region not in FILTER_REGIONS:
                    continue

                recs.append({
                    "代碼": x["代碼"],
                    "日期": d.isoformat(),
                    "淨值": x["淨值"],
                    "幣別": x["幣別"],
                    "名稱": fname[:40],
                    "投信代碼": c_code,
                    "投信": COMPANIES.get(c_code, c_code),
                    "類型代號": tcode,
                    "資產類型": asset,
                    "投資區域": region,
                    "分類": classify_etf(x["代碼"]),
                })
            append_csv(recs, d)
            done.add(key)
            new_records += len(recs)
            tasks_done += 1
            n_active = sum(1 for r in recs if r["分類"].startswith("主動ETF"))
            n_comp = len(set(r["投信代碼"] for r in recs))
            print("  ✓ {} {} → {} 檔（{}家投信, 主動ETF {}）".format(
                cname, date_str, len(recs), n_comp, n_active))
            save_progress(done)  # 每筆都存，確保任何中斷都可續傳
            time.sleep(SLEEP)

    _summary(new_records, tasks_done, done)


def _summary(new_records, tasks_done, done):
    print("=" * 60)
    print("本次完成 {} 個(公司,日)，新增 {} 筆淨值記錄".format(tasks_done, new_records))
    print("累計進度:", len(done), "個組合")
    import glob
    files = sorted(glob.glob(os.path.join(DATA_DIR, "sitca_nav_*.csv")))
    if files:
        print("── 年度檔（分年拆檔避開100MB限制）──")
        grand = 0
        for p in files:
            with open(p, encoding="utf-8-sig") as f:
                n = sum(1 for _ in f) - 1
            mb = os.path.getsize(p) / 1024 / 1024
            grand += n
            flag = " ⚠️接近100MB" if mb > 80 else ""
            print("  {}: {:,} 筆, {:.1f} MB{}".format(
                os.path.basename(p), n, mb, flag))
        print("  總計: {:,} 筆".format(grand))
    if FILTER_COMPANIES or FILTER_ASSETS or FILTER_REGIONS:
        print("── 本次篩選條件 ──")
        if FILTER_COMPANIES:
            print("  投信:", FILTER_COMPANIES)
        if FILTER_ASSETS:
            print("  資產類型:", FILTER_ASSETS)
        if FILTER_REGIONS:
            print("  投資區域:", FILTER_REGIONS)
    print("=" * 60)


if __name__ == "__main__":
    main()
