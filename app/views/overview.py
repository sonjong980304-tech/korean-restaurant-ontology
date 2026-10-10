"""개요 — 이번 달 핵심 숫자와 지금 볼 경보."""
import html

import pandas as pd
import streamlit as st
from common import MONTHS, db_or_stop, pct, setup, won, won_short

from kinetic import functions as F

as_of = setup("한솥밥상 운영 개요", "OVERVIEW", "온톨로지(그래프) 하나에서 POS·주방·구매·회계를 함께 읽은 결과입니다.")
d = db_or_stop()


@st.cache_data(ttl=600, show_spinner=False)
def load(_d, month, as_of):
    start, end = MONTHS[month]
    return {"cost": F.monthly_cost_ratio(_d), "turn": F.table_turnover(_d, start, end),
            "wait": F.waiting_stats(_d, start, end), "seg": F.segment_summary(_d, as_of),
            "stock": F.stock_status(_d, as_of), "menu": F.menu_engineering(_d, start, end)}


month = st.segmented_control("월", ["7월", "8월", "9월"], default="9월", key="ov_month") or "9월"
x = load(d, month, as_of)
key = {"7월": "2026-07", "8월": "2026-08", "9월": "2026-09"}[month]
cost = {r["month"]: r for r in x["cost"]}
cur = cost[key]
prev = cost.get({"2026-08": "2026-07", "2026-09": "2026-08"}.get(key, ""), None)
visits = sum(r["visits"] for r in x["wait"])
lost = sum(r["abandoned"] for r in x["wait"])
turns = pd.DataFrame(x["turn"])

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("매출", won_short(cur["sales"]), None if not prev else f"{(cur['sales'] / prev['sales'] - 1) * 100:+.1f}% 전월 대비")
c2.metric("식재료 원가율", pct(cur["cost_ratio"]),
          None if not prev else f"{(cur['cost_ratio'] - prev['cost_ratio']) * 100:+.1f}%p", delta_color="inverse")
c3.metric("하루 평균 테이블 회전", f"{turns.groupby('date')['table_turns'].sum().mean():.1f}회",
          help="점심+저녁 착석 팀 수 ÷ 테이블 18개")
c4.metric("대기 중 떠난 팀", f"{lost:,}팀", help=f"전체 방문 {visits:,}팀의 {pct(lost / visits)}")
c5.metric("이탈위험 단골", f"{x['seg']['이탈위험']:,}명",
          help=f"기준일 {as_of} — 단골이었다가 30일 이상 오지 않은 회원")

st.subheader("지금 볼 것")
left, right = st.columns([3, 2])
with left:
    worst = x["wait"][0]
    st.markdown(f'<div class="card"><b>놓치는 손님</b> — {html.escape(worst["party_type"])} 손님 {worst["visits"]:,}팀 중 '
                f'{worst["abandoned"]:,}팀({pct(worst["abandon_rate"])})이 기다리다 떠났습니다. '
                f'착석한 팀의 평균 대기 {worst["avg_wait_min"]:.1f}분.</div>', unsafe_allow_html=True)
    dogs = [html.escape(r["menu"]) for r in x["menu"] if r["quadrant"] == "Dog"]
    stars = [html.escape(r["menu"]) for r in x["menu"] if r["quadrant"] == "Star"]
    st.markdown(f'<div class="card"><b>메뉴</b> — 잘 팔리고 많이 남는 Star: {", ".join(stars) or "없음"}. '
                f'안 팔리고 덜 남는 Dog(정리 후보): {", ".join(dogs) or "없음"}.</div>', unsafe_allow_html=True)
    if prev:
        st.markdown(f'<div class="card"><b>원가</b> — 원가율이 전월 {pct(prev["cost_ratio"])} → {pct(cur["cost_ratio"])}로 '
                    f'{(cur["cost_ratio"] - prev["cost_ratio"]) * 100:+.1f}%p 변했습니다. 원인은 '
                    f'「예측·시뮬레이션」의 원가 충격에서 재료별로 확인할 수 있습니다.</div>', unsafe_allow_html=True)
with right:
    low = [r for r in x["stock"] if r["below_safety"] or (r["days_left"] is not None and r["days_left"] < 3)]
    st.markdown("**재고 경보** (안전재고 미달 또는 3일 안에 소진 예상)")
    if low:
        st.dataframe(pd.DataFrame([{"재료": r["ingredient"], "현재고": f"{r['stock']:,.0f}{r['unit']}",
                                    "소진 예상": "-" if r["days_left"] is None else f"{r['days_left']:.1f}일",
                                    "안전재고 미달": "예" if r["below_safety"] else ""} for r in low]),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("경보 없음")

st.divider()
st.markdown("**이 앱의 구조 — 팔란티어 온톨로지의 3개 레이어**")
a, b, c = st.columns(3)
a.markdown('<div class="card"><b>Semantic · 지도</b><br>무엇이 있고 어떻게 이어지나. 객체 22종, 연결 29종, '
           '원천 시스템 4개를 온톨로지가 연결합니다.</div>', unsafe_allow_html=True)
b.markdown('<div class="card"><b>Kinetic · 손발</b><br>지표를 계산하고(Function) 데이터를 바꿉니다(Action). '
           '모든 액션은 검증 후 기록됩니다.</div>', unsafe_allow_html=True)
c.markdown('<div class="card"><b>Dynamic · 두뇌</b><br>예측, What-if 시나리오, 의사결정 기록, '
           '그래프 근거로 답하는 AI 매니저.</div>', unsafe_allow_html=True)
