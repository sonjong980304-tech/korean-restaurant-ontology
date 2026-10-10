"""한솥밥상 온톨로지 앱 — 실행: .venv/bin/streamlit run app/main.py"""
import streamlit as st

st.set_page_config(page_title="한솥밥상 온톨로지", layout="wide")
pages = {
    "": [st.Page("views/overview.py", title="개요", default=True)],
    "Semantic · 지도": [st.Page("views/ontology.py", title="온톨로지 탐색")],
    "Kinetic · 손발": [st.Page("views/operations.py", title="운영 현황"), st.Page("views/actions.py", title="액션")],
    "Dynamic · 두뇌": [st.Page("views/simulation.py", title="예측·시뮬레이션"), st.Page("views/assistant.py", title="AI 매니저")],
}
st.navigation(pages).run()
