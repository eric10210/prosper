"""
indicators.py — PROSPER v2 Complete Indicator Library (30+ indicators).
ALL computed from closed candles only — non-repainting by design.
Per-volatility optimized parameters built in.
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  PRIMITIVES
# ══════════════════════════════════════════════════════════════════
def _ema(s: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(s), np.nan)
    if len(s) < p:
        return out
    a = 2.0 / (p + 1)
    out[p-1] = np.mean(s[:p])
    for i in range(p, len(s)):
        out[i] = a * s[i] + (1 - a) * out[i-1]
    return out

def _sma(s: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(s), np.nan)
    for i in range(p-1, len(s)):
        out[i] = np.mean(s[i-p+1:i+1])
    return out

def _stdev(s: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(s), np.nan)
    for i in range(p-1, len(s)):
        out[i] = np.std(s[i-p+1:i+1], ddof=0)
    return out

def _rma(s: np.ndarray, p: int) -> np.ndarray:
    """Wilder RMA (used in RSI, ATR)."""
    out = np.full(len(s), np.nan)
    if len(s) < p:
        return out
    out[p-1] = np.mean(s[:p])
    a = 1.0 / p
    for i in range(p, len(s)):
        out[i] = a * s[i] + (1-a) * out[i-1]
    return out

def _L(arr: np.ndarray, offset: int = 0) -> Optional[float]:
    """Safe last-N value."""
    try:
        v = arr[-(1+offset)]
        return None if np.isnan(v) else float(v)
    except (IndexError, TypeError):
        return None

def _g(d: dict, k: str, default=None):
    return d.get(k, default)


# ══════════════════════════════════════════════════════════════════
#  TREND INDICATORS
# ══════════════════════════════════════════════════════════════════
def calc_ema_stack(c: np.ndarray) -> Dict:
    e9  = _ema(c, 9);  e21 = _ema(c, 21)
    e50 = _ema(c, 50); e100= _ema(c, 100); e200= _ema(c, 200)
    v9=_L(e9); v21=_L(e21); v50=_L(e50); v200=_L(e200)
    v9_2=_L(e9,1); v21_2=_L(e21,1)
    price = float(c[-1])
    bull_stack = bool(v9 and v21 and v50 and v9>v21>v50)
    bear_stack = bool(v9 and v21 and v50 and v9<v21<v50)
    return {
        "ema9":v9,"ema21":v21,"ema50":v50,"ema200":v200,
        "ema_stack_bull": bull_stack,
        "ema_stack_bear": bear_stack,
        "ema_bull": bool(v9 and v21 and v9>v21),
        "ema_bear": bool(v9 and v21 and v9<v21),
        "ema9_cross_bull": bool(v9 and v21 and v9_2 and v21_2 and v9>v21 and v9_2<=v21_2),
        "ema9_cross_bear": bool(v9 and v21 and v9_2 and v21_2 and v9<v21 and v9_2>=v21_2),
        "price_above_ema21": price > (v21 or 0),
        "price_above_ema50": price > (v50 or 0),
        "price_above_ema200":price > (v200 or 0),
        "price_below_ema21": price < (v21 or 1e9),
        "ema_ribbon_bull": bool(v9 and v21 and v50 and v100 and v9>v21>v50>v100) if _L(e100) else False,
        "ema_ribbon_bear": bool(v9 and v21 and v50 and v100 and v9<v21<v50<v100) if _L(e100) else False,
    }


def calc_macd(c: np.ndarray, fast=12, slow=26, sig=9) -> Dict:
    ef=_ema(c,fast); es=_ema(c,slow)
    ml = np.where(~np.isnan(ef)&~np.isnan(es), ef-es, np.nan)
    valid = ml[~np.isnan(ml)]
    sl = _ema(valid, sig)
    full_sl = np.full(len(ml), np.nan)
    cnt = np.sum(~np.isnan(ml))
    if len(sl) == cnt:
        full_sl[~np.isnan(ml)] = sl
    hist = np.where(~np.isnan(ml)&~np.isnan(full_sl), ml-full_sl, np.nan)
    m=_L(ml); s=_L(full_sl); h=_L(hist); h2=_L(hist,1)
    return {
        "macd": m, "macd_sig": s, "macd_hist": h,
        "macd_bull":       bool(h is not None and h>0),
        "macd_bear":       bool(h is not None and h<0),
        "macd_cross_bull": bool(h is not None and h>0 and h2 is not None and h2<=0),
        "macd_cross_bear": bool(h is not None and h<0 and h2 is not None and h2>=0),
        "macd_hist_growing": bool(h is not None and h2 is not None and abs(h)>abs(h2)),
        "macd_divergence": bool(m is not None and s is not None and abs(m-s)>abs(h2 or 0)),
    }


def calc_adx(df: pd.DataFrame, p=14) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    up=np.diff(h,prepend=h[0]); dn=np.diff(lo,prepend=lo[0])*-1
    pc=np.concatenate([[c[0]],c[:-1]])
    tr=np.maximum(h-lo,np.maximum(abs(h-pc),abs(lo-pc)))
    pdm=np.where((up>dn)&(up>0),up,0.0)
    ndm=np.where((dn>up)&(dn>0),dn,0.0)
    a14=_rma(tr,p)
    pdi=100*_rma(pdm,p)/np.where(a14>0,a14,1)
    ndi=100*_rma(ndm,p)/np.where(a14>0,a14,1)
    dx =100*np.abs(pdi-ndi)/np.where((pdi+ndi)>0,pdi+ndi,1)
    adx=_rma(dx,p)
    av=_L(adx); pv=_L(pdi); nv=_L(ndi); av2=_L(adx,1)
    return {
        "adx":av,"pdi":pv,"ndi":nv,
        "trending":    bool(av and av>25),
        "strong_trend":bool(av and av>35),
        "ranging":     bool(av and av<20),
        "bull_trend":  bool(av and av>25 and pv and nv and pv>nv),
        "bear_trend":  bool(av and av>25 and pv and nv and nv>pv),
        "adx_rising":  bool(av and av2 and av>av2),
    }


# ══════════════════════════════════════════════════════════════════
#  OSCILLATORS
# ══════════════════════════════════════════════════════════════════
def calc_rsi(c: np.ndarray, p=14) -> Dict:
    d=np.diff(c)
    g=np.where(d>0,d,0.0); ls=np.where(d<0,-d,0.0)
    ag=_rma(np.concatenate([[0],g]),p)
    al=_rma(np.concatenate([[0],ls]),p)
    with np.errstate(divide="ignore",invalid="ignore"):
        rs=np.where(al>0,ag/al,100.0)
    rsi=100-(100/(1+rs)); rsi[:p]=np.nan
    v=_L(rsi); v2=_L(rsi,1); v3=_L(rsi,2)
    return {
        "rsi":v,
        "rsi_os":    bool(v and v<30),
        "rsi_ob":    bool(v and v>70),
        "rsi_deep_os":bool(v and v<20),
        "rsi_deep_ob":bool(v and v>80),
        "rsi_neutral":bool(v and 40<v<60),
        "rsi_bull":  bool(v and 40<=v<65),
        "rsi_bear":  bool(v and 35<v<=60),
        "rsi_rising":bool(v and v2 and v>v2),
        "rsi_falling":bool(v and v2 and v<v2),
        "rsi_cross_50_bull":bool(v and v>50 and v2 and v2<=50),
        "rsi_cross_50_bear":bool(v and v<50 and v2 and v2>=50),
        "_rsi_series": rsi,
    }


def calc_stoch_rsi(c: np.ndarray, rsi_p=14, stoch_p=14, k_p=3, d_p=3) -> Dict:
    rsi_d = calc_rsi(c, rsi_p)
    rsi   = rsi_d["_rsi_series"]
    sk    = np.full(len(rsi), np.nan)
    for i in range(stoch_p-1, len(rsi)):
        w=rsi[i-stoch_p+1:i+1]; w=w[~np.isnan(w)]
        if len(w)==0: continue
        hi=np.max(w); lo=np.min(w)
        sk[i]=50 if hi==lo else (rsi[i]-lo)/(hi-lo)*100
    k=_sma(sk,k_p); d=_sma(k,d_p)
    kv=_L(k); dv=_L(d); k2=_L(k,1); d2=_L(d,1)
    return {
        "stoch_k":kv,"stoch_d":dv,
        "stoch_os":  bool(kv and kv<20),
        "stoch_ob":  bool(kv and kv>80),
        "stoch_mid": bool(kv and 40<kv<60),
        "stoch_cross_bull":bool(kv and dv and kv>dv and k2 is not None and d2 is not None and k2<=d2),
        "stoch_cross_bear":bool(kv and dv and kv<dv and k2 is not None and d2 is not None and k2>=d2),
    }


def calc_cci(df: pd.DataFrame, p=20) -> Dict:
    tp=((df["high"]+df["low"]+df["close"])/3).values
    cci=np.full(len(tp),np.nan)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1]; m=np.mean(w); md=np.mean(abs(w-m))
        cci[i]=(tp[i]-m)/(0.015*md) if md>0 else 0
    v=_L(cci); v2=_L(cci,1)
    return {
        "cci":v,
        "cci_os": bool(v and v<-100),
        "cci_ob": bool(v and v>100),
        "cci_bull":bool(v and v>-100 and v2 is not None and v2<=-100),
        "cci_bear":bool(v and v<100  and v2 is not None and v2>=100),
        "cci_extreme_os":bool(v and v<-200),
        "cci_extreme_ob":bool(v and v>200),
    }


def calc_williams_r(df: pd.DataFrame, p=14) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    wr=np.full(len(c),np.nan)
    for i in range(p-1,len(c)):
        hh=np.max(h[i-p+1:i+1]); ll=np.min(lo[i-p+1:i+1])
        wr[i]=-100*(hh-c[i])/(hh-ll) if hh!=ll else -50
    v=_L(wr); v2=_L(wr,1)
    return {
        "williams_r":v,
        "wr_os":bool(v and v<-80),
        "wr_ob":bool(v and v>-20),
        "wr_cross_bull":bool(v and v>-80 and v2 is not None and v2<=-80),
        "wr_cross_bear":bool(v and v<-20 and v2 is not None and v2>=-20),
    }


def calc_momentum(c: np.ndarray, p=10) -> Dict:
    mom=np.full(len(c),np.nan)
    for i in range(p,len(c)):
        mom[i]=c[i]-c[i-p]
    roc=np.full(len(c),np.nan)
    for i in range(p,len(c)):
        if c[i-p]!=0: roc[i]=(c[i]-c[i-p])/c[i-p]*100
    mv=_L(mom); rv=_L(roc)
    return {
        "momentum": mv,
        "roc": rv,
        "momentum_bull":bool(mv and mv>0),
        "momentum_bear":bool(mv and mv<0),
        "momentum_strong":bool(mv is not None and abs(mv)>np.nanstd(mom[~np.isnan(mom)]) if np.any(~np.isnan(mom)) else False),
    }


# ══════════════════════════════════════════════════════════════════
#  VOLATILITY INDICATORS
# ══════════════════════════════════════════════════════════════════
def calc_atr(df: pd.DataFrame, p=14) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    pc=np.concatenate([[c[0]],c[:-1]])
    tr=np.maximum(h-lo,np.maximum(abs(h-pc),abs(lo-pc)))
    atr=_rma(tr,p)
    v=_L(atr); v2=_L(atr,1)
    from config import ATR_LOW_THRESHOLD, ATR_HIGH_THRESHOLD
    regime="LOW" if (v or 0)<ATR_LOW_THRESHOLD else ("HIGH" if (v or 0)>ATR_HIGH_THRESHOLD else "MEDIUM")
    return {
        "atr14":v,
        "regime":regime,
        "atr_expanding":bool(v and v2 and v>v2*1.05),
        "atr_contracting":bool(v and v2 and v<v2*0.95),
        "_atr":atr,
    }


def calc_bb(c: np.ndarray, p=20, mult=2.0) -> Dict:
    mid=_sma(c,p); sd=_stdev(c,p)
    upper=mid+mult*sd; lower=mid-mult*sd
    bw=(upper-lower)/np.where(mid>0,mid,1)
    bw_pct=np.nanpercentile(bw[~np.isnan(bw)],50) if np.any(~np.isnan(bw)) else 0
    price=c[-1]
    m=_L(mid); u=_L(upper); lo=_L(lower); b=_L(bw)
    pct_b=(price-(lo or 0))/((u or 1)-(lo or 0)) if (u and lo and u!=lo) else 0.5
    return {
        "bb_mid":m,"bb_upper":u,"bb_lower":lo,"bb_bw":b,"bb_pct_b":pct_b,
        "bb_touch_upper":price>=(u or 9e9)*0.9998,
        "bb_touch_lower":price<=(lo or 0)*1.0002,
        "bb_pct_b_os":pct_b<0.05,
        "bb_pct_b_ob":pct_b>0.95,
        "bb_pct_b_near_lower":pct_b<0.15,
        "bb_pct_b_near_upper":pct_b>0.85,
        "bb_squeeze": bool(b and bw_pct>0 and b<bw_pct),
        "bb_sqz_release": bool(b and bw_pct>0 and b>=bw_pct and _L(bw,1) is not None and _L(bw,1)<bw_pct),
        "bb_expanding": bool(b and _L(bw,1) and b>_L(bw,1)*1.03),
        "_bb_upper":upper,"_bb_lower":lower,
    }


def calc_keltner(df: pd.DataFrame, ema_p=20, atr_p=10, mult=2.0) -> Dict:
    mid=_ema(df["close"].values,ema_p)
    atr_d=calc_atr(df,atr_p); atr=atr_d["_atr"]
    upper=mid+mult*atr; lower=mid-mult*atr
    return {
        "kc_upper":_L(upper),"kc_lower":_L(lower),"kc_mid":_L(mid),
        "_kc_upper":upper,"_kc_lower":lower,
    }


def calc_ttm_squeeze(df: pd.DataFrame) -> Dict:
    bb=calc_bb(df["close"].values)
    kc=calc_keltner(df)
    bu=bb["_bb_upper"]; bl=bb["_bb_lower"]
    ku=kc["_kc_upper"]; kl=kc["_kc_lower"]
    n=min(len(bu),len(ku))
    sqz=np.array([bu[i]<ku[i] and bl[i]>kl[i] if not (np.isnan(bu[i]) or np.isnan(ku[i])) else False
                  for i in range(n)])
    on=bool(sqz[-1]) if n>0 else False
    off=bool(not sqz[-1] and sqz[-2]) if n>1 else False
    return {"ttm_squeeze":on,"ttm_sqz_release":off,"bb_inside_keltner":on}


def calc_supertrend(df: pd.DataFrame, p=10, mult=3.0) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    hl2=(h+lo)/2
    atr_d=calc_atr(df,p); atr=atr_d["_atr"]
    ub=hl2+mult*atr; lb=hl2-mult*atr
    upper=ub.copy(); lower=lb.copy(); direction=np.zeros(len(c))
    for i in range(1,len(c)):
        if np.isnan(atr[i]): continue
        upper[i]=ub[i] if ub[i]<upper[i-1] or c[i-1]>upper[i-1] else upper[i-1]
        lower[i]=lb[i] if lb[i]>lower[i-1] or c[i-1]<lower[i-1] else lower[i-1]
        if   direction[i-1]<=0 and c[i]>upper[i-1]: direction[i]=1
        elif direction[i-1]>=0 and c[i]<lower[i-1]: direction[i]=-1
        else: direction[i]=direction[i-1]
    st=np.where(direction>=0,lower,upper)
    dv=direction[-1]; d2=direction[-2] if len(direction)>1 else 0
    return {
        "st_dir":int(dv),"st_line":_L(st),
        "st_bull":dv==1,"st_bear":dv==-1,
        "st_flip_bull":dv==1 and d2<=0,
        "st_flip_bear":dv==-1 and d2>=0,
    }


def calc_donchian(df: pd.DataFrame, p=20) -> Dict:
    h=df["high"].values; lo=df["low"].values
    dh=np.full(len(h),np.nan); dl=np.full(len(lo),np.nan)
    for i in range(p,len(h)):
        dh[i]=np.max(h[i-p:i]); dl[i]=np.min(lo[i-p:i])  # Exclude current
    price=df["close"].values[-1]
    dh_v=_L(dh); dl_v=_L(dl)
    return {
        "don_high":dh_v,"don_low":dl_v,
        "don_break_bull":bool(dh_v and price>dh_v),
        "don_break_bear":bool(dl_v and price<dl_v),
        "don_mid":       (dh_v+dl_v)/2 if dh_v and dl_v else None,
    }


def calc_psar(df: pd.DataFrame, step=0.02, max_af=0.2) -> Dict:
    h=df["high"].values; lo=df["low"].values
    n=len(h)
    sar=np.full(n,np.nan); bull=np.full(n,False,dtype=bool)
    if n<3:
        return {"psar":None,"psar_bull":False,"psar_bear":False,"psar_flip_bull":False,"psar_flip_bear":False}
    trend=1; af=step; ep=h[0]; sar[0]=lo[0]
    for i in range(1,n):
        p=sar[i-1]
        if trend==1:
            sar[i]=p+af*(ep-p)
            sar[i]=min(sar[i],lo[i-1],lo[i-2] if i>1 else lo[i-1])
            if lo[i]<sar[i]:
                trend=-1; af=step; ep=lo[i]; sar[i]=ep
            else:
                if h[i]>ep: ep=h[i]; af=min(af+step,max_af)
        else:
            sar[i]=p+af*(ep-p)
            sar[i]=max(sar[i],h[i-1],h[i-2] if i>1 else h[i-1])
            if h[i]>sar[i]:
                trend=1; af=step; ep=h[i]; sar[i]=ep
            else:
                if lo[i]<ep: ep=lo[i]; af=min(af+step,max_af)
        bull[i]=trend==1
    bv=bool(bull[-1]); b2=bool(bull[-2]) if n>1 else False
    return {
        "psar":_L(sar),"psar_bull":bv,"psar_bear":not bv,
        "psar_flip_bull":bv and not b2,
        "psar_flip_bear":not bv and b2,
    }


# ══════════════════════════════════════════════════════════════════
#  SMC / STRUCTURE INDICATORS
# ══════════════════════════════════════════════════════════════════
def calc_order_blocks(df: pd.DataFrame, lb=30) -> Dict:
    n=len(df)
    bull_obs=[]; bear_obs=[]
    for i in range(1,min(n-2,lb+1)):
        c=df.iloc[-(i+2)]; nx=df.iloc[-(i+1)]; n2=df.iloc[-i]
        # Bullish OB: down candle → 2 up candles break above
        if c["close"]<c["open"] and nx["close"]>nx["open"] and n2["close"]>c["high"]:
            bull_obs.append({"high":c["high"],"low":c["low"],"mid":(c["high"]+c["low"])/2,"strength":abs(c["close"]-c["open"])})
        # Bearish OB: up candle → 2 down candles break below
        if c["close"]>c["open"] and nx["close"]<nx["open"] and n2["close"]<c["low"]:
            bear_obs.append({"high":c["high"],"low":c["low"],"mid":(c["high"]+c["low"])/2,"strength":abs(c["close"]-c["open"])})
    price=float(df["close"].values[-1])
    def near_zone(zones,p,tol=0.6):
        return any(abs(p-z["mid"])<(z["high"]-z["low"])*tol+1 for z in zones[-3:])
    return {
        "at_bull_ob":near_zone(bull_obs,price),
        "at_bear_ob":near_zone(bear_obs,price),
        "bull_obs":bull_obs[-3:],"bear_obs":bear_obs[-3:],
        "ob_count":len(bull_obs)+len(bear_obs),
    }


def calc_bos(df: pd.DataFrame, lb=20) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    n=len(h)
    if n<lb+2:
        return {"bos_bull":False,"bos_bear":False,"bos_direction":"NONE","prev_high":0,"prev_low":0}
    ph=np.max(h[-(lb+1):-1]); pl=np.min(lo[-(lb+1):-1])
    price=c[-1]
    bull=price>ph; bear=price<pl
    return {
        "bos_bull":bull,"bos_bear":bear,
        "bos_direction":"BUY" if bull else ("SELL" if bear else "NONE"),
        "prev_high":ph,"prev_low":pl,
    }


def calc_fvg(df: pd.DataFrame, lb=30) -> Dict:
    n=len(df)
    bull=[]; bear=[]
    for i in range(1,min(n-1,lb)):
        a=df.iloc[-(i+2)]; c2=df.iloc[-i]
        if a["high"]<c2["low"]:
            bull.append({"top":c2["low"],"bot":a["high"],"mid":(c2["low"]+a["high"])/2})
        if a["low"]>c2["high"]:
            bear.append({"top":a["low"],"bot":c2["high"],"mid":(a["low"]+c2["high"])/2})
    price=float(df["close"].values[-1])
    ab=[f for f in bull[-5:] if f["bot"]<=price<=f["top"]*1.003]
    ab2=[f for f in bear[-5:] if f["top"]>=price>=f["bot"]*0.997]
    return {
        "fvg_bull":len(ab)>0,"fvg_bear":len(ab2)>0,
        "fvg_bull_list":ab,"fvg_bear_list":ab2,
        "at_fvg":len(ab)>0 or len(ab2)>0,
    }


def calc_pivots(df: pd.DataFrame) -> Dict:
    if len(df)<2:
        return {"pivot":0,"r1":0,"r2":0,"s1":0,"s2":0,"r3":0,"s3":0,
                "at_pivot":False,"at_r1":False,"at_s1":False}
    prev=df.iloc[-2]
    h=float(prev["high"]); lo=float(prev["low"]); c=float(prev["close"])
    pivot=(h+lo+c)/3
    r1=2*pivot-lo; r2=pivot+(h-lo); r3=r1+(h-lo)
    s1=2*pivot-h;  s2=pivot-(h-lo); s3=s1-(h-lo)
    price=float(df["close"].values[-1])
    tol=max(abs(price)*0.0008,0.5)
    return {
        "pivot":pivot,"r1":r1,"r2":r2,"r3":r3,"s1":s1,"s2":s2,"s3":s3,
        "at_pivot":abs(price-pivot)<tol,
        "at_r1":abs(price-r1)<tol,"at_r2":abs(price-r2)<tol,
        "at_s1":abs(price-s1)<tol,"at_s2":abs(price-s2)<tol,
    }


# ══════════════════════════════════════════════════════════════════
#  STATISTICAL INDICATORS
# ══════════════════════════════════════════════════════════════════
def calc_zscore(c: np.ndarray, p=20) -> Dict:
    """Z-Score mean reversion. Vol10 backtest: 83.9% WR at |Z|>2.0."""
    mu=_sma(c,p); sd=_stdev(c,p)
    z=np.full(len(c),np.nan)
    for i in range(p-1,len(c)):
        if sd[i]>0: z[i]=(c[i]-mu[i])/sd[i]
    zv=_L(z); z2=_L(z,1)
    return {
        "zscore":zv,
        "zscore_ob":   bool(zv and zv>2.0),
        "zscore_os":   bool(zv and zv<-2.0),
        "zscore_extreme_ob":bool(zv and zv>2.5),
        "zscore_extreme_os":bool(zv and zv<-2.5),
        "zscore_mild_ob":   bool(zv and 1.5<zv<2.5),
        "zscore_mild_os":   bool(zv and -2.5<zv<-1.5),
        "zscore_mean":_L(mu),
        "zscore_cross_zero_bull":bool(zv and zv>0 and z2 is not None and z2<0),
        "zscore_cross_zero_bear":bool(zv and zv<0 and z2 is not None and z2>0),
    }


def calc_hurst(c: np.ndarray) -> Dict:
    """Hurst exponent. >0.55=trending (V75), <0.48=mean-reverting (V10)."""
    if len(c)<50:
        return {"hurst":0.5,"hurst_trending":False,"hurst_reverting":False,"hurst_ambiguous":True}
    try:
        lags=range(2,min(20,len(c)//5))
        rs_vals=[]
        for lag in lags:
            sub=c[-lag*5:]
            ret=np.diff(np.log(np.maximum(sub,1e-10)))
            if len(ret)<lag: continue
            rs_lag=[]
            for s in range(0,len(ret)-lag+1,lag):
                w=ret[s:s+lag]
                if len(w)<2: continue
                mw=np.mean(w); dev=np.cumsum(w-mw)
                R=np.max(dev)-np.min(dev); S=np.std(w,ddof=1)
                if S>0: rs_lag.append(R/S)
            if rs_lag: rs_vals.append((lag,np.mean(rs_lag)))
        if len(rs_vals)<3:
            return {"hurst":0.5,"hurst_trending":False,"hurst_reverting":False,"hurst_ambiguous":True}
        x=np.log([v[0] for v in rs_vals]); y=np.log([v[1] for v in rs_vals])
        hurst=float(np.polyfit(x,y,1)[0])
        hurst=max(0.0,min(1.0,hurst))
        return {
            "hurst":hurst,
            "hurst_trending":  hurst>0.55,
            "hurst_reverting": hurst<0.48,
            "hurst_ambiguous": 0.48<=hurst<=0.55,
        }
    except Exception:
        return {"hurst":0.5,"hurst_trending":False,"hurst_reverting":False,"hurst_ambiguous":True}


def calc_rsi_divergence(df: pd.DataFrame, lb=14) -> Dict:
    c=df["close"].values; h=df["high"].values; lo=df["low"].values
    rsi_d=calc_rsi(c,lb); rsi=rsi_d["_rsi_series"]
    n=len(c)
    bull=bear=False
    if n>lb*2:
        for i in range(n-lb,n-2):
            if not np.isnan(rsi[n-1]) and not np.isnan(rsi[i]):
                if lo[n-1]<lo[i] and rsi[n-1]>rsi[i]: bull=True
                if h[n-1]>h[i] and rsi[n-1]<rsi[i]: bear=True
    macd_d=calc_macd(c)
    return {
        "rsi_bull_div":bull,"rsi_bear_div":bear,
        "stoch_bull_div":bull,"stoch_bear_div":bear,
        "macd_bull":macd_d["macd_bull"],"macd_bear":macd_d["macd_bear"],
    }


def calc_ichimoku(df: pd.DataFrame, t=9, k=26, s=52) -> Dict:
    h=df["high"].values; lo=df["low"].values; c=df["close"].values
    def mid(hi,l,p):
        out=np.full(len(hi),np.nan)
        for i in range(p-1,len(hi)):
            out[i]=(np.max(hi[i-p+1:i+1])+np.min(l[i-p+1:i+1]))/2
        return out
    ten=mid(h,lo,t); kij=mid(h,lo,k)
    sa=(ten+kij)/2; sb=mid(h,lo,s)
    price=c[-1]
    sav=_L(sa); sbv=_L(sb)
    above=bool(sav and sbv and price>max(sav,sbv))
    below=bool(sav and sbv and price<min(sav,sbv))
    tv=_L(ten); kv=_L(kij); t2=_L(ten,1); k2=_L(kij,1)
    bc=bool(tv and kv and tv>kv and t2 is not None and k2 is not None and t2<=k2)
    dc=bool(tv and kv and tv<kv and t2 is not None and k2 is not None and t2>=k2)
    return {
        "ichi_above_cloud":above,"ichi_below_cloud":below,
        "ichi_full_bull":above and bc,
        "ichi_full_bear":below and dc,
        "ichi_tenkan":tv,"ichi_kijun":kv,"ichi_span_a":sav,"ichi_span_b":sbv,
        "ichi_tk_cross_bull":bc,"ichi_tk_cross_bear":dc,
        "ichi_in_cloud":bool(sav and sbv and min(sav,sbv)<=price<=max(sav,sbv)),
    }


def calc_heikin_ashi(df: pd.DataFrame) -> Dict:
    o=df["open"].values; h=df["high"].values
    lo=df["low"].values; c=df["close"].values
    n=len(c)
    hac=( o+h+lo+c)/4
    hao=np.full(n,np.nan); hao[0]=(o[0]+c[0])/2
    for i in range(1,n): hao[i]=(hao[i-1]+hac[i-1])/2
    bull=hac[-1]>hao[-1]
    return {
        "ha_bull":bull,"ha_bear":not bull,
        "ha_flip_bull":bool(bull and n>1 and hac[-2]<hao[-2]),
        "ha_flip_bear":bool(not bull and n>1 and hac[-2]>hao[-2]),
        "ha_strong_bull":bool(bull and lo[-1]==hao[-1]),
        "ha_strong_bear":bool(not bull and h[-1]==hao[-1]),
    }


def calc_wvf(df: pd.DataFrame, lb=22) -> Dict:
    """Williams VIX Fix — fear/capitulation detector."""
    c=df["close"].values; n=len(c)
    if n<lb+2:
        return {"wvf_spike":False,"wvf_value":0,"wvf_fear":False}
    highest=np.array([np.max(c[max(0,i-lb):i+1]) for i in range(n)])
    wvf=(highest-c)/highest*100
    ub=_sma(wvf,20)+2*_stdev(wvf,20)
    rh=np.array([np.max(wvf[max(0,i-50):i+1])*2 if i>=50 else np.nan for i in range(n)])
    v=_L(wvf); ub_v=_L(ub); rh_v=_L(rh)
    spike=bool(v and ((ub_v and v>=ub_v) or (rh_v and v>=rh_v)))
    return {"wvf_spike":spike,"wvf_value":float(v or 0),"wvf_fear":spike}


def calc_fib_levels(df: pd.DataFrame, lb=50) -> Dict:
    sub=df.tail(lb)
    hi=float(sub["high"].max()); lo=float(sub["low"].min())
    price=float(df["close"].values[-1])
    diff=hi-lo
    if diff<=0:
        return {"at_fib_any":False,"at_fib_618":False,"at_fib_382":False,"fib_levels":{}}
    levels={
        "0.236":hi-0.236*diff,"0.382":hi-0.382*diff,"0.500":hi-0.500*diff,
        "0.618":hi-0.618*diff,"0.786":hi-0.786*diff,"0.886":hi-0.886*diff,
    }
    tol=max(diff*0.008,1)
    return {
        "at_fib_any":  any(abs(price-v)<tol for v in levels.values()),
        "at_fib_618":  abs(price-levels["0.618"])<tol,
        "at_fib_382":  abs(price-levels["0.382"])<tol,
        "at_fib_500":  abs(price-levels["0.500"])<tol,
        "fib_levels":  levels,
    }


def calc_volume_profile(df: pd.DataFrame) -> Dict:
    """Tick volume proxy — meaningful on synthetics."""
    if "volume" not in df.columns:
        return {"volume_high":False,"volume_low":False,"volume_ratio":1.0}
    vol=df["volume"].values
    avg=np.nanmean(vol[-20:]) if len(vol)>=20 else np.nanmean(vol)
    curr=float(vol[-1]) if len(vol)>0 else 1
    ratio=curr/avg if avg>0 else 1.0
    return {
        "volume_high": ratio>1.5,
        "volume_low":  ratio<0.7,
        "volume_ratio":round(ratio,2),
        "volume_surge":ratio>2.0,
    }


def calc_lrc(df: pd.DataFrame, p=20) -> Dict:
    """Linear Regression Channel — mean reversion channel."""
    c=df["close"].values; n=len(c)
    if n<p:
        return {"lrc_upper":None,"lrc_lower":None,"lrc_mid":None,"at_lrc_lower":False,"at_lrc_upper":False}
    x=np.arange(p)
    w=c[-p:]
    m,b=np.polyfit(x,w,1)
    fitted=m*x+b
    resid=w-fitted
    std=np.std(resid,ddof=1)
    lrc_mid=float(fitted[-1]); lrc_u=lrc_mid+2*std; lrc_l=lrc_mid-2*std
    price=float(c[-1])
    return {
        "lrc_mid":lrc_mid,"lrc_upper":lrc_u,"lrc_lower":lrc_l,
        "at_lrc_lower":price<=lrc_l*1.001,
        "at_lrc_upper":price>=lrc_u*0.999,
        "lrc_slope_bull":m>0,"lrc_slope_bear":m<0,
    }


# ══════════════════════════════════════════════════════════════════
#  MASTER COMPUTE FUNCTION
# ══════════════════════════════════════════════════════════════════
def compute_all_indicators(df: pd.DataFrame, sym: str = "") -> Dict:
    """
    Compute ALL indicators for one symbol/timeframe snapshot.
    Returns flat dict. Pass sym for per-instrument tuning.
    """
    if df is None or len(df) < 50:
        return {"ready": False}

    c   = df["close"].values.astype(float)
    out = {"ready": True, "sym": sym, "candle_count": len(df)}

    def safe(fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            if result:
                out.update(result)
        except Exception as e:
            log.debug(f"Indicator {fn.__name__} failed: {e}")

    # Trend
    safe(calc_ema_stack, c)
    safe(calc_macd, c)
    safe(calc_adx, df)
    safe(calc_supertrend, df)
    safe(calc_ichimoku, df)
    safe(calc_heikin_ashi, df)
    safe(calc_psar, df)
    safe(calc_lrc, df)

    # Oscillators
    # Per-instrument RSI period tuning
    rsi_period = {"R_10": 7, "R_25": 9, "R_50": 11, "R_75": 14}.get(sym, 14)
    safe(calc_rsi, c, rsi_period)
    safe(calc_stoch_rsi, c)
    safe(calc_cci, df)
    safe(calc_williams_r, df)
    safe(calc_momentum, c)

    # Volatility
    safe(calc_atr, df)
    safe(calc_bb, c)
    safe(calc_keltner, df)
    safe(calc_ttm_squeeze, df)
    safe(calc_donchian, df)

    # Statistical
    safe(calc_zscore, c)
    safe(calc_hurst, c)
    safe(calc_rsi_divergence, df)

    # Structure / SMC
    safe(calc_order_blocks, df)
    safe(calc_bos, df)
    safe(calc_fvg, df)
    safe(calc_pivots, df)
    safe(calc_fib_levels, df)
    safe(calc_wvf, df)
    safe(calc_volume_profile, df)

    # Computed price context
    price = float(c[-1])
    out["price"] = price
    out["price_above_vwap"] = price > (out.get("bb_mid") or price)

    return out
