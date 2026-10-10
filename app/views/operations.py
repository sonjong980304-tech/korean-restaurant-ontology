"""Kinetic Function — 지표를 계산해 보여준다(읽기 전용)."""
import altair as alt
import pandas as pd
import streamlit as st
from common import (MUTED, QUADRANT_COLORS, QUADRANT_KO, SYSTEM_COLORS, TEXT_2, db_or_stop, legend, pct, period_picker,
                    setup, show_chart)

from kinetic import functions as F

as_of = setup("운영 현황", "KINETIC · FUNCTION", "온톨로지를 읽어 계산만 합니다. 데이터는 바꾸지 않습니다.")
d = db_or_stop()
start, end = period_picker("ops_period")
BLUE = SYSTEM_COLORS["POS"]


@st.cache_data(ttl=600, show_spinner=False)
def load(_d, name, *args):
    return getattr(F, name)(_d, *args)


t1, t2, t3, t4, t5 = st.tabs(["메뉴 엔지니어링", "회전율·대기", "원가율", "재료 로스·재고", "고객"])

with t1:
    rows = pd.DataFrame(load(d, "menu_engineering", start, end))
    st.caption("가로 = 판매 비중, 세로 = 한 그릇 공헌이익. 점선 = 기준선(인기: 0.7 ÷ 메뉴 수, 마진: 판매량 가중평균). 주류·음료 제외. 이름은 비인기 메뉴와 판매 상위 3개만 표시 — 나머지는 점에 마우스를 올리세요.")
    legend([(QUADRANT_KO[q], c) for q, c in QUADRANT_COLORS.items()])
    rows["사분면"] = rows["quadrant"].map(QUADRANT_KO)
    base = alt.Chart(rows).encode(
        x=alt.X("mix:Q", title="판매 비중", axis=alt.Axis(format="%")),
        y=alt.Y("margin:Q", title="공헌이익(원)", axis=alt.Axis(format=",.0f")))
    dots = base.mark_circle(size=140, opacity=0.95, stroke="#1a1a19", strokeWidth=2).encode(
        color=alt.Color("quadrant:N", scale=alt.Scale(domain=list(QUADRANT_COLORS), range=list(QUADRANT_COLORS.values())),
                        legend=None),
        tooltip=[alt.Tooltip("menu:N", title="메뉴"), alt.Tooltip("사분면:N"), alt.Tooltip("sold:Q", title="판매", format=","),
                 alt.Tooltip("mix:Q", title="비중", format=".1%"), alt.Tooltip("margin:Q", title="공헌이익", format=",.0f")])
    # 직접 라벨은 골라서: 비인기(Puzzle·Dog)와 판매 상위 3개. 나머지는 마우스를 올리거나 표로 확인
    top3 = set(rows.nlargest(3, "sold")["menu"])
    picked = rows[rows["quadrant"].isin(["Puzzle", "Dog"]) | rows["menu"].isin(top3)]
    labels = alt.Chart(picked).mark_text(dx=9, align="left", fontSize=12, color=TEXT_2).encode(
        x="mix:Q", y="margin:Q", text="menu:N")
    vline = alt.Chart(rows.head(1)).mark_rule(strokeDash=[4, 4], color=MUTED).encode(x="popularity_threshold:Q")
    hline = alt.Chart(rows.head(1)).mark_rule(strokeDash=[4, 4], color=MUTED).encode(y="avg_margin_threshold:Q")
    show_chart((dots + labels + vline + hline).properties(height=460))
    with st.expander("표로 보기"):
        st.dataframe(rows[["menu", "사분면", "sold", "mix", "margin"]].rename(
            columns={"menu": "메뉴", "sold": "판매", "mix": "비중", "margin": "공헌이익"}),
            hide_index=True, use_container_width=True,
            column_config={"비중": st.column_config.NumberColumn(format="percent"),
                           "공헌이익": st.column_config.NumberColumn(format="%,.0f")})

with t2:
    turns = pd.DataFrame(load(d, "table_turnover", start, end))
    turns["요일"] = turns["date"].map(lambda x: "월화수목금토일"[x.weekday()])
    heat = turns.groupby(["요일", "slot"], as_index=False)["table_turns"].mean()
    left, right = st.columns(2)
    with left:
        st.markdown("**요일·시간대별 평균 테이블 회전율**")
        show_chart(alt.Chart(heat).mark_rect(cornerRadius=4, stroke="#1a1a19", strokeWidth=2).encode(
            x=alt.X("요일:N", sort=list("화수목금토일"), title=None),
            y=alt.Y("slot:N", sort=["점심", "저녁"], title=None),
            color=alt.Color("table_turns:Q", scale=alt.Scale(scheme="blues"), title="회전율"),
            tooltip=["요일", alt.Tooltip("slot:N", title="시간대"),
                     alt.Tooltip("table_turns:Q", title="평균 회전", format=".2f")]).properties(height=220)
            + alt.Chart(heat).mark_text(fontSize=12, color="#0b0b0b").encode(
                x=alt.X("요일:N", sort=list("화수목금토일")), y=alt.Y("slot:N", sort=["점심", "저녁"]),
                text=alt.Text("table_turns:Q", format=".1f")))
    with right:
        wait = pd.DataFrame(load(d, "waiting_stats", start, end))
        st.markdown("**동행유형별 대기 이탈률**")
        show_chart(alt.Chart(wait).mark_bar(color=BLUE, cornerRadiusEnd=4, height=18).encode(
            x=alt.X("abandon_rate:Q", title="이탈률", axis=alt.Axis(format="%")),
            y=alt.Y("party_type:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("party_type:N", title="동행"), alt.Tooltip("visits:Q", title="방문 팀", format=","),
                     alt.Tooltip("abandoned:Q", title="떠난 팀", format=","),
                     alt.Tooltip("abandon_rate:Q", title="이탈률", format=".1%"),
                     alt.Tooltip("avg_wait_min:Q", title="평균 대기(분)", format=".1f")]).properties(height=220)
            + alt.Chart(wait).mark_text(align="left", dx=4, color=TEXT_2, fontSize=11).encode(
                x="abandon_rate:Q", y=alt.Y("party_type:N", sort="-x"), text=alt.Text("abandon_rate:Q", format=".0%")))

with t3:
    cost = pd.DataFrame(load(d, "monthly_cost_ratio"))
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("**월별 식재료 원가율** (ERP 전표: 식재료비 ÷ 매출)")
        show_chart(alt.Chart(cost).mark_line(point=alt.OverlayMarkDef(size=90, filled=True), color=BLUE, strokeWidth=2).encode(
            x=alt.X("month:O", title=None), y=alt.Y("cost_ratio:Q", title="원가율", axis=alt.Axis(format="%"),
                                                     scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("month:O", title="월"), alt.Tooltip("sales:Q", title="매출", format=","),
                     alt.Tooltip("food_cost:Q", title="식재료비", format=","),
                     alt.Tooltip("cost_ratio:Q", title="원가율", format=".1%")]).properties(height=280)
            + alt.Chart(cost).mark_text(dy=-14, color=TEXT_2).encode(x="month:O", y="cost_ratio:Q",
                                                                     text=alt.Text("cost_ratio:Q", format=".1%")))
    with c2:
        st.dataframe(cost.rename(columns={"month": "월", "sales": "매출", "food_cost": "식재료비", "cost_ratio": "원가율"}),
                     hide_index=True, use_container_width=True,
                     column_config={"원가율": st.column_config.NumberColumn(format="percent"),
                                    "매출": st.column_config.NumberColumn(format="%,d"),
                                    "식재료비": st.column_config.NumberColumn(format="%,d")})

with t4:
    var = pd.DataFrame(load(d, "usage_variance", start, end))
    st.markdown("**레시피 대비 과다사용률과 폐기율** — 과다사용 = (실제 소진 − 레시피상 사용) ÷ 레시피상 사용")
    st.dataframe(var[["ingredient", "unit", "theoretical", "actual", "variance_rate", "total_usage", "waste", "waste_rate"]]
                 .rename(columns={"ingredient": "재료", "unit": "단위", "theoretical": "레시피상", "actual": "실제(조리)",
                                  "variance_rate": "과다사용률", "total_usage": "전체 사용", "waste": "폐기",
                                  "waste_rate": "폐기율"}),
                 hide_index=True, use_container_width=True, height=360,
                 column_config={"과다사용률": st.column_config.NumberColumn(format="percent"),
                                "폐기율": st.column_config.NumberColumn(format="percent"),
                                **{c: st.column_config.NumberColumn(format="%,.0f")
                                   for c in ("레시피상", "실제(조리)", "전체 사용", "폐기")}})
    stock = pd.DataFrame(load(d, "stock_status", as_of))
    st.markdown(f"**재고 현황** — 기준일 {as_of}, 하루 사용량 = 최근 7일 평균")
    st.dataframe(stock[["ingredient", "unit", "stock", "daily_usage", "days_left", "safety_stock", "below_safety"]]
                 .rename(columns={"ingredient": "재료", "unit": "단위", "stock": "현재고", "daily_usage": "하루 사용",
                                  "days_left": "소진 예상(일)", "safety_stock": "안전재고", "below_safety": "미달"}),
                 hide_index=True, use_container_width=True, height=360,
                 column_config={c: st.column_config.NumberColumn(format="%,.1f")
                                for c in ("현재고", "하루 사용", "소진 예상(일)", "안전재고")})

with t5:
    seg = load(d, "segment_summary", as_of)
    df = pd.DataFrame({"세그먼트": list(seg), "회원 수": list(seg.values())})
    left, right = st.columns([2, 3])
    with left:
        st.markdown(f"**고객 세그먼트** (기준일 {as_of})")
        show_chart(alt.Chart(df).mark_bar(color=BLUE, cornerRadiusEnd=4, height=20).encode(
            x=alt.X("회원 수:Q", title=None), y=alt.Y("세그먼트:N", sort=list(seg), title=None),
            tooltip=["세그먼트", alt.Tooltip("회원 수:Q", format=",")]).properties(height=220)
            + alt.Chart(df).mark_text(align="left", dx=4, color=TEXT_2).encode(
                x="회원 수:Q", y=alt.Y("세그먼트:N", sort=list(seg)), text=alt.Text("회원 수:Q", format=",")))
        st.caption("이탈위험: 단골이었다가 30일 이상 미방문 · 단골: 90일 4회 이상 · VIP: 단골 중 객단가 상위 20%")
    with right:
        who = st.selectbox("명단 보기", ["이탈위험", "VIP", "단골", "재방문", "신규"])
        members = [r for r in load(d, "customer_segments", as_of) if r["segment"] == who]
        st.dataframe(pd.DataFrame(members)[["customer_id", "visits", "visits_90d", "last_visit", "days_since_last", "avg_spend"]]
                     .rename(columns={"customer_id": "회원", "visits": "누적 방문", "visits_90d": "90일 방문",
                                      "last_visit": "마지막 방문", "days_since_last": "경과일", "avg_spend": "평균 결제"}),
                     hide_index=True, use_container_width=True, height=300,
                     column_config={"평균 결제": st.column_config.NumberColumn(format="%,.0f")})
