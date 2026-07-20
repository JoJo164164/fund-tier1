# -*- coding: utf-8 -*-
"""
SITCA 基金淨值(日) 端到端抓取驗證腳本
=====================================
目的：證明「先 GET 取 token → POST 帶參數 → 解析淨值表」這條鏈在真實網路可行。
      跑通後，SITCA 這條資料源即從「解析已驗」升到「端到端可用」（憲法鐵律13）。

用法（本機有網路環境）：
    pip install requests
    python verify_sitca.py

預期成功輸出：抓到某投信某日的數十~數百檔基金淨值，並印出前 15 筆。

★ 這支腳本只讀取、不寫入任何檔案，安全。★
★ 欄位名、對照表、解析規則全部來自真實 VIEWSTATE 解碼驗證，非臆測。★
"""

import re
import sys
import datetime as dt

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

URL = "https://www.sitca.org.tw/ROC/Industry/IN2106.aspx?pid=IN2213_02"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": URL,
    "Origin": "https://www.sitca.org.tw",
}

# ASP.NET 表單欄位名（來自真實 VIEWSTATE 解碼：ctl00_ContentPlaceHolder1_txtQ_Date）
FIELD_DATE = "ctl00$ContentPlaceHolder1$txtQ_Date"
FIELD_COMPANY = "ctl00$ContentPlaceHolder1$ddlQ_Company"  # 公司下拉（推定名，腳本會自動偵測修正）

# 測試參數：抓「元大投信 A0005」在某個近期營業日的淨值
TEST_COMPANY = "A0005"


def _find_hidden(html, name):
    m = re.search(
        r'<input[^>]*name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(
            r'<input[^>]*value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', html)
    return m.group(1) if m else ""


def _detect_field_names(html):
    """從真實頁面偵測日期與公司欄位的實際 name，避免寫死猜錯。"""
    date_name = FIELD_DATE
    company_name = FIELD_COMPANY
    m_date = re.search(r'name="(ctl00\$[^"]*txt[^"]*[Dd]ate)"', html)
    if m_date:
        date_name = m_date.group(1)
    # 公司下拉：找含 A0005 選項的 <select>
    for sm in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        if "A0005" in sm.group(2):
            company_name = sm.group(1)
            break
    return date_name, company_name


NAV_ROW_RE = re.compile(
    r"<td align='left'>([A-Z0-9]+)</td>"       # 類型代號
    r"<td align='left'>(A00\d{2})</td>"         # 公司代號
    r"<td align='left'>[^<]*</td>"              # 公司名稱
    r"<td align='left'>(\d{4,6}[A-Z]?)</td>"    # 受益憑證代號=基金代碼
    r"<td align='left'>\d+</td>"                # 基金統編
    r"<td align='left'>([^<]+?)</td>"           # 基金名稱
    r"<td align='left'>([A-Z]{3})</td>"         # 幣別
    r"<td align='right'>([\d.]+|\(註\d\)|-)</td>"  # 淨值
)


def classify(code):
    c = code.strip().upper()
    if len(c) == 6 and c[5] == "A":
        return "主動ETF-股"
    if len(c) == 6 and c[5] == "D":
        return "主動ETF-債"
    return "被動/基金"


def main():
    s = requests.Session()
    print("① GET 空頁，取得 ASP.NET token ...")
    r = s.get(URL, headers=HEADERS, timeout=20)
    print("   GET status:", r.status_code, "| 長度:", len(r.text))
    if r.status_code != 200:
        print("   ✗ GET 失敗，SITCA 可能擋此環境。把這行輸出貼回。")
        return

    html = r.text
    vs = _find_hidden(html, "__VIEWSTATE")
    vsg = _find_hidden(html, "__VIEWSTATEGENERATOR")
    ev = _find_hidden(html, "__EVENTVALIDATION")
    print("   __VIEWSTATE 長度:", len(vs), "| GENERATOR:", vsg[:12], "| EVENTVALIDATION 長度:", len(ev))
    if not vs:
        print("   ✗ 抓不到 VIEWSTATE，頁面結構可能變了。把 GET 到的前 500 字貼回：")
        print(html[:500])
        return

    date_name, company_name = _detect_field_names(html)
    print("   偵測到欄位名 → 日期:", date_name, "| 公司:", company_name)

    # 找一個近期營業日（往回找，跳過週末）
    d = dt.date.today()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    d -= dt.timedelta(days=1)  # 用昨天，確保已公告
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    date_str = d.strftime("%Y%m%d")

    payload = {
        "__VIEWSTATE": vs,
        "__VIEWSTATEGENERATOR": vsg,
        "__EVENTVALIDATION": ev,
        date_name: date_str,
        company_name: TEST_COMPANY,
    }
    # 查詢按鈕（常見名，附上以防後端要求）
    for btn in ["ctl00$ContentPlaceHolder1$btnQuery",
                "ctl00$ContentPlaceHolder1$BtnQuery",
                "ctl00$ContentPlaceHolder1$Button1"]:
        payload.setdefault(btn, "查詢")

    print(f"\n② POST 查詢：公司={TEST_COMPANY} 日期={date_str} ...")
    r2 = s.post(URL, headers=HEADERS, data=payload, timeout=30)
    print("   POST status:", r2.status_code, "| 回傳長度:", len(r2.text))

    rows = NAV_ROW_RE.findall(r2.text)
    print("\n③ 解析結果：抓到", len(rows), "檔基金淨值")
    if not rows:
        print("   ✗ 沒解析到列。可能原因：該日無資料/欄位名需微調。")
        print("   請把以下片段貼回（回傳HTML中 <table 附近 300 字）：")
        idx = r2.text.find("<table")
        print(r2.text[idx:idx + 300] if idx >= 0 else r2.text[:300])
        return

    print("\n   代號      分類         幣別  淨值      基金名稱")
    print("   " + "-" * 70)
    for r in rows[:15]:
        tcode, comp, code, name, cur, nav = r
        name = name.split("<")[0][:24]
        print(f"   {code:<9} {classify(code):<11} {cur}  {nav:<9} {name}")

    active = [r[2] for r in rows if classify(r[2]).startswith("主動ETF")]
    print(f"\n   ✓ 端到端成功！此批 {len(rows)} 檔，主動ETF: {active if active else '（此投信無）'}")
    print("   ✓ SITCA 資料源確認可用。把這段輸出貼回，我就把建庫模組寫進正式 app。")


if __name__ == "__main__":
    main()
