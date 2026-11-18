"""Visual styling shared by the hospital and patient screens."""

import streamlit as st


def apply_theme():
    st.markdown("""
<style>
    :root { color-scheme: dark; }
    .stApp {
        background:
            radial-gradient(circle at 18% 0%, rgba(19, 90, 69, .18), transparent 34%),
            radial-gradient(circle at 90% 15%, rgba(193, 145, 65, .07), transparent 28%),
            #0a1119;
    }
    .block-container { max-width: 1480px; padding-top: 2rem; padding-bottom: 4rem; }
    h1, h2, h3 { letter-spacing: -.025em; color: #f5f4e9; }
    h1 { font-weight: 750; }
    p, label { line-height: 1.55; }
    [data-testid="stSidebar"] {
        background: linear-gradient(165deg, #102d25 0%, #0c211f 58%, #0b1820 100%);
        border-right: 1px solid rgba(214, 177, 98, .18);
    }
    [data-testid="stSidebar"] .block-container { padding-top: 1.3rem; }
    [data-testid="stSidebar"] hr { border-color: rgba(210, 226, 209, .14); }
    .ayur-brand {
        display: flex; align-items: center; gap: .55rem;
        color: #f7f5e9; font-size: 1.25rem; font-weight: 760;
        letter-spacing: -.03em; padding: .1rem 0 .9rem;
    }
    .ayur-brand-mark { color: #d9b45f; font-size: 1.5rem; }
    .ayur-hero {
        background: linear-gradient(115deg, rgba(24, 74, 56, .72), rgba(12, 26, 35, .86) 62%, rgba(35, 52, 44, .68));
        border: 1px solid rgba(221, 184, 103, .20); border-radius: 20px;
        padding: 1.65rem 2rem; margin: .35rem 0 1.25rem;
        box-shadow: 0 16px 42px rgba(0, 0, 0, .16);
    }
    .ayur-eyebrow { color: #e1bb72; font-size: .72rem; font-weight: 750; letter-spacing: .16em; text-transform: uppercase; }
    .ayur-hero h1 { font-size: clamp(2rem, 3vw, 2.8rem); line-height: 1.16; margin: .42rem 0 .45rem; }
    .ayur-hero p { color: #bdcfc6; margin: 0; max-width: 750px; }
    .ayur-section-label { color: #d7b16c; font-size: .75rem; font-weight: 760; letter-spacing: .13em; text-transform: uppercase; margin: .5rem 0 .8rem; }
    div[data-testid="stMetric"] {
        background: linear-gradient(145deg, rgba(21, 58, 47, .92), rgba(16, 38, 40, .94));
        border: 1px solid rgba(154, 192, 164, .14); border-radius: 15px;
        padding: 1rem 1.15rem; min-height: 116px;
        box-shadow: 0 10px 28px rgba(0, 0, 0, .12);
    }
    div[data-testid="stMetricLabel"] { color: #bed0c3; font-weight: 600; }
    div[data-testid="stMetricValue"] { color: #f8f5e8; font-weight: 700; }
    div[data-testid="stForm"], div[data-testid="stExpander"] {
        background: linear-gradient(150deg, rgba(18, 49, 40, .9), rgba(14, 29, 36, .94));
        border: 1px solid rgba(156, 191, 162, .14); border-radius: 16px;
        box-shadow: 0 12px 30px rgba(0, 0, 0, .10);
    }
    div[data-testid="stForm"] { padding: 1.1rem 1.15rem; }
    div[data-testid="stDataFrame"], div[data-testid="stPlotlyChart"] {
        overflow: hidden; border: 1px solid rgba(156, 191, 162, .13); border-radius: 15px;
        background: rgba(17, 39, 36, .48);
    }
    div[data-testid="stTabs"] [role="tablist"] {
        gap: .35rem; border-bottom: 1px solid rgba(190, 213, 190, .14); padding-bottom: .45rem;
    }
    div[data-testid="stTabs"] button[role="tab"] {
        border-radius: 10px; padding: .55rem .8rem; color: #afc6ba; font-weight: 620;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        background: rgba(211, 171, 86, .15); color: #f0ca79;
    }
    div[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: #d7ae5b; }
    div.stButton > button, div.stDownloadButton > button, div.stFormSubmitButton > button {
        border-radius: 10px; border-color: rgba(217, 180, 95, .35);
        font-weight: 650; transition: transform .15s ease, border-color .15s ease, background .15s ease;
    }
    div.stButton > button:hover, div.stDownloadButton > button:hover, div.stFormSubmitButton > button:hover {
        border-color: #e0bb70; transform: translateY(-1px);
    }
    button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(110deg, #d9ac54, #e9c674); color: #18231b; border: 0;
    }
    button[data-testid="stBaseButton-primary"]:hover { background: #f0d28d; color: #111d18; }
    div[data-baseweb="input"] > div, div[data-baseweb="select"] > div {
        border-radius: 10px; border-color: rgba(179, 204, 183, .22);
    }
    @media (max-width: 900px) {
        .block-container { padding: 1.1rem .85rem 3rem; }
        .ayur-hero { padding: 1.25rem; border-radius: 15px; }
        .ayur-hero h1 { font-size: 1.8rem; }
    }
</style>
""", unsafe_allow_html=True)
