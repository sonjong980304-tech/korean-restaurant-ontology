"""Kinetic 레이어 — Action(데이터를 바꾸는 행동).

모든 액션은 같은 순서를 따른다.
  1) 검증: 규칙을 어기면 '거부'하고 데이터는 건드리지 않는다
  2) 실행: 한 트랜잭션 안에서 대상 객체를 바꾸고 ActionLog를 남긴다(거부도 ActionLog로 남김 — 감사 추적)
  3) 기록: write-back 파일에 한 줄 추가 → 재적재 후 replay로 복원
"""
import json
import math
import uuid
import warnings
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


def _finish(driver, log_path, replayed, make_event, write):
    """트랜잭션으로 쓰고(검증 결과가 그 안에서 바뀔 수 있음), 새 실행이면 확정된 결과로 write-back 기록을 남긴다."""
    with driver.session() as session:
        session.execute_write(write)
    if not replayed:
        W.append(log_path, make_event())


def _event(action_type, args, actor, reason, action_id, executed_at, status, **extra):
    """write-back 한 줄. 원래 판정(status)도 남겨 재생 때 결과가 달라지면 알아챈다."""
    return {"kind": "action", "action_type": action_type, "args": args, "actor": actor, "reason": reason,
            "action_id": action_id, "executed_at": executed_at.isoformat(), "status": status, **extra}


def _positive_number(x):
    """bool·문자열·NaN·무한대·0 이하를 걸러낸다(NaN은 모든 비교가 False라 검증을 통과해 버린다)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _menu(driver, menu_id):
    rows = driver.execute_query(
        "MATCH (m:MenuItem {menu_id: $id}) RETURN m.name AS name, m.price AS price, m.is_available AS available",
        id=menu_id).records
    if not rows:
        raise ValueError(f"없는 메뉴: {menu_id}")
    return rows[0]


def change_price(driver, menu_id, new_price, actor, reason="", *, log_path=DEFAULT_LOG, _replayed=None):
    """메뉴 가격 변경. 원가 이하 또는 ±30% 초과 변경은 거부."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    menu = _menu(driver, menu_id)
    old = menu["price"]
    cost = next(r["cost"] for r in F.menu_costs(driver, as_of=at.date()) if r["menu_id"] == menu_id)
    if not _positive_number(new_price):
        status, message = "거부", f"가격은 0보다 큰 숫자여야 합니다(입력: {new_price!r})"
    elif new_price <= cost:
        status, message = "거부", f"새 가격 {new_price:,}원이 현재 원가 {cost:,.0f}원 이하입니다"
    elif abs(new_price - old) / old > MAX_PRICE_STEP + 1e-9:
        status, message = "거부", f"한 번에 {MAX_PRICE_STEP:.0%}를 넘게 바꿀 수 없습니다({old:,}원 → {new_price:,}원)"
    else:
        status, message = "실행", f"{menu['name']} {old:,}원 → {new_price:,}원 (원가율 {cost / new_price:.1%})"
    params = {"menu_id": menu_id, "old_price": old, "new_price": new_price}

    final = {}

    def write(tx):                                   # 드라이버가 재시도해도 매번 검증 결과에서 다시 시작한다
        st, msg = status, message
        if st == "실행" and not tx.run(               # 검증한 뒤 다른 곳에서 가격이 바뀌었으면 쓰지 않는다
                "MATCH (m:MenuItem {menu_id: $id}) WHERE m.price = $old SET m.price = $p RETURN m",
                id=menu_id, old=old, p=new_price).single():
            st, msg = "거부", "검증 후 다른 곳에서 가격이 먼저 바뀌었습니다. 다시 시도하세요"
        _log_action(tx, action_id, "가격변경", actor, reason, params, st, msg, at)
        tx.run("MATCH (a:ActionLog {action_id: $a}), (m:MenuItem {menu_id: $m}) CREATE (a)-[:CHANGED_MENU]->(m)",
               a=action_id, m=menu_id)
        final.update(status=st, message=msg)

    _finish(driver, log_path, _replayed,
            lambda: _event("change_price", {"menu_id": menu_id, "new_price": new_price}, actor, reason, action_id, at,
                           final["status"]), write)
    return {"action_id": action_id, **final}


def set_availability(driver, menu_id, available, actor, reason="", *, log_path=DEFAULT_LOG, _replayed=None):
    """품절 처리(available=False) 또는 판매 재개(True). 이미 그 상태면 거부."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    menu = _menu(driver, menu_id)
    action_type = "판매재개" if available else "품절처리"
    if not isinstance(available, bool):
        status, message = "거부", f"상태는 참/거짓이어야 합니다(입력: {available!r})"
    elif menu["available"] == available:
        status, message = "거부", f"{menu['name']}은(는) 이미 {'판매 중' if available else '품절'} 상태입니다"
    else:
        status, message = "실행", f"{menu['name']} {'판매 재개' if available else '품절 처리'}"

    final = {}

    def write(tx):
        st, msg = status, message
        if st == "실행" and not tx.run(
                "MATCH (m:MenuItem {menu_id: $id}) WHERE m.is_available <> $a SET m.is_available = $a RETURN m",
                id=menu_id, a=available).single():
            st, msg = "거부", "검증 후 다른 곳에서 상태가 먼저 바뀌었습니다"
        _log_action(tx, action_id, action_type, actor, reason, {"menu_id": menu_id, "available": available},
                    st, msg, at)
        tx.run("MATCH (a:ActionLog {action_id: $a}), (m:MenuItem {menu_id: $m}) CREATE (a)-[:CHANGED_MENU]->(m)",
               a=action_id, m=menu_id)
        final.update(status=st, message=msg)

    _finish(driver, log_path, _replayed,
            lambda: _event("set_availability", {"menu_id": menu_id, "available": available}, actor, reason, action_id, at,
                           final["status"]), write)
    return {"action_id": action_id, **final}


def create_purchase_order(driver, supplier_id, lines, actor, reason="", *, log_path=DEFAULT_LOG,
                          budget=PO_BUDGET, _replayed=None):
    """발주 생성(요청 상태). lines = {재료 id: 기준단위 수량}. 공급단가는 SUPPLIES에서 가져온다."""
    action_id = _replayed["action_id"] if _replayed else _new_id("ACT")
    at = W.stamp(_replayed)
    offers = {r["iid"]: r for r in driver.execute_query(
        """MATCH (s:Supplier {supplier_id: $s})-[x:SUPPLIES]->(i:Ingredient)
           RETURN i.ingredient_id AS iid, i.name AS name, x.unit_price AS price, s.lead_time_days AS lead""",
        s=supplier_id).records}
    priced, problems = [], []
    if not isinstance(lines, dict):
        lines, problems = {}, [f"발주 품목 형식이 잘못됐습니다(입력: {lines!r})"]
    for iid, qty in lines.items():
        if not _positive_number(qty):
            problems.append(f"{iid}: 수량은 0보다 큰 숫자여야 합니다")
        elif iid not in offers:
            problems.append(f"{iid}: {supplier_id}가 공급하지 않는 재료입니다")
        else:
            priced.append((iid, float(qty), offers[iid]["price"]))
    total = sum(q * p for _, q, p in priced)
    if not lines and not problems:
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
            lambda: _event("create_purchase_order", {"supplier_id": supplier_id, "lines": lines}, actor, reason, action_id, at,
                   status, budget=budget), write)
    return {"action_id": action_id, "status": status, "message": message, "total": total,
            "po_id": po_id if status == "실행" else None}


ACTIONS = {"change_price": change_price, "set_availability": set_availability,
           "create_purchase_order": create_purchase_order}


# AI 제안(propose_action)에서 받아도 되는 인자 — 예산·기록 위치·재생용 내부 값은 제안으로 바꿀 수 없다
PROPOSAL_ARGS = {"change_price": {"menu_id", "new_price"}, "set_availability": {"menu_id", "available"},
                 "create_purchase_order": {"supplier_id", "lines"}}


def check_proposal(proposal):
    """제안의 액션 종류와 인자를 허용 목록과 대조한다. 어긋나면 ValueError."""
    kind, args = proposal.get("action_type"), proposal.get("args")
    if kind not in PROPOSAL_ARGS:
        raise ValueError(f"알 수 없는 액션: {kind}")
    if not isinstance(args, dict) or set(args) != PROPOSAL_ARGS[kind]:
        raise ValueError(f"{kind}의 인자는 {sorted(PROPOSAL_ARGS[kind])}만 허용됩니다(받은 값: {args!r})")
    return kind, args


def describe_proposal(driver, proposal):
    """승인 전에 '실제로 무엇이 바뀌는지'를 서버에서 계산해 사람이 읽는 문장으로 보여준다."""
    kind, args = check_proposal(proposal)
    if kind == "create_purchase_order":
        sup = driver.execute_query("MATCH (s:Supplier {supplier_id: $s}) RETURN s.name AS n", s=args["supplier_id"]).records
        offers = {r["iid"]: r for r in driver.execute_query(
            """MATCH (:Supplier {supplier_id: $s})-[x:SUPPLIES]->(i:Ingredient)
               RETURN i.ingredient_id AS iid, i.name AS name, i.unit AS unit, x.unit_price AS price""",
            s=args["supplier_id"]).records}
        items, total = [], 0.0
        for iid, qty in (args["lines"] or {}).items() if isinstance(args["lines"], dict) else []:
            o = offers.get(iid)
            if o and _positive_number(qty):
                items.append(f"{o['name']} {qty:,.0f}{o['unit']}")
                total += qty * o["price"]
            else:
                items.append(f"{iid} {qty!r}(공급 안 함 또는 잘못된 수량)")
        name = sup[0]["n"] if sup else f"없는 업체 {args['supplier_id']}"
        return f"발주 — {name}: {', '.join(items) or '품목 없음'} · 예상 합계 {total:,.0f}원(예산 {PO_BUDGET:,}원)"
    menu = _menu(driver, args["menu_id"])
    if kind == "change_price":
        return f"가격 변경 — {menu['name']}: {menu['price']:,}원 → {args['new_price']!r}원"
    now = "판매 중" if menu["available"] else "품절"
    to = "판매 재개" if args["available"] is True else "품절 처리" if args["available"] is False else repr(args["available"])
    return f"품절·판매 재개 — {menu['name']}: 지금 {now} → {to}"


def execute_proposal(driver, proposal, actor):
    """사람이 승인한 AI 제안을 실행한다. 허용된 인자만 넘기므로 예산·기록 위치 같은 내부 옵션은 바꿀 수 없다."""
    kind, args = check_proposal(proposal)
    return ACTIONS[kind](driver, **args, actor=actor, reason=proposal.get("reason", ""))


def replay_event(driver, event):
    extra = {"budget": event["budget"]} if "budget" in event else {}
    result = ACTIONS[event["action_type"]](driver, **event["args"], actor=event["actor"], reason=event["reason"],
                                           _replayed=event, **extra)
    if "status" in event and result["status"] != event["status"]:
        warnings.warn(f"재생 결과 불일치: {event['action_id']} 원래 {event['status']} → 재생 {result['status']} "
                      f"({result['message']}) — 원천 데이터나 규칙이 바뀌었을 수 있습니다", stacklevel=2)
    return result


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
