import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
import numpy as np

st.set_page_config(page_title="RシステムPRO", layout="wide")

# -------------------------------------------------------------
# キャッシュのTTLを30分 (1800秒) に設定
# -------------------------------------------------------------
@st.cache_data(ttl=1800)  
def load_data(source):
    try:
        url_map = {
            "today": "https://app.kumagai-stock.com/api/highlow/today",
            "yesterday": "https://app.kumagai-stock.com/api/highlow/yesterday",
            "target2day": "https://app.kumagai-stock.com/api/highlow/target2day",
            "target3day": "https://app.kumagai-stock.com/api/highlow/target3day",
            "target4day": "https://app.kumagai-stock.com/api/highlow/target4day",
            "target5day": "https://app.kumagai-stock.com/api/highlow/target5day"
        }
        url = url_map.get(source, url_map["today"])
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        
        # データの型を明示的に変換（high, lowなどが数値であることを保証）
        df = pd.DataFrame(res.json())
        if not df.empty:
            for col in ["high", "low"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df.dropna(subset=["high", "low"], inplace=True)
            
        return df
    except Exception as e:
        st.error(f"データの読み込み中にエラーが発生しました: {e}")
        return pd.DataFrame()

# -------------------------------------------------------------
# ✅ 本日〜3日前までをまとめて「上げ幅に対する下落率」ランキング表を作る
# -------------------------------------------------------------
@st.cache_data(ttl=1800)
def load_highlow_multi(sources):
    frames = []
    for s in sources:
        d = load_data(s)  # 既存の load_data を流用
        if not d.empty:
            d = d.copy()
            d["source"] = s
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames, ignore_index=True)

    # high_date / low_date を日付化（文字列でも動くように）
    for c in ["high_date", "low_date"]:
        if c in df_all.columns:
            df_all[c] = pd.to_datetime(df_all[c], errors="coerce")

    return df_all

@st.cache_data(ttl=1800)
def load_current_price_map():
    """
    現在値は RealData の current を使う（API側の設計と一致）
    /api/highlow/batch から code->current_price の辞書を作る
    """
    try:
        url = "https://app.kumagai-stock.com/api/highlow/batch"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        items = resp.json()  # list[{code, current_price, ...}]
        m = {}
        for it in items:
            c = str(it.get("code", "")).strip()
            cp = it.get("current_price", None)
            if c != "" and cp is not None:
                m[c] = float(cp)
        return m
    except Exception:
        return {}

def build_drawdown_ranking():
    df_all = load_highlow_multi(["today", "yesterday", "target2day", "target3day"])
    if df_all.empty:
        return pd.DataFrame()

    # 重複コードは「high_date が最新」を採用
    if "code" not in df_all.columns:
        return pd.DataFrame()

    df_all = df_all.sort_values(["code", "high_date"], ascending=[True, False])
    df_u = df_all.drop_duplicates(subset=["code"], keep="first").copy()

    # 数値化
    for c in ["high", "low"]:
        if c in df_u.columns:
            df_u[c] = pd.to_numeric(df_u[c], errors="coerce")
    df_u.dropna(subset=["high", "low"], inplace=True)

    # ★ここを追加：codeを正規化（"6961.0" 問題を潰す）
    df_u["code_key"] = pd.to_numeric(df_u["code"], errors="coerce").astype("Int64").astype(str)

    # 現在値（= candle の最新 close）を付与
    cur_map = load_current_price_map()

    @st.cache_data(ttl=1800)
    def fetch_current_one(code: str):
        try:
            url = "https://app.kumagai-stock.com/api/highlow"
            r = requests.get(url, params={"code": str(code)}, timeout=10)
            r.raise_for_status()
            return r.json().get("current_price", None)
        except Exception:
            return None

    # current付与後
    cur_map = load_current_price_map()
    df_u["current"] = df_u["code"].astype(str).map(cur_map)
    df_u["current"] = pd.to_numeric(df_u["current"], errors="coerce")

# ★不足分だけ補完（欠損が多いと重くなるので上限付けてもOK）
    mask = df_u["current"].isna()
    if mask.any():
        df_u.loc[mask, "current"] = df_u.loc[mask, "code"].astype(str).apply(fetch_current_one)
        df_u["current"] = pd.to_numeric(df_u["current"], errors="coerce")

    # 上昇率・下落率
    df_u["rise_rate"] = df_u["high"] / df_u["low"]

    # 上げ幅に対する下落率 = (高値-現在値)/(高値-安値)
    df_u["up_range"] = df_u["high"] - df_u["low"]
    df_u["drawdown_from_high"] = np.where(
        df_u["up_range"] > 0,
        (df_u["high"] - df_u["current"]) / df_u["up_range"],
        np.nan
    )
    # 表示用整形
    df_u["low_date"] = df_u["low_date"].dt.strftime("%Y-%m-%d")
    df_u["high_date"] = df_u["high_date"].dt.strftime("%Y-%m-%d")

    # 並び替え：下落率が大きい順（＝高値からよく下げてる順）
    df_u = df_u.sort_values("drawdown_from_high", ascending=False)

    # 画面の列順に合わせる
    out = df_u[[
        "code",
        "name",
        "low", "low_date",
        "high", "high_date",
        "rise_rate",
        "current",
        "drawdown_from_high"
    ]].copy()

    out.rename(columns={
        "code": "コード",
        "name": "銘柄名",
        "low": "安値",
        "low_date": "安値日",
        "high": "高値",
        "high_date": "高値日",
        "rise_rate": "上昇率",
        "current": "現在値",
        "drawdown_from_high": "上げ幅に対する下落率"
    }, inplace=True)

    return out

# ▼ ここで表示（好きな場所に置いてOK）
st.markdown("## 上げ幅に対する下落率ランキング（本日〜3日前までの全銘柄）")
rank_df = build_drawdown_ranking()

if rank_df.empty:
    st.info("ランキング対象データがありません。")
else:
    show = rank_df.copy()
    
    # 上昇率を「◯◯倍（小数1位）」に変換
    # ※ build_drawdown_ranking() 内の「上昇率」は、後述の修正で倍率が入る前提

    show["上昇率"] = show["上昇率"].astype(float).map(lambda x: f"{x:.1f}倍" if pd.notna(x) else "")

    # 下落率はパーセントのまま
    show["上げ幅に対する下落率"] = pd.to_numeric(show["上げ幅に対する下落率"], errors="coerce").apply(
        lambda x: f"{x*100:.1f}%" if pd.notna(x) else ""
    )

    # ▼ 追加：順位（1からの連番）
    show.insert(0, "順位", range(1, len(show) + 1))

    # ▼ 追加：価格系を小数1位に固定（見た目を確実にするため文字列化）
    for c in ["安値", "高値", "現在値"]:
        show[c] = pd.to_numeric(show[c], errors="coerce").map(lambda x: f"{x:,.1f}" if pd.notna(x) else "")

    show = show.reset_index(drop=True)
    show.index = [""] * len(show)

    # ✅ スクロールなしで全表示
    st.table(show)


st.markdown("""
<div style='
    text-align: center;
    color: gray;
    font-size: 14px;
    font-family: "Segoe UI", "Helvetica Neue", "Arial", sans-serif !important;
    letter-spacing: 0.5px;
    unicode-bidi: plaintext;
'>
&copy; 2025 KumagaiNext All rights reserved.
</div>
""", unsafe_allow_html=True)