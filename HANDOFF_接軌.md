# 專案接軌檔（2026-08-10 更新 · 境內外雙軌完整）

## repo: github.com/jojo164164/fund-tier1

## ★系統現況：境內＋境外雙軌完整、能回測、自動更新★
- **境內(SITCA)**：滿量建庫成功，掃描 4700+ 檔、資料品質✅、勝率有數字。
- **境外(cnyes+yfinance)**：3336 檔有多年歷史、能回測、勝率有數字。
- app(Streamlit Cloud)讀靜態庫；新鮮度靠 GitHub Actions 排程(境內每日/境外每週)。

## ★境內(SITCA)重點（前幾輪，已結案）★
- 根因鏈修法：seg 檔名(防覆蓋) + cp -rf(防合流丟資料) + verify_build.py(四斷言護欄)
  + 斷路器(連6逾時中止、指數退避、假日不誤判)。
- 每日排程 topup_daily.yml：台北21:00 補最新境內，庫≤1天新。
- 踩過真相：單日偏低=美股休市非bug；sleep_sec 用 1.5(0.3會被SITCA丟包)；
  改 parallel 要先清 data；SITCA POST 從 Streamlit Cloud 回404→app即時抓不可行。

## ★境外(cnyes+yfinance)重點（本輪，已結案）★
**方向來自使用者**：Greg 找到 cnyes 有每檔歷史。AI 負責驗證+落地。
**免費源窮舉結論**：官方無多年歷史(TDCC只7天、fundclear SPA、晨星擋、Bloomberg付費)。
  最佳解=cnyes 清單拿 0P 代碼 → yfinance 抓長歷史。
**建庫鏈**（build_offshore.py + build_offshore.yml + merge_offshore.py）：
  1. cnyes v2/search/fund 清單 → 每檔帶 fundClassId(=Yahoo 0P)、isin、sitca、名稱、區域。
  2. **sitca 欄位空=境外**(有值=境內)，用此篩境外(避開與SITCA境內重複)。
  3. yfinance 抓 fundClassId 的 max；非美元類股裸碼常無資料 → 試 .F/.SG/.DE/.MU 後綴救援。
  4. 平行分段(prepare列清單→matrix分段→merge)，比照境內。
**輸出**：data/offshore_nav_YYYY_NN.csv（按年切、超80MB的年再切子檔）。
  代碼=cnyesId(去逗號)、日期ISO、來源=yfinance。app 依年份 glob 只載選定年份。
**每週排程**：build_offshore.yml 加 schedule(週日台北22:00)自動重建。
**踩過真相**：
  - cnyes 淨值 table 端點鎖10筆/頁→無法bulk，改走清單0P→yfinance。
  - offshore 單檔~200MB > GitHub 100MB → push被拒(job仍綠!)→按年切檔+子檔。
  - 切檔命名要帶年份(非純序號)，否則 app 無法只載部分年份→OOM。
  - 涵蓋率~60-70%(3336檔，含主流美元類股)=免費天花板；其餘標資料稀疏。
  - 深度依基金年齡，0P約4.4年、老基金7.6年+，跨得過2022大跌。
  - **-10% 門檻對境外太嚴**：多數境外歷史觸發次數不足→勝率留空(正常，非bug)；
    調 -3~-5% 就有大量觸發+勝率。回測「最佳持有天」有樂觀偏誤，實際打折看。

## ★平台硬限制（憲法鐵律25-29，務必遵守）★
- GitHub 單檔上限 100MB(建議50MB)：大CSV切檔(每檔80MB上限)，命名帶篩選鍵(年份)。
  commit後一定回repo親眼確認檔案在(job綠≠push成功)；刪舊檔要 git add -A。
- Streamlit Cloud ~1GB RAM：載入要「只讀選定年份+usecols+category」，預設最近2年。
  所有資料源都要套同一套省記憶體，缺一個就OOM(Oh no)。
- 「資料深度」≠「一次載入量」：庫裡全年份都能回測；2年只是免費主機預設載入量，
  可調大(3/5/全部)或升級RAM。app環境≠建庫環境，即時更新放後端排程。

## 日常維運
- **每天不用做事**：境內排程每日、境外排程每週自動更新，打開app用「最近2年」掃描。
- **一週瞄一次** Actions 是否綠(SITCA每日 / 境外每週)。連續紅才看log。
- app掛掉(Oh no)→ Manage app(右下)或 share.streamlit.io → Reboot app。
- 「掃描前即時補最新」勾選：雲端永遠不勾(SITCA擋)；台灣本機跑app才勾。

## 已完成
- 境內建庫+每日排程；境外建庫(cnyes+yfinance)+每週排程；app雙軌讀取(省記憶體)。
- verify_build.py、憲法v4(含新增鐵律17-29)。

## 待辦（PENDING）
- 全部主線完成。下一階段：**全站 UI/UX review**（逐個tab優化版面/動線/可讀性）。
- 可選增強：回測「最佳持有天」樂觀偏誤修正、境外分類關鍵字校準、排程失敗通知、
  app升級RAM以支援全年份回測。

## Greg 偏好
不當測試員、全市場不縮範圍、UI整頁不要iframe、每檔用自己最新NAV日期不對齊、
走GitHub網頁不碰終端機、交付整份完整檔。
★方向常來自Greg的領域直覺，AI要如實標示功勞歸屬(鐵律17)。★
