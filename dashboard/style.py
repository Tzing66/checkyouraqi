"""Page-wide styles: soft cards, AQI pills, notices. The colours live in .streamlit/config.toml;
this only adds what Streamlit's theme can't express."""

from __future__ import annotations

import html

import streamlit as st

CSS = """
<style>
.block-container { max-width: 1120px; padding-top: 2.2rem; padding-bottom: 3rem; }
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3 { letter-spacing: -0.02em; }
h1 { font-weight: 700; }
[data-testid="stHeading"] h3 { margin-top: 1.4rem; }

.cya-sub { color: #6B7180; margin-top: -0.6rem; margin-bottom: 1.2rem; font-size: 1.02rem; }
.cya-card {
  background: #FFFFFF; border: 1px solid #E3E7F2; border-radius: 18px;
  padding: 1.15rem 1.3rem; box-shadow: 0 1px 3px rgba(30, 40, 80, 0.05); height: 100%;
}
.cya-label { color: #6B7180; font-size: 0.82rem; font-weight: 600; letter-spacing: 0.02em;
  text-transform: uppercase; margin-bottom: 0.35rem; }
.cya-value { font-size: 2.6rem; font-weight: 700; line-height: 1.1; color: #2B2F3A; }
.cya-value small { font-size: 1rem; font-weight: 500; color: #6B7180; margin-left: 0.2rem; }
.cya-value-sm { font-size: 1.7rem; font-weight: 700; line-height: 1.15; color: #2B2F3A; }
.cya-value-sm small { font-size: 0.85rem; font-weight: 500; color: #6B7180; margin-left: 0.15rem; }
.cya-meta { color: #6B7180; font-size: 0.86rem; margin-top: 0.55rem; }
.cya-advice { color: #3F4554; font-size: 0.93rem; margin-top: 0.6rem; }

.cya-pill {
  display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.18rem 0.7rem;
  border-radius: 999px; border: 1px solid transparent; font-size: 0.84rem; font-weight: 600;
  color: #2B2F3A; white-space: nowrap; margin-top: 0.45rem;
}
.cya-dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; display: inline-block; }
.cya-legend { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.3rem 0 0.2rem; }
.cya-legend .cya-pill { margin-top: 0; font-weight: 500; }

.cya-note {
  display: flex; gap: 0.7rem; align-items: flex-start; background: #FFF7E8;
  border: 1px solid #F5E3BC; color: #5E4A1E; border-radius: 14px;
  padding: 0.8rem 1rem; margin: 0.4rem 0 1.3rem; font-size: 0.93rem;
}
.cya-note.info { background: #EEF2FF; border-color: #D9E0FB; color: #36406E; }
.cya-note b { font-weight: 600; }

.cya-zone { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; }
.cya-zone-name { font-weight: 600; color: #2B2F3A; }
.cya-zone .cya-pill { margin-top: 0.3rem; }
.cya-foot { color: #8A90A0; font-size: 0.8rem; margin-top: 2.5rem; }
iframe { border-radius: 16px; }
</style>
"""


def apply() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def card(body: str) -> None:
    st.markdown(f'<div class="cya-card">{body}</div>', unsafe_allow_html=True)


def note(text: str, kind: str = "warn") -> None:
    """Soft notice box (amber for paused data, blue for information). Text is escaped."""
    icon = "⏸" if kind == "warn" else "ℹ"
    cls = "cya-note" if kind == "warn" else "cya-note info"
    st.markdown(
        f'<div class="{cls}"><span>{icon}</span><span>{html.escape(text)}</span></div>',
        unsafe_allow_html=True,
    )
