"""Dynamic 레이어 — 수요 예측과 재료 필요량.

예측 방식: '같은 요일 최근 N주 평균'(계절성 나이브 평균). 단순하지만 요일 패턴이 강한 음식점에서
설명하기 쉽고, 왜 그 숫자가 나왔는지 사장님이 바로 확인할 수 있다.
"""
from datetime import date, timedelta

from kinetic import functions as F


def forecast_menu_demand(driver, target_date: date, weeks: int = 4):
    """target_date 하루의 메뉴별 예상 판매량 = 최근 weeks번의 같은 요일 판매량 평균.
    데이터 시작일(F.DATA_START) 이전·데이터 끝 이후 날짜는 '판매 0'이 아니라 '모름'이라 평균에서 뺀다."""
    days = [target_date - timedelta(weeks=k) for k in range(1, weeks + 1)]
    days = [x for x in days if F.DATA_START <= x <= F.DATA_END]
    if not days:
        return []
    records = driver.execute_query(
        """
        MATCH (o:Order)-[x:CONTAINS]->(m:MenuItem) WHERE date(o.ordered_at) IN $days
        RETURN m.menu_id AS menu_id, m.name AS menu, sum(x.qty) AS total
        ORDER BY total DESC
        """, days=days).records
    return [{"menu_id": r["menu_id"], "menu": r["menu"], "expected_qty": r["total"] / len(days),
             "basis_days": days} for r in records]


MAX_HORIZON_DAYS = 14


def ingredient_requirements(driver, target_date: date, as_of: date, weeks: int = 4):
    """기준일 다음날부터 target_date까지의 예측 판매량 × 레시피 = 필요량. 재고·입고 예정과 비교해
    부족분·추천 발주량·가장 싼 공급처를 낸다.

    추천 발주량 = max(0, 필요량 + 안전재고 − 현재고 − 입고 예정(기준일까지 발주했고 아직 입고 기록이 없는 발주))
    기준일이 target_date 이후면 target_date 하루치만 본다.
    """
    horizon = [as_of + timedelta(days=k) for k in range(1, (target_date - as_of).days + 1)][:MAX_HORIZON_DAYS] \
        or [target_date]
    demand = {}
    for day in horizon:
        for r in forecast_menu_demand(driver, day, weeks):
            demand[r["menu_id"]] = demand.get(r["menu_id"], 0) + r["expected_qty"]
    if not demand:
        return []
    need = {}
    for r in driver.execute_query(
            """MATCH (m:MenuItem)-[u:USES]->(i:Ingredient) WHERE m.menu_id IN $ids
               RETURN m.menu_id AS mid, i.ingredient_id AS iid, u.qty_per_serving AS q""",
            ids=list(demand)).records:
        need[r["iid"]] = need.get(r["iid"], 0) + demand[r["mid"]] * r["q"]
    stock = {r["ingredient_id"]: r for r in F.stock_status(driver, as_of)}
    # 입고 예정 = 기준일까지 발주했고(취소 제외) 기준일까지 입고되지 않은 것. 발주의 status는 데이터 마지막 날 기준이라
    # 시점 판단에 쓰지 않고, 날짜(ordered_at)와 입고 기록(RECEIVED_FOR의 moved_at)으로 판단한다.
    # 입고 기록이 아예 없는 '입고완료' 발주는 원천에서 입고 연결이 빠진 것으로 보고 이미 받은 것으로 친다.
    # 기준일이 데이터 마지막 날이면 '지금' 계획으로 보고, 앱에서 낸(ONTOLOGY) 아직 요청 상태인 발주도 입고 예정에 넣는다
    # (그래야 추천 발주를 두 번 내지 않는다). 과거 기준일에는 날짜로만 판단한다.
    incoming = {r["iid"]: r["qty"] for r in driver.execute_query(
        """MATCH (po:PurchaseOrder)-[x:ORDERS_ITEM]->(i:Ingredient)
           WHERE (po.ordered_at < $until OR ($now AND po._system = 'ONTOLOGY' AND po.status = '요청'))
             AND po.status <> '취소'
             AND NOT EXISTS { MATCH (po)<-[:RECEIVED_FOR]-(r:StockMovement) WHERE r.moved_at < $until }
             AND (po.status <> '입고완료' OR EXISTS { MATCH (po)<-[:RECEIVED_FOR]-() })
           RETURN i.ingredient_id AS iid, sum(x.qty) AS qty""", until=F._day_end(as_of), now=as_of >= F.DATA_END).records}
    offers = {r["iid"]: r for r in driver.execute_query(
        """MATCH (s:Supplier)-[x:SUPPLIES]->(i:Ingredient) WHERE i.ingredient_id IN $ids
           WITH i, s, x ORDER BY x.unit_price
           WITH i, collect({sid: s.supplier_id, price: x.unit_price})[0] AS best
           RETURN i.ingredient_id AS iid, best.sid AS sid, best.price AS price""",
        ids=list(need)).records}
    rows = []
    for iid, n in need.items():
        s = stock[iid]
        on_hand = max(s["stock"], 0)
        offer = offers.get(iid)
        rows.append({
            "ingredient_id": iid, "ingredient": s["ingredient"], "unit": s["unit"], "need": n, "stock": on_hand,
            "horizon_days": len(horizon),
            "safety_stock": s["safety_stock"], "incoming": incoming.get(iid, 0.0),
            "shortage": max(0, n - on_hand - incoming.get(iid, 0.0)),
            "recommended_qty": max(0, n + s["safety_stock"] - on_hand - incoming.get(iid, 0.0)),
            "best_supplier": offer["sid"] if offer else None, "best_price": offer["price"] if offer else None,
        })
    return sorted(rows, key=lambda r: -r["recommended_qty"])
