"""Dynamic 레이어 — 의사결정 기록과 효과 추적.

결정 하나에 '무엇을 근거로(Scenario) 무엇을 했나(ActionLog)'를 묶어 두면, 나중에
'그 결정이 효과가 있었나?'를 그래프에서 바로 따라갈 수 있다.
"""
import uuid
from datetime import date, datetime, time, timedelta

from kinetic import functions as F
from kinetic import writeback as W
from kinetic.writeback import DEFAULT_LOG


def record_decision(driver, title, rationale, actor, scenario_ids=(), action_ids=(), log_path=DEFAULT_LOG,
                    _replayed=None):
    decision_id = _replayed["decision_id"] if _replayed else f"DEC-{uuid.uuid4().hex[:8].upper()}"
    at = W.stamp(_replayed)
    driver.execute_query(
        """
        CREATE (d:Decision {decision_id: $id, title: $title, rationale: $why, actor: $actor, decided_at: $at,
                            _system: 'ONTOLOGY'})
        WITH d
        OPTIONAL MATCH (s:Scenario) WHERE s.scenario_id IN $sids
        FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | CREATE (d)-[:BASED_ON]->(s))
        WITH DISTINCT d
        OPTIONAL MATCH (a:ActionLog) WHERE a.action_id IN $aids
        FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | CREATE (d)-[:EXECUTED]->(a))
        """,
        id=decision_id, title=title, why=rationale, actor=actor, at=at, sids=list(scenario_ids), aids=list(action_ids))
    if not _replayed:
        W.append(log_path, {"kind": "decision", "decision_id": decision_id, "executed_at": at.isoformat(),
                            "title": title, "rationale": rationale, "actor": actor,
                            "scenario_ids": list(scenario_ids), "action_ids": list(action_ids)})
    return decision_id


def replay_event(driver, event):
    record_decision(driver, event["title"], event["rationale"], event["actor"], event["scenario_ids"],
                    event["action_ids"], _replayed=event)


def decision_trace(driver, decision_id):
    """결정 → 근거 시나리오 → 실행한 액션을 한 번에."""
    rec = driver.execute_query(
        """
        MATCH (d:Decision {decision_id: $id})
        OPTIONAL MATCH (d)-[:BASED_ON]->(s:Scenario)
        WITH d, collect(s {.scenario_id, .title, .impact, .scenario_type}) AS scenarios
        OPTIONAL MATCH (d)-[:EXECUTED]->(a:ActionLog)
        WITH d, scenarios, a ORDER BY a.executed_at
        RETURN d {.decision_id, .title, .rationale, .actor, .decided_at} AS d, scenarios,
               collect(a {.action_id, .action_type, .status, .message}) AS actions
        """, id=decision_id).records[0]
    return {**F._native(rec["d"]), "scenarios": rec["scenarios"], "actions": rec["actions"]}


def list_decisions(driver):
    records = driver.execute_query(
        "MATCH (d:Decision) RETURN d.decision_id AS decision_id ORDER BY d.decided_at DESC").records
    return [decision_trace(driver, r["decision_id"]) for r in records]


def sales_before_after(driver, menu_id, pivot: date, days: int = 14):
    """기준일 전 days일 [pivot−days, pivot)과 후 days일 [pivot, pivot+days)의 판매량·매출 비교."""
    def window(a, b):
        r = driver.execute_query(
            """MATCH (o:Order)-[x:CONTAINS]->(:MenuItem {menu_id: $m}) WHERE $a <= o.ordered_at < $b
               RETURN coalesce(sum(x.qty), 0) AS qty, coalesce(sum(x.qty * x.unit_price), 0) AS revenue""",
            m=menu_id, a=datetime.combine(a, time()), b=datetime.combine(b, time())).records[0]
        return {"qty": r["qty"], "revenue": r["revenue"]}

    before = window(pivot - timedelta(days=days), pivot)
    after = window(pivot, pivot + timedelta(days=days))
    rate = after["qty"] / before["qty"] - 1 if before["qty"] else None
    return {"menu_id": menu_id, "pivot": pivot, "days": days, "before": before, "after": after,
            "qty_change_rate": rate}
