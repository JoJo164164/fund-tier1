# 台灣基金滾動跌幅系統 — Tier1（被動ETF）

把股票版「滾動跌幅觸發 → 回測勝率 → 進場時機 → 追蹤日誌驗證」方法移植到基金標的。
本 repo 為 **Tier1：被動ETF**。

## 快速部署

```bash
git init && git add . && git commit -m "feat: Tier1 被動ETF v0.1"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<repo名>.git
git push -u origin main
```

再到 https://share.streamlit.io → New app → 選此 repo → Main file: `app.py` → Deploy。

## 已實作的憲法鐵律

| 鐵律 | 內容 | 實作位置 |
|------|------|---------|
| 12 | 費用/entry_lag 參數化、預設0、嚴禁寫死 | `run_full_backtest()` |
| 14 | 配息必須還原 | `fetch_prices(adjust=True)` |
| 16 | 10筆視窗 + 曆日跨度記錄 + 跨度上限作廢 | `calc_all_rolling_returns()` |
| 九 | 主動ETF（6碼第6碼A/D）只收資料不出結論 | `classify_etf()` / `is_tier1()` |

## 已知偏誤（供歸因，非待辦）

- `entry_lag=0` 對**共同基金**假設「觸發日可成交」；基金淨值收盤後公告，實務上不可得。
  **Tier1（ETF）有盤中價，lag=0 可成交，無此問題。**
- 費用未計入（使用者裁決）。因回測保存原始報酬序列，日後扣費可純後處理，無需重跑。

## 部署出問題時

**第一件事是要 log，不要猜**（憲法鐵律9）：Manage app → Download log。
母專案教訓：沒看 log 連改 3~4 版 requirements 全沒中，最後 log 顯示真兇是 pandas 被升到 3.0.3。

## 測試

```bash
python test_logic.py     # 22 項邏輯測試，不需網路
```
