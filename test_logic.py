# -*- coding: utf-8 -*-
"""不依賴 streamlit/yfinance 的純邏輯測試（sandbox 無網路，鐵律13）"""
import sys, types, datetime as dt
# stub streamlit：讓 app.py 可 import，不啟動 UI
st = types.ModuleType("streamlit")
def _cache(**k):
    def deco(f): return f
    return deco
st.cache_data = _cache
for n in ["set_page_config","title","caption","header","subheader","error","warning",
          "info","success","markdown","dataframe","metric","progress","columns",
          "multiselect","number_input","checkbox","button","tabs","data_editor",
          "download_button","sidebar"]:
    setattr(st, n, lambda *a, **k: None)
sys.modules["streamlit"] = st
sys.modules["yfinance"] = types.ModuleType("yfinance")
import app

P=F=0
def chk(name, cond, detail=""):
    global P,F
    if cond: P+=1; print("  [PASS] {:<48} {}".format(name, detail))
    else:    F+=1; print("  [FAIL] {:<48} {}".format(name, detail))

print("\n=== 憲法 Z1-2：主動ETF 官方識別規則（6碼第6碼 A/D）===")
chk("00980A → 主動ETF-股票", app.classify_etf("00980A.TW")=="主動ETF-股票", app.classify_etf("00980A.TW"))
chk("00981D → 主動ETF-債券", app.classify_etf("00981D.TW")=="主動ETF-債券", app.classify_etf("00981D.TW"))
chk("0050 → 被動ETF",        app.classify_etf("0050.TW")=="被動ETF", app.classify_etf("0050.TW"))
chk("00878 → 被動ETF(5碼)",  app.classify_etf("00878.TW")=="被動ETF", app.classify_etf("00878.TW"))
chk("Tier1 排除主動ETF",     app.is_tier1("00980A.TW")==False and app.is_tier1("0050.TW")==True)

print("\n=== 鐵律16：曆日跨度記錄 + sanity 上限 ===")
# 造一組「中間缺一週」的序列，模擬非營業日/停止公告
base = dt.date(2026,1,5); px={}
d=base
for i in range(30):
    while d.weekday()>=5: d+=dt.timedelta(days=1)
    px[d.isoformat()]=100.0
    d+=dt.timedelta(days=1)
# 再接一段隔了60天後的資料（模擬停止公告後恢復）
d2 = d+dt.timedelta(days=60)
for i in range(5):
    while d2.weekday()>=5: d2+=dt.timedelta(days=1)
    px[d2.isoformat()]=50.0   # 腰斬 → 若不作廢會產生 -50% 假訊號
    d2+=dt.timedelta(days=1)
rr = app.calc_all_rolling_returns(px, 10, 25)
spans=[r["span_days"] for r in rr]
chk("span_days 有被記錄", all("span_days" in r for r in rr), "共{}筆".format(len(rr)))
bad=[r for r in rr if not r["valid"]]
chk("跨度>25天者標記 valid=False", len(bad)>0, "作廢{}筆, 最大跨度={}天".format(len(bad), max(spans)))
big_drop=[r for r in rr if r["return"]<=-40]
big_drop_valid=[r for r in big_drop if r["valid"]]
chk("腰斬假訊號被跨度上限擋下", len(big_drop)>0 and len(big_drop_valid)==0,
    "-40%以下共{}筆, 其中通過跨度檢查的={}筆".format(len(big_drop), len(big_drop_valid)))

print("\n=== 鐵律16：回測只納入 valid 觸發 ===")
bt = app.run_full_backtest(px, -40.0, rr, 0, 0.0, 0.0, 25)
chk("跨度作廢的觸發不進回測", bt is None, "回測結果={}（應為None，因唯一觸發已作廢）".format("None" if bt is None else "有值"))

print("\n=== 鐵律12：entry_lag 預設0 = 母專案行為 ===")
px2={}; d=dt.date(2026,1,1)
vals=[100,99,98,97,96,95,94,93,92,91,80, 85, 90, 95, 96, 97, 98, 99, 100, 101]  # 第11筆(idx10)暴跌至80
for i,v in enumerate(vals):
    px2[(d+dt.timedelta(days=i)).isoformat()]=float(v)
rr2=app.calc_all_rolling_returns(px2,10,999)
trig=[r for r in rr2 if r["return"]<=-15]
chk("觸發被偵測", len(trig)>0, "觸發日={} return={}%".format(trig[0]["date"], trig[0]["return"]) if trig else "無")
bt0=app.run_full_backtest(px2,-15.0,rr2,0,0.0,0.0,999)
bt1=app.run_full_backtest(px2,-15.0,rr2,1,0.0,0.0,999)
e0=bt0["horizon_rets"][5][0]["entry_price"] if bt0 and bt0["horizon_rets"][5] else None
chk("lag=0 進場價=觸發日收盤(=母專案 curr_price)", e0==80.0, "entry_price={}".format(e0))
e1=bt1["horizon_rets"][5][0]["entry_price"] if bt1 and bt1["horizon_rets"][5] else None
chk("lag=1 進場價=次一筆(參數確實生效)", e1==85.0, "entry_price={}".format(e1))

print("\n=== 鐵律12：費用參數化、預設0不影響結果 ===")
r_free=bt0["horizon_rets"][5][0]["ret"]
bt_fee=app.run_full_backtest(px2,-15.0,rr2,0,2.0,0.0,999)
r_fee=bt_fee["horizon_rets"][5][0]["ret"]
chk("預設0時報酬不變(=母專案)", abs(r_free-((97-80)/80*100))<0.01, "ret={}% (預期{:.2f}%)".format(r_free,(97-80)/80*100))
chk("扣2%前收後報酬下降", r_fee < r_free, "0%:{}% → 2%:{}%".format(r_free, round(r_fee,2)))

print("\n=== 鐵律12技術依據：原始報酬序列有保存 → 費用可後處理 ===")
keys=set(bt0["horizon_rets"][5][0].keys())
chk("保存 entry_price/future_price/ret", {"ret","entry_price","future_price"}<=keys, sorted(keys))
# 證明：後處理扣2% 應等同 fee_buy_pct=2.0
post = (97 - 80*1.02)/(80*1.02)*100
chk("後處理扣費 ≡ 回測內扣費（無需重跑）", abs(post-r_fee)<0.01,
    "後處理={:.4f}% vs 回測內={:.4f}%".format(post, r_fee))

print("\n=== 母專案同源：_pick_best_timing_idx 樣本<10 回 None ===")
import pandas as pd
d_small=pd.DataFrame([{"持有天數":5,"樣本數":9,"勝率":"100.0%","平均報酬%":"20.0%"}])
chk("樣本9 → 回 None（不硬推）", app._pick_best_timing_idx(d_small) is None)
d_ok=pd.DataFrame([{"持有天數":5,"樣本數":30,"勝率":"70.0%","平均報酬%":"5.0%"},
                   {"持有天數":10,"樣本數":30,"勝率":"68.0%","平均報酬%":"12.0%"}])
chk("勝率差≤5pp → 改用報酬決勝", app._pick_best_timing_idx(d_ok)==1,
    "選中 idx={}（應為1: 勝率68%但報酬12%）".format(app._pick_best_timing_idx(d_ok)))

print("\n=== 系統檢核：CRITICAL_CHECKS 與 IMPACT_MAP 同步 ===")
expect={k for k,v in app.IMPACT_MAP.items() if v[0]=="error"}
chk("CRITICAL_CHECKS ≡ IMPACT_MAP error項", app.CRITICAL_CHECKS==expect, "{}項".format(len(expect)))
c=app.run_system_checks(True,0.0,0.0,0)
chk("adjust=True 時全數PASS", (~c["結果"].str.contains("FAIL")).all())
c2=app.run_system_checks(False,0.0,0.0,0)
f2=c2[(c2["結果"].str.contains("FAIL"))&(c2["檢核項目"].isin(app.CRITICAL_CHECKS))]
chk("關閉配息還原 → 關鍵檢核FAIL(鐵律14)", len(f2)==1, "FAIL項: {}".format(list(f2["檢核項目"])))

print("\n=== 追蹤日誌向後相容（母專案行為）===")
import tempfile,os
app.JOURNAL_PATH=os.path.join(tempfile.mkdtemp(),"j.csv")
pd.DataFrame([{"代碼":"0050.TW","狀態":"已結案","實際報酬%":"5.0"}]).to_csv(app.JOURNAL_PATH,index=False)
j=app.load_journal()
chk("舊CSV缺欄自動補齊", list(j.columns)==app.JOURNAL_COLS, "{}欄".format(len(j.columns)))

print("\n" + "="*62)
print("  PASS {} / FAIL {}".format(P,F))
print("="*62)
sys.exit(1 if F else 0)
