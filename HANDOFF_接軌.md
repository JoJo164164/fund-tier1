# 專案接軌檔（2026-08-06 更新 · 資料稀疏bug結案＋新鮮度方案B上線）

## repo: github.com/jojo164164/fund-tier1

## ★本輪成果（資料稀疏62天 bug 正式結案）★
症狀回顧：app 每早掃描顯示「資料稀疏(62天)」、4553 檔全作廢、勝率空白。
根因鏈與修法（全部已部署 + 實測通過，滿量 1250 天建庫成功、掃描 4700 檔全 ✅正常）：

1. **分段檔互相覆蓋（根因）** — `csv_path_for()` 分年檔不帶 segment 後綴，4 個平行 job
   各寫同名檔、merge 互相覆蓋 → 只剩 1/4 的「日期」(時間軸140→33天、每第4個營業日)。
   修法：分段檔名帶 `_seg{NN}`；app `HIST_SITCA_GLOB` 讀含 seg 的分年檔。
2. **CI 合流會丟資料** — merge 步 `cp -rn`(no-clobber) 會跳過覆蓋 → 完整版被丟。
   修法：改 `cp -rf`（artifact 永遠是超集，合併無條件以 artifact 為準）。
3. **建庫驗證護欄** — `verify_build.py`，merge commit 前跑四斷言：段齊/覆蓋≥80%/
   最近10筆跨度≤25/單日筆數健檢，不過就 fail、不准 commit。
4. **抓取韌性（斷路器）** — build_sitca.py：連續6個逾時例外→中止本段、指數退避、
   假日回空不誤判。防 runner IP 被 SITCA 丟包後硬撞 350 分鐘 timeout。

## ★資料新鮮度：方案A失敗 → 改方案B（已上線）★
需求(Q3)：掃描時要用「當下最新淨值」，不是停在建庫日。
- **方案A(app 掃描前即時抓)試過、失敗**：Streamlit Cloud 打 SITCA 的 POST 被回
  **404**(WAF/環境擋，非碼問題；GET拿token成功、POST 404、與 payload 無關)。
  同樣抓取在 GitHub Actions 可成、在 app 部署環境不可成 → A 在此部署走不通。
- **方案B(每日排程補最新)＝現行解**：`topup_daily.yml`(在 .github/workflows/)，
  週一~五台北21:00 自動跑，抓最近7個營業日境內全市場、commit 進庫。
  用 `SEGMENT_TOTAL=1` 寫 `sitca_nav_{年}.csv`(非seg)，靠 load_progress 讀既有 seg
  歷史檔自動跳過舊資料，不重抓整庫、不動全量 seg 檔。app glob 同時讀 seg 與非 seg。
  → app 讀靜態庫即永遠 ≤1 天新，Q3 目標用「排程」達成(非 app 現抓)。
- app.py 的「即時補最新」勾選框**預設關閉**(此環境會404)；哪天在能連 SITCA 的環境
  (台灣本機)跑 app，可勾開，届時 fetch 已修正(ALL 送空字串"")會正確運作。
- 境外(境外基金)無即時源，一律用庫最新 → 屬 PENDING#3。

## ★UI：Q2 表格攤平已修★
原用 `st.dataframe(height=列數×36)`：4700列=16.9萬px→render失敗變空白。
改**靜態HTML表格**(`to_html`+`st.markdown`)：整頁捲動、表頭sticky、無 iframe 內捲軸。
欄位滑鼠提示改放「欄位說明」expander。

## ★踩過的真相（勿再誤判）★
- **單日筆數偏低 ≠ bug = 美股/他國休市日**：境內外混合母體，美股休市(如01-19 MLK、
  07-03國慶)時投資美股的境外基金無淨值→該日只剩~40%(缺口集中美國區，已驗證)。
  verify 斷言4 已放行此類、只擋 <5% 的真斷線。
- **sleep_sec 0.3 會被 SITCA 丟包**：總請求率≈1/SLEEP。0.3=3.3次/秒→丟包(0/3活1/2死)。
  **用 sleep_sec=1.5**(0.67次/秒)+重跑拿新IP，穩定通過。
- **SITCA 擋 Streamlit Cloud 的 POST(回404)**：故 app 端即時抓不可行，新鮮度靠排程。
- **改 parallel 要先清 data/**：改並行數會改 stride、seg 檔名對不上→verify 誤擋。
  要改就先刪光 data/ 全部 seg csv + progress json(★CSV與progress同進退，絕不半刪★)。

## ★維運說明（方案B 上線後，日常怎麼做）★
**每天：不用做任何事**。排程自動補，早上打開 app 按「掃描今日觸發」即為 ≤1 天新。
**一週瞄一次**：GitHub → Actions →「每日補最新 SITCA 淨值」看最近幾次是否綠。
  - 綠 → 免動作。
  - 偶爾一天紅(假日/SITCA抽風) → 免理，隔天7天窗口自動補回(days_back=7的容錯)。
  - 連續多天紅 → 才看 log。多為 SITCA 改版(解析失敗)或排程被停用，貼 log 給下一手。
  - 註：repo 若 60 天無 push，GitHub 會自動停排程；但每天有 commit 就不會觸發。
**需要時才手動**：
  - 想立刻更新不等21:00 → Actions →「每日補最新」→ Run workflow。
  - 庫落後太多(排程停很久/淨值截至停在很舊) → 手動跑全量 `build_sitca.yml`
    (days_back 1250 / parallel 4 / skip_recent 2 / sleep_sec 1.5)重建整庫。
  - SITCA 改版導致抓取壞 → 需改碼，貼失敗 log。

## 已完成
- SITCA 建庫(seg檔名/cp -rf/verify護欄/斷路器)、滿量1250天建庫成功
- 資料新鮮度方案B：topup_daily.yml 每日排程補最新(＝原PENDING#1即時補+#2排程)
- app.py：即時補最新函式(app端因環境404預設關)、表格攤平(HTML)、ALL送空字串修正
- verify_build.py、憲法v4

## 待辦（PENDING）
1. ~~即時補最新~~ → 已用方案B(排程)達成。
2. ~~每日自動排程~~ → 已由 topup_daily.yml 達成。
3. **境外建庫(build_offshore.py 參數自動偵測版)** — 唯一剩下的主線待辦；
   完成後境外基金也能有最新/歷史，掃描的境外那半才完整。
（延伸可選：排程失敗的通知、境外每日補最新、app 端即時抓改用可連 SITCA 的代理。）

## Greg 偏好
不當測試員、要全市場所有基金不縮範圍、UI要整頁不要iframe、
每檔用自己最新NAV標日期不對齊、走GitHub網頁操作不碰終端機、交付整份完整檔。
