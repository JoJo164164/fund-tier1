# -*- coding: utf-8 -*-
"""母專案個別分析引擎（移植進 fund 版）
=====================================================
來源：母專案 app__10_.py（股票版）的個別分析函式，邏輯逐字保留。
fund 版唯一改動：calc_all_rolling_returns 加「跨度上限過濾」(鐵律16)——
  基金/ETF 非營業日各異，10筆曆日跨度>max_span 代表有大缺口→跳過(防假訊號)。
其餘（run_full_backtest、build_*、色彩/熱圖）與母專案一致。
"""
import pandas as pd
import streamlit as st

THRESHOLDS = [-5, -7, -10, -15, -20]
HORIZONS = [5, 10, 20, 40, 60, 80, 100, 120, 240]
MAX_SPAN_DAYS = 25   # 鐵律16：滾動10筆的曆日跨度上限


def calc_all_rolling_returns(prices_dict, max_span=MAX_SPAN_DAYS):
    """★fund 版：加跨度過濾。10筆曆日跨度>max_span → 有大缺口，跳過(假訊號防護)。"""
    if len(prices_dict) < 11:
        return []
    dates = sorted(prices_dict.keys())
    results = []
    for i in range(10, len(dates)):
        base_date = dates[i - 10]
        curr_date = dates[i]
        try:
            span = (pd.to_datetime(curr_date) - pd.to_datetime(base_date)).days
        except Exception:
            span = 0
        if max_span and span > max_span:
            continue                      # 大缺口→這筆不算(fund 差異點)
        base_price = prices_dict[base_date]
        curr_price = prices_dict[curr_date]
        if base_price > 0:
            ret = (curr_price - base_price) / base_price * 100
            results.append({
                "date": curr_date, "base_date": base_date,
                "base_price": base_price, "curr_price": curr_price,
                "return": round(ret, 2),
            })
    return results


def calc_consecutive_trigger_days(prices_dict, threshold):
    if len(prices_dict) < 11:
        return 0
    dates = sorted(prices_dict.keys())
    consec = 0
    for i in range(len(dates) - 1, 9, -1):
        base_price = prices_dict[dates[i - 10]]
        curr_price = prices_dict[dates[i]]
        if base_price > 0:
            ret = (curr_price - base_price) / base_price * 100
            if ret <= threshold:
                consec += 1
            else:
                break
        else:
            break
    return consec


def run_full_backtest(prices_dict, threshold, precomputed_rolling=None):
    # 效能重構（2026-07-09，perf/rolling-return-vectorize）：
    # calc_all_rolling_returns(prices_dict) 完全不依賴 threshold，
    # 舊版每呼叫一次 run_full_backtest 就重算一次，同一檔股票對 5 個門檻＝重複算 5 次。
    # 新增 precomputed_rolling 參數：呼叫端可在門檻迴圈外算好一次、傳進來重複使用；
    # 不傳（None）時行為與舊版完全一致，故所有既有呼叫點無需改動。
    rolling = precomputed_rolling if precomputed_rolling is not None else calc_all_rolling_returns(prices_dict)
    if not rolling:
        return None
    dates = sorted(prices_dict.keys())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    triggers = [r for r in rolling if r["return"] <= threshold]
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
    # 新增：記錄最大回撤發生的天數
    horizon_dd_days = {h: [] for h in HORIZONS}

    for t in triggers:
        idx = date_to_idx.get(t["date"])
        if idx is None:
            continue
        entry_price = t["curr_price"]
        year = t["date"][:4]
        for h in HORIZONS:
            future_idx = idx + h
            if future_idx < len(dates):
                future_price = prices_dict[dates[future_idx]]
                ret = (future_price - entry_price) / entry_price * 100
                horizon_rets[h].append({
                    "ret": round(ret, 2),
                    "year": year,
                    "date": t["date"],
                    "entry_price": entry_price,
                    "future_price": future_price,
                })
                min_ret = 0.0
                min_day = 0
                for d in range(1, h + 1):
                    fi = idx + d
                    if fi < len(dates):
                        p = prices_dict[dates[fi]]
                        r = (p - entry_price) / entry_price * 100
                        if r < min_ret:
                            min_ret = r
                            min_day = d
                horizon_drawdowns[h].append({"dd": round(min_ret, 2), "year": year})
                horizon_dd_days[h].append(min_day)

    yearly = {}
    for t in triggers:
        year = t["date"][:4]
        if year not in yearly:
            yearly[year] = {"trigger_dates": set(), "max_consec": 0,
                            "rets": {hh: [] for hh in HORIZONS},
                            "dds": {hh: [] for hh in HORIZONS}}
        yearly[year]["trigger_dates"].add(t["date"])

    for h in HORIZONS:
        for item in horizon_rets[h]:
            y = item["year"]
            if y in yearly:
                yearly[y]["rets"][h].append(item["ret"])
        for item in horizon_drawdowns[h]:
            y = item["year"]
            if y in yearly:
                yearly[y]["dds"][h].append(item["dd"])

    for year in yearly:
        mc = cc = 0
        for r in rolling:
            if r["date"][:4] == year and r["date"] in yearly[year]["trigger_dates"]:
                cc += 1
                mc = max(mc, cc)
            elif r["date"][:4] == year:
                cc = 0
        yearly[year]["max_consec"] = mc

    return {
        "triggers": triggers, "trigger_dates": list(trigger_dates),
        "max_consecutive": max_consecutive,
        "horizon_rets": horizon_rets,
        "horizon_drawdowns": horizon_drawdowns,
        "horizon_dd_days": horizon_dd_days,
        "yearly": yearly, "total": len(triggers)
    }


def color_ret(val):
    if val is None or str(val) in ["", "---", "待觀察"]:
        return ""
    try:
        v = float(str(val).replace("%", ""))
        return "color: red; font-weight: bold" if v > 0 else "color: green; font-weight: bold"
    except:
        return ""


def color_dd(val):
    if val is None or str(val) in ["", "---", "待觀察"]:
        return ""
    try:
        v = float(str(val).replace("%", ""))
        return "color: green; font-weight: bold" if v < 0 else "color: red; font-weight: bold"
    except:
        return ""


def color_winrate(val):
    if val is None or str(val) in ["", "---", "待觀察"]:
        return ""
    try:
        v = float(str(val).replace("%", "").replace("⚠️", ""))
        if v >= 80:
            return "background-color: #F86200; color: white; font-weight: bold"
        return ""
    except:
        return ""


def color_winrate_80only(val):
    """連續觸發勝率表專用：只塗 ≥80% 的格子，其他不塗色"""
    if val is None or str(val) in ["", "---", "待觀察"]:
        return ""
    try:
        raw = str(val).replace("⚠️", "").replace("%", "").strip()
        v = float(raw)
        if v >= 80:
            return "background-color: #F86200; color: white; font-weight: bold"
        return "color: #414141"
    except:
        return ""


def heatmap_positive(df, cols):
    all_vals = []
    for c in cols:
        for v in df[c]:
            try:
                all_vals.append(float(str(v).replace("%", "")))
            except:
                pass
    max_pos = max((v for v in all_vals if v > 0), default=1)
    min_neg = min((v for v in all_vals if v < 0), default=-1)

    def cell_style(val):
        if val is None or str(val) in ["", "---", "待觀察"]:
            return ""
        try:
            v = float(str(val).replace("%", ""))
            if v > 0:
                intensity = min(v / max_pos, 1.0)
                r = int(255 - intensity * 115)
                g = int(224 - intensity * 224)
                b = int(224 - intensity * 224)
                text = "white" if intensity > 0.55 else "#8B0000"
                return "background-color: rgb({},{},{}); color: {}; font-weight: bold".format(r, g, b, text)
            elif v < 0:
                intensity = min(abs(v) / abs(min_neg), 1.0)
                r = int(224 - intensity * 224)
                g = int(255 - intensity * 111)
                b = int(224 - intensity * 224)
                text = "white" if intensity > 0.55 else "#006400"
                return "background-color: rgb({},{},{}); color: {}; font-weight: bold".format(r, g, b, text)
            return ""
        except:
            return ""
    return df.style.map(cell_style, subset=cols)


def heatmap_negative(df, cols):
    all_vals = []
    for c in cols:
        for v in df[c]:
            try:
                all_vals.append(float(str(v).replace("%", "")))
            except:
                pass
    min_neg = min((v for v in all_vals if v < 0), default=-1)

    def cell_style(val):
        if val is None or str(val) in ["", "---", "待觀察"]:
            return ""
        try:
            v = float(str(val).replace("%", ""))
            if v < 0:
                intensity = min(abs(v) / abs(min_neg), 1.0)
                r = int(255)
                g = int(255 - intensity * 180)
                b = int(200 - intensity * 200)
                text = "white" if intensity > 0.65 else "#8B2500"
                return "background-color: rgb({},{},{}); color: {}; font-weight: bold".format(r, g, b, text)
            return ""
        except:
            return ""
    return df.style.map(cell_style, subset=cols)


def fmt(v):
    return "待觀察" if v is None else "{:.2f}%".format(v)


def show_html(s):
    """輸出 DataFrame 為 HTML 表格，表頭不斷行、字體 14px，文字靠左"""
    html = s.to_html(index=False)
    styled = """
<style>
.stbl { border-collapse: collapse; width: 100%; font-size: 14px; }
.stbl th { background: #003781; color: white; padding: 8px 14px;
           white-space: nowrap; text-align: left; font-size: 13px; font-weight: 600; }
.stbl td { padding: 8px 14px; border-bottom: 1px solid #e0e0e0;
           white-space: nowrap; text-align: left; font-size: 14px; }
.stbl tr:hover td { background: #f0f4ff; }
</style>
""" + html.replace('<table', '<table class="stbl"')
    st.markdown(styled, unsafe_allow_html=True)


def safe_md(text):
    """輸出 markdown 前，把半形 ～ 換成全形 ～，避免 Streamlit 誤判為刪除線"""
    return text.replace("～", "～")



def build_summary_tables(prices_dict):
    win_rows, avg_rows, dd_rows = [], [], []
    for thr in THRESHOLDS:
        result = run_full_backtest(prices_dict, thr)
        win_row = {"觸發門檻": str(thr) + "%", "樣本數": 0 if result is None else result["total"]}
        avg_row = {"觸發門檻": str(thr) + "%", "樣本數": 0 if result is None else result["total"]}
        dd_row = {"觸發門檻": str(thr) + "%", "樣本數": 0 if result is None else result["total"]}
        for h in HORIZONS:
            col_w = str(h) + "天勝率"
            col_a = str(h) + "天平均報酬%"
            col_d = str(h) + "天平均最大回撤%"
            if result is None:
                win_row[col_w] = avg_row[col_a] = dd_row[col_d] = "---"
            else:
                rets = [x["ret"] for x in result["horizon_rets"][h]]
                dds = [x["dd"] for x in result["horizon_drawdowns"][h]]
                if not rets:
                    win_row[col_w] = avg_row[col_a] = dd_row[col_d] = "待觀察"
                else:
                    wins = sum(1 for r in rets if r > 0)
                    win_row[col_w] = "{:.2f}%".format(wins / len(rets) * 100)
                    avg_row[col_a] = "{:.2f}%".format(sum(rets) / len(rets))
                    dd_row[col_d] = "{:.2f}%".format(sum(dds) / len(dds))
        win_rows.append(win_row)
        avg_rows.append(avg_row)
        dd_rows.append(dd_row)
    return pd.DataFrame(win_rows), pd.DataFrame(avg_rows), pd.DataFrame(dd_rows)


def build_yearly_table(prices_dict, threshold):
    result = run_full_backtest(prices_dict, threshold)
    if not result:
        return None, None
    rows = []
    for year in sorted(result["yearly"].keys()):
        y = result["yearly"][year]
        row = {"年度": year, "觸發次數": len(y["trigger_dates"]), "最長連續觸發": y["max_consec"]}
        for h in HORIZONS:
            rets = y["rets"][h]
            row[str(h) + "天平均%"] = fmt(round(sum(rets) / len(rets), 2) if rets else None)
        rows.append(row)
    total_row = {"年度": "合計/平均", "觸發次數": result["total"], "最長連續觸發": result["max_consecutive"]}
    for h in HORIZONS:
        rets = [x["ret"] for x in result["horizon_rets"][h]]
        total_row[str(h) + "天平均%"] = fmt(round(sum(rets) / len(rets), 2) if rets else None)
    rows.append(total_row)
    return pd.DataFrame(rows), result


def build_yearly_cumulative_table(prices_dict, threshold):
    """年度實際累積損益%（按股價進場）：Σ(出場價-進場價)/Σ進場價 × 100，依年度分組"""
    result = run_full_backtest(prices_dict, threshold)
    if not result:
        return None

    rows = []
    for year in sorted(result["yearly"].keys()):
        row = {"年度": year,
               "觸發次數": len(result["yearly"][year]["trigger_dates"]),
               "最長連續觸發": result["yearly"][year]["max_consec"]}
        for h in HORIZONS:
            items = [x for x in result["horizon_rets"][h] if x["year"] == year]
            if items:
                total_entry  = sum(x["entry_price"]  for x in items)
                total_future = sum(x["future_price"] for x in items)
                cum = (total_future - total_entry) / total_entry * 100 if total_entry > 0 else None
                row[str(h) + "天累積%"] = fmt(round(cum, 2)) if cum is not None else "---"
            else:
                row[str(h) + "天累積%"] = "待觀察"
        rows.append(row)

    # 合計列
    total_row = {"年度": "合計", "觸發次數": result["total"],
                 "最長連續觸發": result["max_consecutive"]}
    for h in HORIZONS:
        items = result["horizon_rets"][h]
        if items:
            total_entry  = sum(x["entry_price"]  for x in items)
            total_future = sum(x["future_price"] for x in items)
            cum = (total_future - total_entry) / total_entry * 100 if total_entry > 0 else None
            total_row[str(h) + "天累積%"] = fmt(round(cum, 2)) if cum is not None else "---"
        else:
            total_row[str(h) + "天累積%"] = "---"
    rows.append(total_row)
    return pd.DataFrame(rows)


def build_entry_timing_table(prices_dict, threshold, horizon):
    rolling = calc_all_rolling_returns(prices_dict)
    if not rolling:
        return None
    dates = sorted(prices_dict.keys())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    triggers = [r for r in rolling if r["return"] <= threshold]
    if not triggers:
        return None
    trigger_date_set = set(t["date"] for t in triggers)
    consec_day = {}
    cc = 0
    for r in rolling:
        if r["date"] in trigger_date_set:
            cc += 1
            consec_day[r["date"]] = cc
        else:
            cc = 0
    groups = {"連續第1天進場": [], "連續第2天進場": [], "連續第3天以後進場": [], "連續結束翌日進場": []}
    rolling_list = list(rolling)
    for i, r in enumerate(rolling_list):
        d = r["date"]
        if d not in trigger_date_set:
            if i > 0 and rolling_list[i - 1]["date"] in trigger_date_set:
                idx = date_to_idx.get(d)
                if idx is None:
                    continue
                ep = r["curr_price"]
                fi = idx + horizon
                ret = round((prices_dict[dates[fi]] - ep) / ep * 100, 2) if fi < len(dates) else None
                groups["連續結束翌日進場"].append(ret)
        else:
            day_num = consec_day.get(d, 1)
            idx = date_to_idx.get(d)
            if idx is None:
                continue
            ep = r["curr_price"]
            fi = idx + horizon
            ret = round((prices_dict[dates[fi]] - ep) / ep * 100, 2) if fi < len(dates) else None
            if day_num == 1:
                groups["連續第1天進場"].append(ret)
            elif day_num == 2:
                groups["連續第2天進場"].append(ret)
            else:
                groups["連續第3天以後進場"].append(ret)
    rows = []
    for gname, rets_raw in groups.items():
        rets = [r for r in rets_raw if r is not None]
        row = {"進場時機": gname, "樣本數": len(rets_raw)}
        if not rets:
            row["勝率"] = "---"
            row["平均報酬%"] = "---"
            row["累積報酬%"] = "---"
        else:
            wins = sum(1 for r in rets if r > 0)
            row["勝率"] = "{:.2f}%".format(wins / len(rets) * 100)
            row["平均報酬%"] = "{:.2f}%".format(sum(rets) / len(rets))
            row["累積報酬%"] = "{:.2f}%".format(sum(rets))
        rows.append(row)
    return pd.DataFrame(rows)


def build_dd_timing_table(prices_dict, threshold):
    """表E增強：最大回撤 + 平均發生天數"""
    result = run_full_backtest(prices_dict, threshold)
    if not result:
        return None
    rows = []
    for h in HORIZONS:
        dds = [x["dd"] for x in result["horizon_drawdowns"][h]]
        dd_days = result["horizon_dd_days"][h]
        if not dds:
            rows.append({"觀察天數": str(h) + "天", "樣本數": 0,
                         "平均最大回撤%": "待觀察", "最深回撤%": "待觀察",
                         "平均回撤發生於第幾天": "待觀察"})
        else:
            valid_days = [d for d in dd_days if d > 0]
            rows.append({
                "觀察天數": str(h) + "天",
                "樣本數": len(dds),
                "平均最大回撤%": "{:.2f}%".format(sum(dds) / len(dds)),
                "最深回撤%": "{:.2f}%".format(min(dds)),
                "平均回撤發生於第幾天": "{:.1f}天".format(sum(valid_days) / len(valid_days)) if valid_days else "無回撤",
            })
    return pd.DataFrame(rows)


def build_consec_analysis(prices_dict, threshold, horizon):
    """連續觸發天數 vs 後續報酬交叉分析"""
    rolling = calc_all_rolling_returns(prices_dict)
    if not rolling:
        return None
    dates = sorted(prices_dict.keys())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    triggers = [r for r in rolling if r["return"] <= threshold]
    if not triggers:
        return None
    trigger_date_set = set(t["date"] for t in triggers)
    consec_day = {}
    cc = 0
    for r in rolling:
        if r["date"] in trigger_date_set:
            cc += 1
            consec_day[r["date"]] = cc
        else:
            cc = 0

    buckets = {"第1天": [], "第2天": [], "第3天": [], "第4天以後": []}
    for t in triggers:
        dn = consec_day.get(t["date"], 1)
        idx = date_to_idx.get(t["date"])
        if idx is None:
            continue
        ep = t["curr_price"]
        fi = idx + horizon
        if fi >= len(dates):
            continue
        ret = round((prices_dict[dates[fi]] - ep) / ep * 100, 2)
        if dn == 1:
            buckets["第1天"].append(ret)
        elif dn == 2:
            buckets["第2天"].append(ret)
        elif dn == 3:
            buckets["第3天"].append(ret)
        else:
            buckets["第4天以後"].append(ret)

    rows = []
    for bucket, rets in buckets.items():
        if not rets:
            rows.append({"連續觸發天數": bucket, "樣本數": 0,
                         "勝率": "---", "平均報酬%": "---", "最佳報酬%": "---", "最差報酬%": "---"})
        else:
            wins = sum(1 for r in rets if r > 0)
            rows.append({
                "連續觸發天數": bucket,
                "樣本數": len(rets),
                "勝率": "{:.2f}%".format(wins / len(rets) * 100),
                "平均報酬%": "{:.2f}%".format(sum(rets) / len(rets)),
                "最佳報酬%": "{:.2f}%".format(max(rets)),
                "最差報酬%": "{:.2f}%".format(min(rets)),
            })
    return pd.DataFrame(rows)





def _pick_best_timing_idx(df):
    """進場時機『★最佳』判定（勝率優先版，表格與結論框共用，確保一致）。
    規則：
    1. 只看樣本數≥10的合格行（統計可靠門檻；<10不夠格當最佳）。
    2. 合格行中優先選勝率最高。
    3. 勝率相近（差距≤5pp視為平手）時，改用平均報酬決勝（避免選到高勝率低報酬的雞肋行）。
    4. 無任何行樣本≥10 → 回傳 None（顯示無明確最佳，不硬推）。"""
    try:
        valid = df[pd.to_numeric(df["樣本數"], errors='coerce').fillna(0) >= 10].copy()
        if valid.empty:
            return None
        valid["_wr"] = pd.to_numeric(valid["勝率"].astype(str).str.replace("%", ""), errors='coerce')
        valid["_avg"] = pd.to_numeric(valid["平均報酬%"].astype(str).str.replace("%", ""), errors='coerce')
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
