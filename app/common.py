"""Streamlit 앱 공용 — DB 연결, 화면 스타일, 기준일·기간, 캐시, 그래프 그리기."""
import html
import json
import sys
from datetime import date
from pathlib import Path

import altair as alt
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_driver  # noqa: E402

DATA_START, DATA_END = date(2026, 7, 1), date(2026, 9, 30)
MONTHS = {"7월": (date(2026, 7, 1), date(2026, 7, 31)), "8월": (date(2026, 8, 1), date(2026, 8, 31)),
          "9월": (date(2026, 9, 1), date(2026, 9, 30)), "3개월 전체": (DATA_START, DATA_END)}

# 색 — dataviz 검증 팔레트(다크, 표면 #1a1a19에서 5색 모두 통과). 시스템(출처)마다 고정.
SURFACE, TEXT, TEXT_2, MUTED, GRID = "#1a1a19", "#f0efec", "#c3c2b7", "#8a897f", "#2c2c2a"
SYSTEM_COLORS = {"POS": "#3987e5", "MES": "#d95926", "SCM": "#199e70", "ERP": "#c98500", "ONTOLOGY": "#d55181"}
SYSTEM_KO = {"POS": "POS 포스·고객", "MES": "MES 주방", "SCM": "SCM 구매·재고", "ERP": "ERP 회계·인사",
             "ONTOLOGY": "온톨로지 쓰기(액션·시나리오·결정)"}
QUADRANT_COLORS = {"Star": "#3987e5", "Plowhorse": "#d95926", "Puzzle": "#199e70", "Dog": "#c98500"}
QUADRANT_KO = {"Star": "Star 효자", "Plowhorse": "Plowhorse 박리다매", "Puzzle": "Puzzle 숨은 보석", "Dog": "Dog 정리 후보"}

CSS = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select, label {
  font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
}
.stMarkdown p, .stMarkdown li, .stCaption, h1, h2, h3 { word-break: keep-all; }
h1 { letter-spacing: -0.02em; font-weight: 700; }
[data-testid="stMetricValue"], [data-testid="stDataFrame"], .num { font-variant-numeric: tabular-nums; }
[data-testid="stMetric"] {
  background: #1a1a19; border: 1px solid rgba(240,239,236,0.06); border-radius: 16px; padding: 14px 18px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}
.eyebrow { display:inline-block; padding:2px 10px; border-radius:999px; font-size:11px; letter-spacing:.12em;
  background: rgba(212,162,76,.12); color:#d4a24c; font-weight:600; margin-bottom:4px; }
.legend { display:flex; flex-wrap:wrap; gap:14px; font-size:13px; color:#c3c2b7; margin:4px 0 8px; }
.legend span i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }
.card { background:#1a1a19; border:1px solid rgba(240,239,236,0.06); border-radius:16px; padding:14px 18px; margin-bottom:10px; }
.card b { color:#f0efec; } .card { color:#c3c2b7; word-break: keep-all; }
</style>
"""


def setup(title, layer=None, caption=None):
    """모든 화면 공통: 스타일 주입, 제목, 사이드바 기준일. 기준일(date)을 돌려준다."""
    st.markdown(CSS, unsafe_allow_html=True)
    if layer:
        st.markdown(f'<span class="eyebrow">{layer}</span>', unsafe_allow_html=True)
    st.title(title)
    if caption:
        st.caption(caption)
    with st.sidebar:
        as_of = st.date_input("기준일", value=st.session_state.get("as_of", DATA_END), min_value=DATA_START,
                              max_value=DATA_END, key="as_of",
                              help="재고·원가·고객 세그먼트를 이 날짜 기준으로 계산합니다(데이터 기간 2026-07-01~09-30)")
        st.caption("데이터: 가상 한식당 '한솥밥상' 3개월")
    return as_of


def period_picker(key, default="9월"):
    label = st.segmented_control("기간", list(MONTHS), default=default, key=key) or default
    return MONTHS[label]


@st.cache_resource(show_spinner=False)
def driver():
    d = get_driver()
    d.verify_connectivity()
    return d


def db_or_stop():
    try:
        return driver()
    except Exception as e:  # noqa: BLE001
        st.error(f"Neo4j(bolt://localhost:7688)에 연결할 수 없습니다. 컨테이너를 켜 주세요: docker start restaurant-neo4j\n\n{e}")
        st.stop()


def refresh():
    """데이터를 바꾼 뒤(액션·시나리오·결정) 캐시를 비운다."""
    st.cache_data.clear()


def won(x):
    return f"{x:,.0f}원"


def won_short(x):
    """큰 금액을 짧게: 1억 2,355만원 → '1.24억원', 3,450,000 → '345만원'."""
    return f"{x / 1e8:.2f}억원" if abs(x) >= 1e8 else f"{x / 1e4:,.0f}만원"


def pct(x, digits=1):
    return f"{x * 100:.{digits}f}%"


def legend(items):
    spans = "".join(f'<span><i style="background:{c}"></i>{html.escape(t)}</span>' for t, c in items)
    st.markdown(f'<div class="legend">{spans}</div>', unsafe_allow_html=True)


def system_of(value):
    """'POS+MES' 같은 출처는 첫 시스템 색으로 칠한다."""
    return (value or "ONTOLOGY").split("+")[0]


def graph(nodes, edges, height=560):
    """vis-network로 노드·선을 그린다. nodes: {id,label,system,title}, edges: {from,to,label,title,width,dashes}"""
    vis_nodes = [{"id": n["id"], "label": n["label"], "title": n.get("title", n["label"]),
                  "color": {"background": SYSTEM_COLORS[system_of(n.get("system"))],
                            "border": SURFACE, "highlight": {"background": TEXT, "border": TEXT}},
                  "size": n.get("size", 14)} for n in nodes]
    vis_edges = [{"from": e["from"], "to": e["to"], "label": e.get("label", ""), "title": e.get("title", ""),
                  "width": e.get("width", 1), "dashes": e.get("dashes", False)} for e in edges]
    page = f"""
<div id="g" style="height:{height}px;background:{SURFACE};border-radius:16px;border:1px solid rgba(240,239,236,.06)"></div>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script>
const net = new vis.Network(document.getElementById('g'),
  {{nodes: new vis.DataSet({json.dumps(vis_nodes, ensure_ascii=False)}),
    edges: new vis.DataSet({json.dumps(vis_edges, ensure_ascii=False)})}},
  {{nodes: {{shape: 'dot', borderWidth: 2, font: {{color: '{TEXT}', size: 16, face: 'Pretendard, sans-serif',
             strokeWidth: 3, strokeColor: '{SURFACE}'}}}},
    edges: {{color: {{color: '#5a5955', highlight: '{TEXT_2}', hover: '{TEXT_2}'}},
             font: {{color: '{TEXT_2}', size: 10, face: 'Pretendard, sans-serif', strokeWidth: 0}},
             arrows: {{to: {{enabled: true, scaleFactor: 0.5}}}}, smooth: {{type: 'continuous'}}}},
    physics: {{barnesHut: {{gravitationalConstant: -7000, springLength: 120}}, stabilization: {{iterations: 200}}}},
    interaction: {{hover: true, tooltipDelay: 80, navigationButtons: false}}}});
</script>"""
    components.html(page, height=height + 10)


def chart_theme(chart):
    """altair 차트 공통: 다크 표면, 보조 글자색, 옅은 격자, 테두리 없음."""
    return (chart.configure(background=SURFACE, font="Pretendard")
            .configure_view(stroke=None)
            .configure_axis(labelColor=TEXT_2, titleColor=TEXT_2, gridColor=GRID, domainColor=GRID, tickColor=GRID,
                            labelFontSize=12, titleFontSize=12)
            .configure_legend(labelColor=TEXT_2, titleColor=TEXT_2, orient="top")
            .configure_title(color=TEXT, fontSize=14, anchor="start"))


def show_chart(chart):
    st.altair_chart(chart_theme(chart), use_container_width=True)


alt.data_transformers.disable_max_rows()
