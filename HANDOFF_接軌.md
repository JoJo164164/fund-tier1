# 專案接軌檔（2026-08-06 更新）

## repo: github.com/jojo164164/fund-tier1

## ★本輪成果（資料稀疏62天 bug 正式結案）★
症狀回顧：app 每早掃描顯示「資料稀疏(62天)」、4553 檔全作廢、勝率空白。
根因鏈與修法（全部已部署 + 實測通過）：

1. **分段檔互相覆蓋（根因）** — `build_sitca.py` 的 `csv_path_for()` 分年檔不帶
   segment 後綴，4 個平行 job 各寫同名 `sitca_nav_2026.csv`，merge 互相覆蓋 →
   只剩 1/4 的「日期」（不是 1/4 基金！是時間軸 140→33 天、每第4個營業日）。
   修法：分段時檔名帶 `_seg{NN}` 後綴；app `HIST_SITCA_GLOB` 讀含 seg 的分年檔。
   驗證：33 個殘存日期 index 全部 `mod 4 == 0`，是單一 segment 的完美指紋。

2. **CI 合流會丟資料** — merge 步原用 `cp -rn`（no-clobber）：先煙霧後全量/斷點續傳時，
   舊分段檔已存在 → 跳過覆蓋 → 完整版被丟。修法：改 `cp -rf`（build 一定是
   checkout→append，artifact 永遠是超集，合併應無條件以 artifact 為準）。

3. **加了建庫驗證護欄** — `verify_build.py`（repo 根目錄），merge commit 前執行，
   四條斷言，缺段/覆蓋不足/近端稀疏/單日抓取斷掉就 fail、不准 commit：
   - 斷言1：SEG_TOTAL 段全到齊（防某段 artifact 掉了）
   - 斷言2：日期覆蓋率 ≥ 80%（防覆蓋復發 → 只剩 1/N 日期）
   - 斷言3：最近10筆曆日跨度 ≤ 25（鐵律16；直接對應「資料稀疏」）
   - 斷言4：單日筆數健檢（境外休市日合理稀疏放行、抓取斷掉<5%才 fail）

4. **抓取韌性（斷路器）** — `build_sitca.py` 加：連續 6 個「逾時例外」就中止本段
   (sys.exit 1)、指數退避、假日回空不誤判。防某台 runner IP 被 SITCA 丟包後、
   硬撞 350 分鐘 timeout 才停（實測曾浪費 3+ 小時）。

**滿量建庫成功**：`ALL / days_back 1250 / parallel 4 / skip_recent 2 / sleep_sec 1.5`，
run #15，總時 2h23m，各段約 2 小時。結果：掃描 4700 檔、資料品質全 ✅正常、
歷史勝率有數字、最佳持有天有數字。跨度回正常，鐵律16 通過。

## ★踩過的真相（勿再誤判）★
- **單日筆數偏低 ≠ bug = 美股/他國休市日**。境內外混合母體，遇美股休市（如
  2026-01-19 MLK、2026-07-03 國慶順延），投資美股的境外基金無淨值可算 →
  該日只剩 ~40%（缺口集中在美國區基金，已用資料驗證）。verify 斷言4 已放行此類、
  只擋 <5% 的真斷線。全量會出現多個 ⚠️ 提示（境外休市），屬正常。
- **sleep_sec 0.3 會被 SITCA 丟包**。EFFECTIVE_SLEEP = SLEEP × parallel，總請求率
  ≈ 1/SLEEP。0.3 = 每秒 3.3 次 → SITCA 丟包（曾 0/3 段活、1/2 段全 ReadTimeout）。
  **用 sleep_sec = 1.5**（總率 0.67/秒）+ 重跑拿新 runner IP，穩定通過。
- **改 parallel 要先清 data/**。改並行數會改 stride 分段、seg 檔名對不上，殘留舊
  seg 檔會害 verify「找到N段 ≠ 期望M段」誤擋。要改 parallel 就先刪光 data/ 全部
  seg csv + progress json（★CSV 與 progress.json 同進退，絕不半刪★）。

## ★待釐清/未完成的重要缺口（Q3，2026-08-06 確認）★
**掃描目前讀「靜態庫」、非「即時最新」**。`scan_history_db` 算滾動10日是從已 commit
的 CSV 取 `rolling[-1]`，`淨值截至` = 庫裡最新日期。所以資料新鮮度**完全取決於
build 何時跑**（上週跑 → 今天看就是 8 天前）。`skip_recent=2` 是建庫全體砍最近2天，
與「每檔用自己最新」方向相反。
→ **要達成「掃描當下用最新淨值（境內今天/境外它最新）」= PENDING 第1項，尚未做。**
   `fetch_prices` / `fetch_sitca_nav` 即時抓的函式已存在，但未接進掃描流程。

## ★UI 待修（Q2，一行）★
app.py 第957行 `_rows_h = min(len(result)*35+38, 3000)` 的 `3000` 上限，會讓 4700 列
壓成內部捲動框（iframe 感）。改 `_rows_h = len(result)*36 + 60`（拿掉上限）即整頁攤平。
→ 併入 PENDING#1 改 app.py 時一起交付完整檔，避免改兩次。

## 已完成
- SITCA 建庫機制（ALL 一次抓全市場、平行分段、斷點續傳、CSV 反推進度、
  seg 檔名、cp -rf 合流、verify 護欄、斷路器）
- app.py（7 tabs、全寬去 iframe、個別基金分析、動態高度表格〔待修上限〕）
- 憲法 v4、verify_build.py

## 待辦（PENDING_每早補最新.md）
1. **即時補最新（＝Q3，最高優先）** — 掃描前即時抓最近幾天，境內拿今天、境外拿它
   最新，append 到價格序列，讓滾動10日以「今天」收尾。做完順手併入 Q2 高度修正。
2. 每日自動排程（GitHub Actions schedule cron）→ 讓庫每天自己更新，新鮮度 ≤1 天。
3. 境外建庫（build_offshore.py 參數自動偵測版）。

## Greg 偏好
不當測試員、要全市場所有基金不縮範圍、UI 要整頁不要 iframe、
每檔用自己最新 NAV 標日期不對齊、走 GitHub 網頁操作不碰終端機、交付整份完整檔。
