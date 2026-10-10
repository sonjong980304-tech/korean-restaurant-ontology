"""Kinetic 레이어 — Action(데이터를 바꾸는 행동).

모든 액션은 같은 순서를 따른다.
  1) 검증: 규칙을 어기면 '거부'하고 데이터는 건드리지 않는다
  2) 실행: 한 트랜잭션 안에서 대상 객체를 바꾸고 ActionLog를 남긴다(거부도 ActionLog로 남김 — 감사 추적)
  3) 기록: write-back 파일에 한 줄 추가 → 재적재 후 replay로 복원
"""
import json
import uuid
from datetime import timedelta

from kinetic import functions as F
from kinetic import writeback as W
from kinetic.writeback import DEFAULT_LOG

MAX_PRICE_STEP = 0.30         # 한 번에 바꿀 수 있는 가격 폭(±30%)
PO_BUDGET = 3_000_000         # 발주 1건 예산 한도(원)


def _new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _log_action(tx, action_id, action_type, actor, reason, params, status, message, executed_at):
    tx.run(
        """
        CREATE (:ActionLog {action_id: $id, action_type: $type, actor: $actor, executed_at: $at, reason: $reason,
                            params: $params, status: $status, message: $message, _system: 'ONTOLOGY'})
        """,
        id=action_id, type=action_type, actor=actor, at=executed_at, reason=reason,
        params=json.dumps(params, ensure_ascii=False), status=status, message=message,
    )


def _finish(driver, log_path, replayed, event, write):
    """트랜잭션으로 쓰고, 새 실행이면 write-back 기록을 남긴다."""
    with driver.session() as session:
        session.execute_write(write)
    if not replayed:
        W.append(log_path, event)


def _event(action_type, args, actor, reason, action_id, executed_at):
    return {"kind": "action", "action_type": action_type, "args": args, "actor": actor, "reason": reason,
            "action_id": action_id, "executed_at": executed_at.isoformat()}


def _menu(driver, menu_id):
    rows = driver.execute_query(
        "MATCH (m:MenuItem {menu_id: $id}) RETURN m.name AS name, m.price AS price, m.is_available AS available",
        id=menu_id).records
    if not rows:
        raise ValueError(f"없는 메뉴: {menu_id}")
    return rows[0]


def change_price(driver, menu_id, new_price, actor, reason="", log_path=DEFAULT_LOG, _replayed=None):
    """메뉴 가격 변경. 원가 이하 또는 ±30% 초과 변경은 거부."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    menu = _menu(driver, menu_id)
    old = menu["price"]
    cost = next(r["cost"] for r in F.menu_costs(driver, as_of=at.date()) if r["menu_id"] == menu_id)
    if new_price <= cost:
        status, message = "거부", f"새 가격 {new_price:,}원이 현재 원가 {cost:,.0f}원 이하입니다"
    elif abs(new_price - old) / old > MAX_PRICE_STEP + 1e-9:
        status, message = "거부", f"한 번에 {MAX_PRICE_STEP:.0%}를 넘게 바꿀 수 없습니다({old:,}원 → {new_price:,}원)"
    else:
        status, message = "실행", f"{menu['name']} {old:,}원 → {new_price:,}원 (원가율 {cost / new_price:.1%})"
    params = {"menu_id": menu_id, "old_price": old, "new_price": new_price}

    def write(tx):
        if status == "실행":
            tx.run("MATCH (m:MenuItem {menu_id: $id}) SET m.price = $p", id=menu_id, p=new_price)
        _log_action(tx, action_id, "가격변경", actor, reason, params, status, message, at)
        tx.run("MATCH (a:ActionLog {action_id: $a}), (m:MenuItem {menu_id: $m}) CREATE (a)-[:CHANGED_MENU]->(m)",
               a=action_id, m=menu_id)

    _finish(driver, log_path, _replayed,
            _event("change_price", {"menu_id": menu_id, "new_price": new_price}, actor, reason, action_id, at), write)
    return {"action_id": action_id, "status": status, "message": message}


def set_availability(driver, menu_id, available, actor, reason="", log_path=DEFAULT_LOG, _replayed=None):
    """품절 처리(available=False) 또는 판매 재개(True). 이미 그 상태면 거부."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    menu = _menu(driver, menu_id)
    action_type = "판매재개" if available else "품절처리"
    if menu["available"] == available:
        status, message = "거부", f"{menu['name']}은(는) 이미 {'판매 중' if available else '품절'} 상태입니다"
    else:
        status, message = "실행", f"{menu['name']} {'판매 재개' if available else '품절 처리'}"

    def write(tx):
        if status == "실행":
            tx.run("MATCH (m:MenuItem {menu_id: $id}) SET m.is_available = $a", id=menu_id, a=available)
        _log_action(tx, action_id, action_type, actor, reason, {"menu_id": menu_id, "available": available},
                    status, message, at)
        tx.run("MATCH (a:ActionLog {action_id: $a}), (m:MenuItem {menu_id: $m}) CREATE (a)-[:CHANGED_MENU]->(m)",
               a=action_id, m=menu_id)

    _finish(driver, log_path, _replayed,
            _event("set_availability", {"menu_id": menu_id, "available": available}, actor, reason, action_id, at),
            write)
    return {"action_id": action_id, "status": status, "message": message}


def create_purchase_order(driver, supplier_id, lines, actor, reason="", log_path=DEFAULT_LOG,
                          budget=PO_BUDGET, _replayed=None):
    """발주 생성(요청 상태). lines = {재료 id: 기준단위 수량}. 공급단가는 SUPPLIES에서 가져온다."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    offers = {r["iid"]: r for r in driver.execute_query(
        """MATCH (s:Supplier {supplier_id: $s})-[x:SUPPLIES]->(i:Ingredient)
           RETURN i.ingredient_id AS iid, i.name AS name, x.unit_price AS price, s.lead_time_days AS lead""",
        s=supplier_id).records}
    priced, problems = [], []
    for iid, qty in lines.items():
        if qty <= 0:
            problems.append(f"{iid}: 수량은 0보다 커야 합니다")
        elif iid not in offers:
            problems.append(f"{iid}: {supplier_id}가 공급하지 않는 재료입니다")
        else:
            priced.append((iid, float(qty), offers[iid]["price"]))
    total = sum(q * p for _, q, p in priced)
    if not lines:
        problems.append("발주할 재료가 없습니다")
    if not problems and total > budget:
        problems.append(f"합계 {total:,.0f}원이 예산 {budget:,}원을 넘습니다(예산 초과)")
    status = "거부" if problems else "실행"
    po_id = f"PO-{action_id[4:]}"
    message = "; ".join(problems) if problems else f"{supplier_id} 발주 {po_id} 합계 {total:,.0f}원"
    params = {"supplier_id": supplier_id, "lines": lines, "total": total}

    def write(tx):
        _log_action(tx, action_id, "발주", actor, reason, params, status, message, at)
        if status != "실행":
            return
        lead = next(iter(offers.values()))["lead"]
        tx.run(
            """
            MATCH (s:Supplier {supplier_id: $s}), (a:ActionLog {action_id: $a})
            CREATE (po:PurchaseOrder {po_id: $po, ordered_at: $at, expected_at: $exp, status: '요청', _system: 'ONTOLOGY'})
            CREATE (po)-[:ISSUED_TO]->(s), (a)-[:CREATED_PO]->(po)
            WITH po
            UNWIND $lines AS l
            MATCH (i:Ingredient {ingredient_id: l.iid})
            CREATE (po)-[:ORDERS_ITEM {qty: l.qty, unit_price: l.price}]->(i)
            """,
            s=supplier_id, a=action_id, po=po_id, at=at, exp=(at + timedelta(days=lead)).date(),
            lines=[{"iid": i, "qty": q, "price": p} for i, q, p in priced],
        )

    _finish(driver, log_path, _replayed,
            _event("create_purchase_order", {"supplier_id": supplier_id, "lines": lines}, actor, reason, action_id, at),
            write)
    return {"action_id": action_id, "status": status, "message": message, "total": total,
            "po_id": po_id if status == "실행" else None}


ACTIONS = {"change_price": change_price, "set_availability": set_availability,
           "create_purchase_order": create_purchase_order}


def replay_event(driver, event):
    ACTIONS[event["action_type"]](driver, **event["args"], actor=event["actor"], reason=event["reason"],
                                  _replayed=event)


def replay(driver, log_path=DEFAULT_LOG):
    return W.replay(driver, log_path)


def history(driver, limit=50):
    """최근 액션 기록(실행·거부 모두)과 대상."""
    records = driver.execute_query(
        """
        MATCH (a:ActionLog)
        OPTIONAL MATCH (a)-[:CHANGED_MENU|CREATED_PO]->(t)
        RETURN a.action_id AS action_id, a.executed_at AS executed_at, a.action_type AS action_type,
               a.status AS status, a.actor AS actor, a.reason AS reason, a.message AS message,
               coalesce(t.name, t.po_id) AS target
        ORDER BY a.executed_at DESC LIMIT $limit
        """, limit=limit).records
    return [F._native(r) for r in records]
