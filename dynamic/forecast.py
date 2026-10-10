"""Dynamic 레이어 — 수요 예측과 재료 필요량.

예측 방식: '같은 요일 최근 N주 평균'(계절성 나이브 평균). 단순하지만 요일 패턴이 강한 음식점에서
설명하기 쉽고, 왜 그 숫자가 나왔는지 사장님이 바로 확인할 수 있다.
"""
from datetime import date, timedelta

from kinetic import functions as F


def forecast_menu_demand(driver, target_date: date, weeks: int = 4):
    """target_date 하루의 메뉴별 예상 판매량 = 최근 weeks번의 같은 요일 판매량 평균."""
    days = [target_date - timedelta(weeks=k) for k in range(1, weeks + 1)]
    records = driver.execute_query(
        """
        MATCH (o:Order)-[x:CONTAINS]->(m:MenuItem) WHERE date(o.ordered_at) IN $days
        RETURN m.menu_id AS menu_id, m.name AS menu, sum(x.qty) AS total
        ORDER BY total DESC
        """, days=days).records
    return [{"menu_id": r["menu_id"], "menu": r["menu"], "expected_qty": r["total"] / weeks,
             "basis_days": days} for r in records]


def ingredient_requirements(driver, target_date: date, as_of: date, weeks: int = 4):
    """예측 판매량 × 레시피 = 필요량. 기준일 재고와 비교해 부족분·추천 발주량·가장 싼 공급처를 낸다.

    추천 발주량 = max(0, 필요량 + 안전재고 − 현재고)  — 쓰고 나서도 안전재고가 남도록
    """
    demand = {r["menu_id"]: r["expected_qty"] for r in forecast_menu_demand(driver, target_date, weeks)}
    if not demand:
        return []
    need = {}
    for r in driver.execute_query(
            """MATCH (m:MenuItem)-[u:USES]->(i:Ingredient) WHERE m.menu_id IN $ids
               RETURN m.menu_id AS mid, i.ingredient_id AS iid, u.qty_per_serving AS q""",
            ids=list(demand)).records:
        need[r["iid"]] = need.get(r["iid"], 0) + demand[r["mid"]] * r["q"]
    stock = {r["ingredient_id"]: r for r in F.stock_status(driver, as_of)}
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
            "safety_stock": s["safety_stock"], "shortage": max(0, n - on_hand),
            "recommended_qty": max(0, n + s["safety_stock"] - on_hand),
            "best_supplier": offer["sid"] if offer else None, "best_price": offer["price"] if offer else None,
        })
    return sorted(rows, key=lambda r: -r["recommended_qty"])
