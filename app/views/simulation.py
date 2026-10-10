"""Dynamic — 수요 예측, What-if 시나리오, 의사결정 기록."""
import math
from collections import defaultdict
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st
from common import SYSTEM_COLORS, TEXT_2, db_or_stop, period_picker, refresh, setup, show_chart, won

from dynamic import decision as D
from dynamic import forecast as FC
from dynamic import scenario as SC
from kinetic import actions as A

as_of = setup("예측·시뮬레이션", "DYNAMIC",
              "앞으로를 예측하고, '만약 ~라면'을 계산하고, 그 결과로 내린 결정을 근거와 함께 남깁니다.")
d = db_or_stop()
ORANGE = SYSTEM_COLORS["MES"]
ingredients = {r["id"]: r["name"] for r in d.execute_query(
    "MATCH (i:Ingredient) RETURN i.ingredient_id AS id, i.name AS name ORDER BY name").records}

t1, t2, t3, t4, t5 = st.tabs(["수요 예측·발주 추천", "원가 충격", "업체 변경", "의사결정", "전후 비교"])

with t1:
    c1, c2 = st.columns(2)
    target = c1.date_input("예측할 날짜", date(2026, 10, 2), min_value=date(2026, 7, 29))
    weeks = c2.slider("최근 몇 주의 같은 요일을 볼까", 2, 8, 4)
    st.caption(f"{target:%m/%d}({'월화수목금토일'[target.weekday()]}) 예측 = 최근 {weeks}번의 같은 요일 판매량 평균. "
               f"재고는 기준일 {as_of} 기준.")
    demand = FC.forecast_menu_demand(d, target, weeks)
    req = FC.ingredient_requirements(d, target, as_of=as_of, weeks=weeks)
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**메뉴별 예상 판매**")
        st.dataframe(pd.DataFrame(demand)[["menu", "expected_qty"]].rename(columns={"menu": "메뉴", "expected_qty": "예상"}),
                     hide_index=True, use_container_width=True, height=420,
                     column_config={"예상": st.column_config.NumberColumn(format="%.1f")})
    with right:
        st.markdown("**재료 필요량과 추천 발주** — 추천 = 필요량 + 안전재고 − 현재고 − 입고 예정")
        rq = pd.DataFrame(req)
        if not rq.empty:
            st.caption(f"필요량 = 기준일 다음날부터 예측일까지 {rq['horizon_days'].iloc[0]}일치 예측 판매 × 레시피")
        st.dataframe(rq[["ingredient", "unit", "need", "stock", "incoming", "shortage", "recommended_qty", "best_supplier",
                         "best_price"]]
                     .rename(columns={"ingredient": "재료", "unit": "단위", "need": "필요", "stock": "현재고", "incoming": "입고 예정",
                                      "shortage": "부족", "recommended_qty": "추천 발주", "best_supplier": "최저가 업체",
                                      "best_price": "단가"}),
                     hide_index=True, use_container_width=True, height=420,
                     column_config={c: st.column_config.NumberColumn(format="%,.0f")
                                    for c in ("필요", "현재고", "입고 예정", "부족", "추천 발주")})
    todo = defaultdict(dict)
    for r in req:
        if r["recommended_qty"] > 0 and r["best_supplier"]:
            todo[r["best_supplier"]][r["ingredient_id"]] = math.ceil(r["recommended_qty"])   # 0.4개도 1개로 올림
    if todo:
        st.markdown("추천대로 발주하면 업체별로 발주 액션이 하나씩 실행됩니다(예산 등 규칙 검증 포함): "
                    + ", ".join(f"{s} {len(v)}개 품목" for s, v in todo.items()))
        flag = f"po_done_{target}_{as_of}"
        if st.button("추천량으로 발주 실행", type="primary", disabled=st.session_state.get(flag, False),
                     help="한 번 실행하면 같은 날짜 조합으로는 다시 누를 수 없습니다(중복 발주 방지)"):
            st.session_state[flag] = True
            for sid, lines in todo.items():
                r = A.create_purchase_order(d, sid, lines, actor="사장", reason=f"{target} 수요 예측 기반 추천 발주")
                (st.success if r["status"] == "실행" else st.error)(f"{sid}: {r['status']} — {r['message']}")
            refresh()


def scenario_result(key, result, default_title):
    st.metric("기간 영향액", won(result["impact"]), help="+면 비용 증가, −면 절감")
    title = st.text_input("시나리오 이름", default_title, key=f"{key}_title")
    if st.button("시나리오 저장", key=f"{key}_save"):
        sid = SC.save_scenario(d, result, title)
        st.success(f"저장됨: {sid} — 「의사결정」 탭에서 근거로 연결할 수 있습니다.")
        refresh()


with t2:
    c1, c2, c3 = st.columns([2, 2, 3])
    ing = c1.selectbox("재료", list(ingredients), format_func=ingredients.get,
                       index=list(ingredients.values()).index("양파"))
    change = c2.slider("단가 변화", -30, 80, 40, step=5, format="%d%%")
    with c3:
        start, end = period_picker("shock_period")
    res = SC.price_shock(d, ing, change / 100, start, end)
    p = res["params"]
    st.caption(f"{p['ingredient']} 단가 {p['price']:,.2f} → {p['new_price']:,.2f}원/기준단위, "
               f"{start}~{end} 판매량으로 계산. 이 재료를 쓰는 메뉴 {len(res['menus'])}개.")
    m = pd.DataFrame(res["menus"])
    if not m.empty:
        show_chart(alt.Chart(m).mark_bar(color=ORANGE, cornerRadiusEnd=4, height=16).encode(
            x=alt.X("impact:Q", title="기간 원가 변화(원)", axis=alt.Axis(format=",.0f")),
            y=alt.Y("menu:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("menu:N", title="메뉴"), alt.Tooltip("sold:Q", title="판매", format=","),
                     alt.Tooltip("cost:Q", title="원가", format=",.0f"), alt.Tooltip("new_cost:Q", title="새 원가", format=",.0f"),
                     alt.Tooltip("new_cost_ratio:Q", title="새 원가율", format=".1%"),
                     alt.Tooltip("impact:Q", title="영향", format=",.0f")]).properties(height=max(160, 28 * len(m)))
            + alt.Chart(m).mark_text(align="left", dx=4, color=TEXT_2).encode(
                x="impact:Q", y=alt.Y("menu:N", sort="-x"), text=alt.Text("impact:Q", format=",.0f")))
    scenario_result("shock", res, f"{p['ingredient']} {change:+d}%")

with t3:
    sups = {r["id"]: r["name"] for r in d.execute_query(
        "MATCH (s:Supplier) RETURN s.supplier_id AS id, s.name AS name ORDER BY id").records}
    c1, c2 = st.columns([1, 2])
    sid = c1.selectbox("바꿀 업체", list(sups), format_func=lambda s: f"{sups[s]} ({s})",
                       index=list(sups).index("SUP-MEAT-B") if "SUP-MEAT-B" in sups else 0)
    offered = [r["iid"] for r in d.execute_query(
        "MATCH (:Supplier {supplier_id: $s})-[:SUPPLIES]->(i:Ingredient) RETURN i.ingredient_id AS iid ORDER BY i.name",
        s=sid).records]
    picked = c2.multiselect("재료", offered, default=offered, format_func=ingredients.get)
    start, end = period_picker("switch_period")
    if picked:
        res = SC.supplier_switch(d, picked, sid, start, end)
        st.dataframe(pd.DataFrame(res["ingredients"]).rename(columns={
            "ingredient": "재료", "current_price": "현재 단가", "new_price": "새 단가", "usage": "기간 사용량", "impact": "영향"})
            .drop(columns=["ingredient_id"]), hide_index=True, use_container_width=True,
            column_config={c: st.column_config.NumberColumn(format="%,.2f") for c in ("현재 단가", "새 단가")}
            | {c: st.column_config.NumberColumn(format="%,.0f") for c in ("기간 사용량", "영향")})
        scenario_result("switch", res, f"{sups[sid]}로 {len(picked)}개 품목 전환")

with t4:
    scen = SC.list_scenarios(d)
    acts = [a for a in A.history(d, limit=200) if a["status"] == "실행"]
    with st.form("decision"):
        title = st.text_input("결정", placeholder="예: 김치찌개 500원 인상")
        why = st.text_area("근거·이유", placeholder="예: 양파 40% 인상 시 월 34만 원 부담")
        sids = st.multiselect("근거 시나리오", [s["scenario_id"] for s in scen],
                              format_func=lambda s: next(f"{x['title']} ({won(x['impact'])})" for x in scen if x["scenario_id"] == s))
        aids = st.multiselect("실행한 액션", [a["action_id"] for a in acts],
                              format_func=lambda a: next(f"{x['action_type']} · {x['message']}" for x in acts if x["action_id"] == a))
        who = st.text_input("결정자", "사장")
        if st.form_submit_button("결정 기록", type="primary"):
            if not title:
                st.error("결정 제목을 입력하세요.")
            else:
                st.success(f"기록됨: {D.record_decision(d, title, why, who, sids, aids)}")
                refresh()
    for dec in D.list_decisions(d):
        with st.expander(f"{dec['decided_at']:%m/%d %H:%M} · {dec['title']} · {dec['actor']}"):
            st.write(dec["rationale"] or "-")
            st.markdown("근거 시나리오: " + (", ".join(f"{s['title']}({won(s['impact'])})" for s in dec["scenarios"]) or "없음"))
            st.markdown("실행 액션: " + (", ".join(f"{a['action_type']} {a['status']} — {a['message']}" for a in dec["actions"]) or "없음"))

with t5:
    menus = {r["id"]: r["name"] for r in d.execute_query(
        "MATCH (m:MenuItem) RETURN m.menu_id AS id, m.name AS name ORDER BY id").records}
    c1, c2, c3 = st.columns(3)
    mid = c1.selectbox("메뉴", list(menus), format_func=menus.get)
    pivot = c2.date_input("기준일(결정한 날)", date(2026, 9, 1), min_value=date(2026, 7, 8), max_value=date(2026, 9, 30))
    days = c3.slider("비교 기간(일)", 7, 30, 14)
    r = D.sales_before_after(d, mid, pivot, days)
    a, b, c = st.columns(3)
    used = r["days"]
    a.metric(f"이전 {used}일 판매", f"{r['before']['qty']:,}개", help=f"매출 {won(r['before']['revenue'])}")
    b.metric(f"이후 {used}일 판매", f"{r['after']['qty']:,}개", help=f"매출 {won(r['after']['revenue'])}")
    if used < days:
        st.caption(f"데이터 기간(7/1~9/30) 밖은 비교할 수 없어 {days}일 → {used}일로 줄여 비교했습니다.")
    c.metric("판매량 변화", "-" if r["qty_change_rate"] is None else f"{r['qty_change_rate'] * 100:+.1f}%")
    st.caption("결정 효과 추적용: 결정한 날 앞뒤의 같은 길이 기간을 비교합니다. 계절·요일 구성 차이는 따로 고려하지 않습니다.")
