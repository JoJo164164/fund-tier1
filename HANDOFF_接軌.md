# 專案接軌檔（2026-07-30 對話結束）

## repo: github.com/jojo164164/fund-tier1

## ★本則對話破解的關鍵bug（最重要，勿重踩）★
症狀：app每早掃描顯示「資料稀疏(62天)」，4553/4553檔全作廢，勝率空白。
查證：sitca_nav_2026.csv 只有33個日期、間隔6天（應140天連續）。
根因：build_sitca.py 的 csv_path_for() 分年檔名不帶segment後綴，
      4個平行job在各自獨立機器都寫同名 sitca_nav_2026.csv，
      merge時互相覆蓋 → 只剩最後1個job的1/4資料（33≈140/4，間隔6天）。
已修（本則已完成，待部署驗證）：
  - build_sitca.py: csv_path_for() 分段時回傳 sitca_nav_{年}_seg{NN}.csv
  - app.py: HIST_SITCA_GLOB = "data/sitca_nav_[0-9][0-9][0-9][0-9]*.csv"（讀含seg的分年檔）
  - 自測：4段各寫各檔不覆蓋、單段維持原名、glob正確合併 全通過

## 下一步（新對話接手第一件事）
1. 覆蓋 build_sitca.py + app.py（本則修正版，在outputs）
2. 刪掉 repo/data 裡舊的 sitca_nav_2021~2026.csv（那是被覆蓋只剩1/4的壞資料）
   和 sitca_progress_seg*.json
3. 重跑建庫：ALL / days_back 1250 / parallel 4 / skip_recent 2 / sleep 1.0
4. 這次分年檔會帶_seg後綴、merge保留全部、app glob合併 → 跨度應回14天
5. 驗收：app每早掃描→歷史深度「最近2年」→資料品質應「✅正常」、勝率有數字

## 已完成
- SITCA建庫機制（ALL模式一次抓全市場4400檔、平行分段、斷點續傳、CSV反推進度）
- app.py新UI（1250行，7 tabs，全寬去iframe，個別基金分析tab，動態高度表格）
- 憲法v4、PENDING待辦檔

## 待辦（PENDING_每早補最新.md）
1. 即時補最新（掃描前即時抓最近幾天，境內拿今天/境外拿它最新）
2. 每日自動排程（GitHub Actions schedule cron）
3. 境外建庫（build_offshore.py 參數自動偵測版，看log偵測結果決定成敗）

## Greg偏好
不當測試員、要全市場所有基金不縮範圍、UI要整頁不要iframe、
每檔用自己最新NAV標日期不對齊、走GitHub網頁操作不碰終端機。
