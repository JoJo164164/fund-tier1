# 專案接軌檔（2026-08-14 更新 · 全功能完整版）
## repo: github.com/jojo164164/fund-tier1

## ★系統現況：資料雙軌 + 完整分析 + 官方績效 + Allianz UI，全自動更新★
- **境內(SITCA)**：滿量建庫，掃描 4700+ 檔、資料品質✅、勝率有數字。每日排程。
- **境外(cnyes+yfinance)**：3300+ 檔多年歷史、能回測。每日排程。
- **官方績效(MoneyDJ)**：7116 檔報酬+風險+排名，每日排程 → data/performance.csv。
- app(Streamlit Cloud)讀靜態庫；新鮮度靠 GitHub Actions 排程（全部每日/工作日）。

## ★App 結構（6 tab，Allianz 風格）★
1. 🛡️系統檢核：資料源完整度表(境外讀coverage.json)、資料品質、排程狀態(GitHub API)、
   境內完整度說明(36投信清單)、邏輯檢核、SITCA連線測試(摺疊,雲端404屬正常)。
2. ☀️每早掃描：全市場滾動10日跌幅，篩選(境內外/發行公司/系列/資產/區域/門檻)、
   可排序、凍結表頭、下載。發行公司=官方代碼、系列=品牌。
3. 🔍個別基金分析：篩選選檔→官方績效卡(MoneyDJ)+母專案8表(勝率/報酬/累積/進場時機/
   回撤/年度/連續觸發)+plotly走勢圖(各門檻標記)。引擎=mp_analysis.py。
4. 🆚同類型比較：同類並排比滾動跌幅+歷史勝率+官方績效欄。
5. 🏆績效Ranking：MoneyDJ官方榜，篩選同個別分析、報酬紅綠熱圖、名次、下載。
6. 📝筆記：手動筆記，下載/上傳保存(雲端重啟會清空)。

## ★核心檔案與相依（清理後）★
- app.py（主程式，import mp_analysis）
- mp_analysis.py（個別分析引擎，母專案移植+跨度過濾）
- build_sitca.py + verify_build.py（境內建庫，build_sitca.yml呼叫兩者）+ topup_daily.yml
- build_offshore.py + merge_offshore.py（境外建庫，build_offshore.yml）
- build_performance.py（績效，build_performance.yml）
- .streamlit/config.toml（Allianz主題 primaryColor=#003781）
- data/（所有CSV + coverage_offshore.json + performance.csv）
- 憲法 *.md、發行公司對照表.md（文件）
- 已刪：cnyes_probe/probe_offshore(+yml)、test_logic、舊筆記（純測試探針）

## ★發行公司/系列：官方 TDCC ISIN 對照（鐵律32，重要）★
- 不用基金名猜品牌(打地鼠已淘汰)。build_offshore 抓 TDCC opendata(id=3-4)，
  用 ISIN 對每檔官方「總代理→發行公司」「境外基金機構→系列」。
- 系列細分到 AGIF/DIT/HORIZON/CAPITAL/SICAV（安聯AGIF vs 安聯DIT 分得出）。
- 投顧併投信：富蘭克林→A0045、國泰→A0037、永豐→A0025。荷寶/利安→台新A0047、
  紐約梅隆→合庫A0048。併購標註：A0014新光(併台新)、A0020日盛(併富邦)。
- app 載入：官方值優先，TDCC未涵蓋的舊基金用基金名寬鬆比對備援。

## ★踩過的真相（血淚，別重犯）★
- SITCA POST 從 Streamlit Cloud 回404→app即時抓不可行，靠排程。sleep_sec=1.5(0.3被丟包)。
- 單日偏低=美股休市非bug。改parallel先清data。
- 境外免費源天花板~79%(yfinance歷史)，非cnyes漏檔；缺的多是非美元平行類股。
- 大CSV按年切檔(帶年份命名)否則app無法只載部分年份→OOM。commit後回repo親眼確認(job綠≠push成功)。
- Streamlit ~1GB RAM：載入只讀選定年份+usecols+category，預設最近2年；個別/比較只讀選中檔。
  系統檢核曾因全載整庫→OOM(Oh no,log停在Uvicorn started無traceback)，已改2年+按需讀。
- -10%門檻對境外太嚴→勝率常留空(正常)；調-3~-5%就有觸發。最佳持有天有樂觀偏誤。
- MoneyDJ類別選單是JS(HTML抓不到)→暴力枚舉類別碼。Big5編碼。
- 績效join：MoneyDJ用名稱、我們用cnyes碼→靠名稱寬鬆比對(去類股後綴)，非100%命中。

## ★平台硬限制（憲法鐵律25-29）★
GitHub單檔100MB(切80MB、帶年份命名)；Streamlit~1GB RAM(省記憶體載入)；
資料深度≠一次載入量(庫全年份可回測,2年只是預設載入)；即時更新放後端排程。

## 日常維運
- 每天不用做事：三個排程(境內每日/境外每日/績效每日)自動更新。
- 一週瞄一次 Actions 綠不綠，或看系統檢核的排程狀態按鈕。連續紅才看log。
- app掛(Oh no)→Manage app→Reboot。「掃描前即時補最新」雲端不勾(SITCA擋)。
- 新投信不在36家清單→告訴AI補SITCA_COMPANIES。績效對不到→告訴AI調寬鬆比對。

## Greg 偏好（鐵律17：功勞誠實歸屬）
不當測試員、全市場不縮範圍、UI滿版不要藏欄、每檔用自己最新NAV日不對齊、
走GitHub網頁不碰終端機、交付整份完整檔、投資/設計/資料源要多方辯證。
★方向常來自Greg領域直覺，AI如實標示功勞歸屬。★

## 待辦 / 可選增強
- 境內風險指標(標準差/Sharpe)：MoneyDJ依類型頁境內僅報酬，可加抓人氣排行頁合併。
- 績效join命中率再優化(理想用ISIN,但MoneyDJ排名頁無ISIN)。
- 回測最佳持有天樂觀偏誤修正、排程失敗通知、升級RAM支援全年份回測。
