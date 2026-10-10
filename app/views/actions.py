"""Kinetic Action — 데이터를 바꾸는 행동. 검증을 통과하면 실행, 아니면 거부. 둘 다 기록된다."""
from datetime import date

import pandas as pd
import streamlit as st
from common import db_or_stop, refresh, setup, won

from kinetic import actions as A
from kinetic import functions as F

as_of = setup("액션", "KINETIC · ACTION",
              "가격 변경·품절·발주를 온톨로지에 직접 반영합니다. 규칙을 어기면 거부되고, 실행과 거부 모두 기록에 남습니다.")
d = db_or_stop()


def show(result):
    (st.success if result["status"] == "실행" else st.error)(f"{result['status']} — {result['message']}")
    refresh()


menus = {r["menu_id"]: r for r in F.menu_costs(d, as_of=date.today())}   # 검증과 같은 기준(오늘)
avail = {r["id"]: r["a"] for r in d.execute_query("MATCH (m:MenuItem) RETURN m.menu_id AS id, m.is_available AS a").records}
label = lambda mid: f"{menus[mid]['menu']} · {menus[mid]['price']:,}원 · 원가 {menus[mid]['cost']:,.0f}원"  # noqa: E731

t1, t2, t3, t4 = st.tabs(["가격 변경", "품절·판매 재개", "발주", "기록"])
with t1:
    st.caption(f"규칙: 새 가격은 원가보다 높아야 하고, 한 번에 ±{A.MAX_PRICE_STEP:.0%}까지만 바꿀 수 있습니다.")
    mid = st.selectbox("메뉴", list(menus), format_func=label, key="price_menu")   # 폼 밖: 바꾸면 기본 가격도 따라 바뀜
    with st.form("price"):
        new = st.number_input("새 가격(원)", min_value=0, step=500, value=int(menus[mid]["price"]), key=f"price_{mid}")
        who = st.text_input("실행자", "사장")
        why = st.text_input("사유", placeholder="예: 양파 단가 인상 반영")
        if st.form_submit_button("가격 변경 실행", type="primary"):
            show(A.change_price(d, mid, int(new), actor=who, reason=why))
with t2:
    with st.form("avail"):
        mid = st.selectbox("메뉴", list(menus), format_func=lambda m: f"{menus[m]['menu']} · {'판매 중' if avail[m] else '품절'}")
        to = st.radio("바꿀 상태", ["품절 처리", "판매 재개"], horizontal=True)
        who = st.text_input("실행자", "주방장")
        why = st.text_input("사유", placeholder="예: 돼지고기 소진")
        if st.form_submit_button("상태 변경 실행", type="primary"):
            show(A.set_availability(d, mid, to == "판매 재개", actor=who, reason=why))
with t3:
    st.caption(f"규칙: 그 업체가 공급하는 재료만, 수량은 0보다 크게, 1건 합계 {won(A.PO_BUDGET)} 이하.")
    sups = {r["id"]: r["name"] for r in d.execute_query(
        "MATCH (s:Supplier) RETURN s.supplier_id AS id, s.name AS name ORDER BY id").records}
    sid = st.selectbox("공급업체", list(sups), format_func=lambda s: f"{sups[s]} ({s})")
    offers = {r["iid"]: r for r in d.execute_query(
        """MATCH (:Supplier {supplier_id: $s})-[x:SUPPLIES]->(i:Ingredient)
           RETURN i.ingredient_id AS iid, i.name AS name, i.unit AS unit, x.unit_price AS price ORDER BY name""",
        s=sid).records}
    picked = st.multiselect("재료", list(offers), format_func=lambda i: f"{offers[i]['name']} ({offers[i]['price']:,.2f}원/{offers[i]['unit']})")
    lines = {}
    cols = st.columns(3)
    for k, iid in enumerate(picked):
        o = offers[iid]
        lines[iid] = cols[k % 3].number_input(f"{o['name']} 수량({o['unit']})", min_value=0.0, step=1000.0 if o["unit"] != "ea" else 10.0,
                                              key=f"qty_{sid}_{iid}")
    total = sum(q * offers[i]["price"] for i, q in lines.items())
    st.markdown(f"예상 합계 **{won(total)}**")
    who = st.text_input("실행자", "사장", key="po_who")
    why = st.text_input("사유", key="po_why", placeholder="예: 주말 대비")
    if st.button("발주 실행", type="primary", disabled=not lines):
        show(A.create_purchase_order(d, sid, lines, actor=who, reason=why))
with t4:
    hist = A.history(d, limit=100)
    if hist:
        st.dataframe(pd.DataFrame(hist)[["executed_at", "action_type", "status", "target", "message", "actor", "reason"]]
                     .rename(columns={"executed_at": "시각", "action_type": "액션", "status": "결과", "target": "대상",
                                      "message": "내용", "actor": "실행자", "reason": "사유"}),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("아직 실행한 액션이 없습니다.")
