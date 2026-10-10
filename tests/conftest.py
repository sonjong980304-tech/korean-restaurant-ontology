"""Kinetic 함수 테스트용 '손으로 계산 가능한 작은 가게'.

실제 3개월 데이터는 다시 적재될 때마다 바뀌므로, 테스트는 별도 Neo4j(NEO4J_TEST_URI, 포트 7689)에
이 작은 그래프를 넣고 정답을 손으로 계산해 비교한다. 정답 계산 근거는 각 테스트에 적어 둔다.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from semantic import pipeline as P  # noqa: E402
from semantic import schema as S  # noqa: E402

load_dotenv(ROOT / ".env")


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def d(s):
    return date.fromisoformat(s)


def tiny_store():
    g = P.Graph()
    # ── 기준정보 ──
    g.node("Zone", "Z1", {"zone_id": "Z1", "name": "홀", "seating": "입식"}, "POS")
    for tid, cap in (("T1", 4), ("T2", 2)):                      # 테이블 2개, 좌석 6석
        g.node("DiningTable", tid, {"table_id": tid, "capacity": cap}, "POS")
        g.link("IN_ZONE", tid, "Z1")
    g.node("MenuCategory", "C1", {"category_id": "C1", "name": "찌개·탕"}, "POS")
    g.node("MenuCategory", "C7", {"category_id": "C7", "name": "주류·음료"}, "POS")
    ingredients = {  # id: (이름, 단위, 안전재고)
        "ONION": ("양파", "g", 500), "PORK": ("돼지앞다리", "g", 5000),
        "TOFU": ("두부", "g", 500), "SOJU": ("소주", "ea", 10),
    }
    for iid, (name, unit, safety) in ingredients.items():
        g.node("Ingredient", iid, {"ingredient_id": iid, "name": name, "scm_item_code": iid, "mes_material_name": name,
                                   "unit": unit, "storage": "냉장", "shelf_life_days": 7, "safety_stock": float(safety)}, "SCM")
    menus = {  # id: (이름, 가격, 카테고리, 레시피)
        "M-A": ("김치찌개", 8000, "C1", {"ONION": 100, "PORK": 100}),
        "M-B": ("된장찌개", 9000, "C1", {"ONION": 50, "TOFU": 100}),
        "M-E": ("비빔밥", 10000, "C1", {"TOFU": 50}),
        "M-F": ("고등어구이", 7000, "C1", {"PORK": 400}),
        "M-D": ("소주", 5000, "C7", {"SOJU": 1}),
    }
    for mid, (name, price, cat, recipe) in menus.items():
        g.node("MenuItem", mid, {"menu_id": mid, "name": name, "price": price, "pos_menu_code": mid,
                                 "mes_recipe_code": "R" + mid, "is_available": True}, "POS+MES")
        g.link("IN_CATEGORY", mid, cat)
        for iid, qty in recipe.items():
            g.link("USES", mid, iid, {"qty_per_serving": float(qty)})

    # ── 매입 단가: 8/1 발주 → 9/1 양파만 3.0 → 4.2원/g 인상 ──
    g.node("Supplier", "S1", {"supplier_id": "S1", "name": "테스트상회", "category": "종합", "lead_time_days": 1,
                              "payment_terms_days": 30}, "SCM")
    g.node("Supplier", "S2", {"supplier_id": "S2", "name": "대체정육", "category": "육류", "lead_time_days": 2,
                              "payment_terms_days": 30}, "SCM")
    # 현재 공급 단가표: S1은 전 품목(9/1 이후 단가), S2는 돼지만 11원/g(1원 저렴)
    for iid, price in (("ONION", 4.2), ("PORK", 12.0), ("TOFU", 4.0), ("SOJU", 1300.0)):
        g.link("SUPPLIES", "S1", iid, {"unit_price": price, "min_order_qty": 1.0})
    g.link("SUPPLIES", "S2", "PORK", {"unit_price": 11.0, "min_order_qty": 1.0})
    for po, when, prices in (("PO1", "2026-08-01 22:00", {"ONION": 3.0, "PORK": 12.0, "TOFU": 4.0, "SOJU": 1300.0}),
                             ("PO2", "2026-09-01 22:00", {"ONION": 4.2})):
        g.node("PurchaseOrder", po, {"po_id": po, "ordered_at": dt(when), "expected_at": dt(when).date(),
                                     "status": "입고완료"}, "SCM")
        g.link("ISSUED_TO", po, "S1")
        for iid, price in prices.items():
            g.link("ORDERS_ITEM", po, iid, {"qty": 1000.0, "unit_price": price})

    # ── 메뉴 판매(메뉴 엔지니어링용): 9월 A10·B6·E1·F1·소주5, 8월 A3(기간 밖) ──
    for oid, when, lines in (("OM0", "2026-08-20 12:00", {"M-A": 3}),
                             ("OM1", "2026-09-03 12:00", {"M-A": 10, "M-B": 6}),
                             ("OM2", "2026-09-04 19:00", {"M-E": 1, "M-F": 1, "M-D": 5})):
        g.node("Order", oid, {"order_id": oid, "ordered_at": dt(when)}, "POS")
        for mid, qty in lines.items():
            g.link("CONTAINS", oid, mid, {"qty": qty, "unit_price": menus[mid][1]})

    # ── 재고: 8/2 입고, 9/27 김치찌개 2인분 조리(양파 210g·돼지 200g 소진), 9/28 반찬 양파 60g, 9/29 양파 30g 폐기 ──
    def move(mid, when, kind, iid, qty, ticket=None, sys_="MES"):
        g.node("StockMovement", mid, {"movement_id": mid, "moved_at": dt(when), "movement_type": kind, "qty": float(qty),
                                      "unit_cost": None}, sys_)
        g.link("MOVES", mid, iid)
        if ticket:
            g.link("CONSUMED_BY", mid, ticket)

    move("R1", "2026-08-02 09:00", "입고", "ONION", 10000, sys_="SCM")
    move("R2", "2026-08-02 09:00", "입고", "PORK", 5000, sys_="SCM")
    g.node("CookTicket", "K1", {"ticket_id": "K1", "station": "찌개", "qty": 2, "started_at": dt("2026-09-27 12:00"),
                                "completed_at": dt("2026-09-27 12:10")}, "MES")
    g.link("COOKS", "K1", "M-A")
    move("C1", "2026-09-27 12:00", "소진", "ONION", -210, ticket="K1")
    move("C2", "2026-09-27 12:00", "소진", "PORK", -200, ticket="K1")
    move("B1", "2026-09-28 10:30", "소진", "ONION", -60)                # 기본반찬 — 조리지시 없는 소진
    move("W1", "2026-09-29 22:30", "폐기", "ONION", -30)

    # ── 방문·대기·결제 ──
    def visit(vid, cust, arrive, seat, ptype, size, amount=None, wait=None):
        g.node("Visit", vid, {"visit_id": vid, "arrived_at": dt(arrive), "seated_at": dt(seat) if seat else None,
                              "left_at": dt(seat).replace(hour=dt(seat).hour + 1) if seat else None,
                              "party_type": ptype, "party_size": size, "has_child": False, "channel": "워크인"}, "POS")
        if cust:
            g.link("MADE_VISIT", cust, vid)
        if wait:
            g.node("WaitingRecord", "W" + vid, {"waiting_id": "W" + vid, "queued_at": dt(arrive),
                                                "called_at": dt(seat) if seat else None, "quoted_wait_min": 10,
                                                "outcome": wait}, "POS")
            g.link("WAITED", vid, "W" + vid)
        if seat:
            g.link("SEATED_AT", vid, "T1" if size > 2 else "T2")
            g.node("Payment", "P" + vid, {"payment_id": "P" + vid, "paid_at": dt(seat), "amount": amount,
                                          "method": "카드"}, "POS")
            g.link("PAID_BY", vid, "P" + vid)

    customers = {
        "C1": ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"],   # 단골(90일 4회, 최근 8일 전)
        "C2": ["2026-09-02", "2026-09-09", "2026-09-16", "2026-09-23"],   # VIP(단골 + 객단가 상위)
        "C3": ["2026-07-01", "2026-07-08", "2026-07-15", "2026-07-22"],   # 이탈위험(단골이었다가 70일 미방문)
        "C4": ["2026-09-10", "2026-09-20"],                               # 재방문
        "C5": ["2026-09-25", "2026-10-05"],                               # 신규(10/5는 기준일 이후)
        "C6": [],                                                         # 대기 이탈만 → 판정 없음
    }
    spend = {"C1": 30000, "C2": 80000, "C3": 20000, "C4": 25000, "C5": 15000}
    for cid, days in customers.items():
        g.node("Customer", cid, {"customer_id": cid, "gender": None, "age_group": None, "joined_at": d("2026-01-01")}, "POS")
        for i, day in enumerate(days):
            visit(f"V{cid}-{i}", cid, f"{day} 12:00", f"{day} 12:00", "친구", 2, spend[cid])
    visit("VC6-0", "C6", "2026-09-05 12:00", None, "친구", 2, wait="이탈")
    # 9/1 점심: C1(친구2) + 가족3(10분 대기 후 착석) + 가족4(대기 이탈) + 혼밥1(4분 대기)
    visit("VN1", None, "2026-09-01 12:10", "2026-09-01 12:20", "가족", 3, 40000, wait="착석")
    visit("VN2", None, "2026-09-01 12:15", None, "가족", 4, wait="이탈")
    visit("VN3", None, "2026-09-01 12:30", "2026-09-01 12:34", "혼밥", 1, 9000, wait="착석")
    # 9/1 저녁: 연인2
    visit("VN4", None, "2026-09-01 19:00", "2026-09-01 19:00", "연인", 2, 30000)

    # ── 회계: 8월 매출 200,000 / 식재료비 60,000, 9월 100,000 / 35,000 ──
    for code, name, kind in (("101", "현금", "자산"), ("146", "원재료", "자산"), ("401", "매출", "수익"),
                             ("451", "식재료비", "비용")):
        g.node("Account", code, {"account_code": code, "name": name, "account_type": kind}, "ERP")
    for je, day, debit, credit, amt in (("JS08", "2026-08-01", "101", "401", 200000),
                                        ("JC08", "2026-08-01", "451", "146", 60000),
                                        ("JS09", "2026-09-01", "101", "401", 100000),
                                        ("JC09", "2026-09-01", "451", "146", 35000)):
        g.node("JournalEntry", je, {"je_id": je, "posted_on": d(day), "description": je, "source_doc_no": None}, "ERP")
        g.link("DEBITS", je, debit, {"amount": amt})
        g.link("CREDITS", je, credit, {"amount": amt})
    return g


@pytest.fixture(scope="session")
def test_driver():
    uri = os.environ.get("NEO4J_TEST_URI", "")
    if not uri.endswith(":7689"):
        pytest.skip("테스트 전용 Neo4j(NEO4J_TEST_URI, 7689)가 설정되지 않음 — 실데이터 DB를 지우지 않기 위해 건너뜀")
    driver = GraphDatabase.driver(uri, auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                                  notifications_min_severity="OFF")
    try:
        driver.verify_connectivity()
    except Exception:
        pytest.skip("테스트 전용 Neo4j가 꺼져 있음")
    yield driver
    driver.close()


@pytest.fixture(scope="module")
def kdb(test_driver):
    """읽기 전용 테스트용: 모듈마다 작은 가게를 새로 적재한다(다른 모듈의 액션이 섞이지 않게)."""
    P.load(test_driver, S.load(), tiny_store())
    return test_driver


@pytest.fixture
def fresh_kdb(test_driver):
    """데이터를 바꾸는 테스트용: 테스트마다 새로 적재한다."""
    P.load(test_driver, S.load(), tiny_store())
    return test_driver
