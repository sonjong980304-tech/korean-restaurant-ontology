"""Dynamic 레이어 — What-if 시나리오. 실제 데이터는 건드리지 않고 '만약 ~라면'을 계산한다.

save_scenario를 호출해야 Scenario 노드로 온톨로지에 남는다(결정의 근거로 연결하기 위해).
"""
import json
import uuid
from datetime import date, datetime, time

from kinetic import functions as F
from kinetic import writeback as W
from kinetic.writeback import DEFAULT_LOG


def _period(start, end):
    return {"start": datetime.combine(start, time()), "until": F._day_end(end)}


def _ingredient(driver, key):
    rows = driver.execute_query(
        "MATCH (i:Ingredient) WHERE i.ingredient_id = $k OR i.name = $k RETURN i.ingredient_id AS id, i.name AS name",
        k=key).records
    if not rows:
        raise ValueError(f"없는 재료: {key}")
    return rows[0]["id"], rows[0]["name"]


def _supplier(driver, key):
    rows = driver.execute_query(
        "MATCH (s:Supplier) WHERE s.supplier_id = $k OR s.name = $k RETURN s.supplier_id AS id", k=key).records
    if not rows:
        raise ValueError(f"없는 공급업체: {key}")
    return rows[0]["id"]


def price_shock(driver, ingredient, pct: float, start: date, end: date):
    """재료 단가가 pct만큼 오르면(음수면 내리면) 메뉴별 원가·마진이 어떻게 바뀌고, 기간 판매량 기준 얼마를 더 쓰나."""
    iid, name = _ingredient(driver, ingredient)
    price = F.latest_prices(driver, end)[iid]
    costs = {r["menu_id"]: r for r in F.menu_costs(driver, as_of=end)}
    records = driver.execute_query(
        """
        MATCH (m:MenuItem)-[u:USES]->(:Ingredient {ingredient_id: $iid})
        OPTIONAL MATCH (o:Order)-[x:CONTAINS]->(m) WHERE $start <= o.ordered_at < $until
        RETURN m.menu_id AS mid, u.qty_per_serving AS q, coalesce(sum(x.qty), 0) AS sold
        """, iid=iid, **_period(start, end)).records
    menus = []
    for r in records:
        c = costs[r["mid"]]
        delta = r["q"] * price * pct
        menus.append({"menu_id": r["mid"], "menu": c["menu"], "price": c["price"], "cost": c["cost"],
                      "new_cost": c["cost"] + delta, "margin": c["margin"], "new_margin": c["margin"] - delta,
                      "new_cost_ratio": (c["cost"] + delta) / c["price"], "sold": r["sold"],
                      "impact": r["sold"] * delta})
    menus.sort(key=lambda m: -m["impact"])
    return {"type": "원가충격",
            "params": {"ingredient_id": iid, "ingredient": name, "pct": pct, "price": price,
                       "new_price": price * (1 + pct), "start": start.isoformat(), "end": end.isoformat()},
            "menus": menus, "impact": sum(m["impact"] for m in menus)}


def supplier_switch(driver, ingredients, supplier_id: str, start: date, end: date):
    """재료들을 supplier_id(id 또는 이름)로 바꿔 샀다면: (새 공급단가 − 현재 매입단가) × 기간 사용량(소진+폐기)."""
    supplier_id = _supplier(driver, supplier_id)
    prices = F.latest_prices(driver, end)
    rows = []
    for key in ingredients:
        iid, name = _ingredient(driver, key)
        offer = driver.execute_query(
            "MATCH (:Supplier {supplier_id: $s})-[x:SUPPLIES]->(:Ingredient {ingredient_id: $i}) RETURN x.unit_price AS p",
            s=supplier_id, i=iid).records
        if not offer:
            raise ValueError(f"{supplier_id}는 {name}을(를) 공급하지 않습니다")
        usage = driver.execute_query(
            """MATCH (s:StockMovement)-[:MOVES]->(:Ingredient {ingredient_id: $i})
               WHERE s.movement_type IN ['소진', '폐기'] AND $start <= s.moved_at < $until
               RETURN -sum(s.qty) AS used""", i=iid, **_period(start, end)).records[0]["used"] or 0.0
        new = offer[0]["p"]
        rows.append({"ingredient_id": iid, "ingredient": name, "current_price": prices[iid], "new_price": new,
                     "usage": usage, "impact": usage * (new - prices[iid])})
    return {"type": "업체변경",
            "params": {"supplier_id": supplier_id, "ingredients": [r["ingredient_id"] for r in rows],
                       "start": start.isoformat(), "end": end.isoformat()},
            "ingredients": rows, "impact": sum(r["impact"] for r in rows)}


def save_scenario(driver, result, title, log_path=DEFAULT_LOG, _replayed=None):
    """시나리오 결과를 Scenario 노드로 저장한다."""
    scenario_id = _replayed["scenario_id"] if _replayed else f"SCN-{uuid.uuid4().hex[:8].upper()}"
    at = W.stamp(_replayed)
    driver.execute_query(
        """CREATE (:Scenario {scenario_id: $id, scenario_type: $type, title: $title, created_at: $at,
                              params: $params, result: $result, impact: $impact, _system: 'ONTOLOGY'})""",
        id=scenario_id, type=result["type"], title=title, at=at,
        params=json.dumps(result["params"], ensure_ascii=False, default=str),
        result=json.dumps({k: v for k, v in result.items() if k not in ("type", "params")}, ensure_ascii=False,
                          default=str),
        impact=float(result["impact"]))
    if not _replayed:
        W.append(log_path, {"kind": "scenario", "scenario_id": scenario_id, "executed_at": at.isoformat(),
                            "title": title, "result": json.loads(json.dumps(result, default=str))})
    return scenario_id


def replay_event(driver, event):
    save_scenario(driver, event["result"], event["title"], _replayed=event)


def list_scenarios(driver):
    records = driver.execute_query(
        """MATCH (s:Scenario) RETURN s.scenario_id AS scenario_id, s.title AS title, s.scenario_type AS type,
                  s.impact AS impact, s.created_at AS created_at ORDER BY s.created_at DESC""").records
    return [F._native(r) for r in records]
