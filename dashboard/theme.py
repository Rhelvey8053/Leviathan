"""
Shared visual theme for the Leviathan dashboard -- color palette, Plotly
template, and CSS injection. Imported by app.py and every page so all
three look and feel like one product instead of three separate scripts.

Small-n honesty is a design constraint, not just a data-contract note:
this dashboard's real dataset is 46 bets, 16 resolved. Any chart built on
the resolved subset (or smaller) must show its n prominently -- CSS here
provides a `.small-n-badge` class for that, used consistently instead of
each page inventing its own wording.
"""

import streamlit as st

# Categorical palette -- used for win/loss, category, and flag_path charts
# so the same value always gets the same color across pages.
WIN_COLOR = "#2E7D32"
LOSS_COLOR = "#C62828"
NEUTRAL_COLOR = "#546E7A"
ACCENT_COLOR = "#1565C0"

CATEGORICAL_SEQUENCE = [
    "#1565C0", "#2E7D32", "#EF6C00", "#6A1B9A",
    "#00838F", "#AD1457", "#5D4037", "#616161",
]

PLOTLY_TEMPLATE = {
    "layout": {
        "colorway": CATEGORICAL_SEQUENCE,
        "font": {"family": "Segoe UI, -apple-system, sans-serif", "size": 13, "color": "#263238"},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"t": 40, "b": 40, "l": 50, "r": 20},
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        "xaxis": {"gridcolor": "#E0E0E0", "zerolinecolor": "#BDBDBD"},
        "yaxis": {"gridcolor": "#E0E0E0", "zerolinecolor": "#BDBDBD"},
    }
}


def inject_css():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; max-width: 1200px; }

        div[data-testid="stMetric"] {
            background: rgba(127, 127, 127, 0.06);
            border: 1px solid rgba(127, 127, 127, 0.15);
            border-radius: 10px;
            padding: 14px 16px 10px 16px;
        }
        div[data-testid="stMetricLabel"] { font-size: 0.8rem; opacity: 0.75; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }

        .lv-header-row { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
        .lv-title { font-size: 1.9rem; font-weight: 650; margin: 0; }
        .lv-subtitle { opacity: 0.65; font-size: 0.95rem; }

        .small-n-badge {
            display: inline-block;
            font-size: 0.78rem;
            font-weight: 600;
            padding: 2px 9px;
            border-radius: 999px;
            background: rgba(239, 108, 0, 0.14);
            color: #EF6C00;
            border: 1px solid rgba(239, 108, 0, 0.35);
            margin-left: 6px;
        }
        .lv-caption { opacity: 0.65; font-size: 0.85rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str = ""):
    sub_html = f'<span class="lv-subtitle">{subtitle}</span>' if subtitle else ""
    st.markdown(
        f'<div class="lv-header-row"><span class="lv-title">{title}</span>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def small_n_badge(n: int, threshold: int = 20) -> str:
    """Inline HTML badge flagging a chart built on a small sample. Empty string if n is large enough not to need one."""
    if n >= threshold:
        return ""
    return f'<span class="small-n-badge">small sample, n={n}</span>'
