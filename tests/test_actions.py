"""Kinetic Action 테스트 — 데이터를 바꾸는 행동. 실행·거부 모두 ActionLog로 남는지까지 본다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import tiny_store  # noqa: E402
from kinetic import actions as A  # noqa: E402
from semantic import pipeline as P  # noqa: E402
from semantic import schema as S  # noqa: E402


@pytest.fixture
def log(tmp_path):
    return tmp_path / "actions.jsonl"


def one(driver, cypher, **params):
    return driver.execute_query(cypher, **params).records[0]


def menu_price(driver, mid):
    return one(driver, "MATCH (m:MenuItem {menu_id: $m}) RETURN m.price AS p", m=mid)["p"]


# ── 가격 변경 ──
def test_가격변경_실행되면_메뉴가격이_바뀌고_기록이_남는다(fresh_kdb, log):
    r = A.change_price(fresh_kdb, "M-A", 9000, actor="사장", reason="양파값 인상 반영", log_path=log)
    assert r["status"] == "실행"
    assert menu_price(fresh_kdb, "M-A") == 9000
    rec = one(fresh_kdb, """MATCH (a:ActionLog {action_id: $id})-[:CHANGED_MENU]->(m:MenuItem)
                           RETURN a.action_type AS t, a.status AS s, a.actor AS who, a.params AS p, m.menu_id AS m""",
              id=r["action_id"])
    assert (rec["t"], rec["s"], rec["who"], rec["m"]) == ("가격변경", "실행", "사장", "M-A")
    assert json.loads(rec["p"]) == {"menu_id": "M-A", "old_price": 8000, "new_price": 9000}


def test_원가_이하_가격은_거부되고_가격은_그대로(fresh_kdb, log):
    # 김치찌개 현재 원가 = 양파 100g×4.2 + 돼지 100g×12 = 1,620원
    r = A.change_price(fresh_kdb, "M-A", 1500, actor="사장", reason="실수", log_path=log)
    assert r["status"] == "거부" and "원가" in r["message"]
    assert menu_price(fresh_kdb, "M-A") == 8000
    rec = one(fresh_kdb, "MATCH (a:ActionLog {action_id: $id}) RETURN a.status AS s", id=r["action_id"])
    assert rec["s"] == "거부"


def test_한번에_30퍼센트_넘게_바꾸면_거부(fresh_kdb, log):
    assert A.change_price(fresh_kdb, "M-A", 11000, actor="사장", reason="", log_path=log)["status"] == "거부"  # +37.5%
    assert A.change_price(fresh_kdb, "M-A", 10400, actor="사장", reason="", log_path=log)["status"] == "실행"  # +30%


# ── 품절 처리 ──
def test_품절처리와_같은상태_재요청_거부(fresh_kdb, log):
    assert A.set_availability(fresh_kdb, "M-A", False, actor="주방장", reason="돼지고기 소진", log_path=log)["status"] == "실행"
    avail = one(fresh_kdb, "MATCH (m:MenuItem {menu_id:'M-A'}) RETURN m.is_available AS a")["a"]
    assert avail is False
    again = A.set_availability(fresh_kdb, "M-A", False, actor="주방장", reason="", log_path=log)
    assert again["status"] == "거부"
    resume = A.set_availability(fresh_kdb, "M-A", True, actor="주방장", reason="입고", log_path=log)
    assert resume["status"] == "실행"
    types = [r["t"] for r in fresh_kdb.execute_query(
        "MATCH (a:ActionLog)-[:CHANGED_MENU]->(:MenuItem {menu_id:'M-A'}) RETURN a.action_type AS t ORDER BY a.executed_at").records]
    assert types == ["품절처리", "품절처리", "판매재개"]


# ── 발주 ──
def test_발주_실행되면_요청상태_발주가_공급단가로_생긴다(fresh_kdb, log):
    # 양파 5,000g×4.2 + 돼지 2,000g×12 = 45,000원
    r = A.create_purchase_order(fresh_kdb, "S1", {"ONION": 5000, "PORK": 2000}, actor="사장", reason="주말 대비",
                                log_path=log)
    assert r["status"] == "실행" and r["total"] == pytest.approx(45000)
    rec = one(fresh_kdb, """MATCH (a:ActionLog {action_id: $id})-[:CREATED_PO]->(po:PurchaseOrder)-[:ISSUED_TO]->(s:Supplier)
                           MATCH (po)-[x:ORDERS_ITEM]->(i:Ingredient)
                           RETURN po.status AS st, s.supplier_id AS sup, sum(x.qty * x.unit_price) AS total,
                                  count(i) AS n""", id=r["action_id"])
    assert (rec["st"], rec["sup"], rec["n"]) == ("요청", "S1", 2)
    assert rec["total"] == pytest.approx(45000)


@pytest.mark.parametrize("supplier, lines, why", [
    ("S2", {"TOFU": 100}, "공급하지 않는"),          # 대체정육은 두부를 안 판다
    ("S1", {"ONION": 0}, "수량"),
    ("S1", {"ONION": 1_000_000}, "예산"),            # 4,200,000원 > 3,000,000원
])
def test_발주_검증_실패는_거부(fresh_kdb, log, supplier, lines, why):
    r = A.create_purchase_order(fresh_kdb, supplier, lines, actor="사장", reason="", log_path=log)
    assert r["status"] == "거부" and why in r["message"]
    n = one(fresh_kdb, "MATCH (po:PurchaseOrder) RETURN count(po) AS n")["n"]
    assert n == 2                                       # 원래 있던 PO1·PO2만


# ── write-back: 재적재해도 액션이 되살아난다 ──
def test_재적재후_writeback_재생으로_액션이_복원된다(fresh_kdb, log):
    ok = A.change_price(fresh_kdb, "M-B", 9500, actor="사장", reason="", log_path=log)
    bad = A.change_price(fresh_kdb, "M-B", 100, actor="사장", reason="", log_path=log)
    po = A.create_purchase_order(fresh_kdb, "S2", {"PORK": 1000}, actor="사장", reason="", log_path=log)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3

    P.load(fresh_kdb, S.load(), tiny_store())          # 원천에서 다시 적재 → 액션 흔적이 사라짐
    assert menu_price(fresh_kdb, "M-B") == 9000
    A.replay(fresh_kdb, log_path=log)

    assert menu_price(fresh_kdb, "M-B") == 9500
    ids = {r["id"]: r["s"] for r in fresh_kdb.execute_query(
        "MATCH (a:ActionLog) RETURN a.action_id AS id, a.status AS s").records}
    assert ids == {ok["action_id"]: "실행", bad["action_id"]: "거부", po["action_id"]: "실행"}
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3   # 재생은 기록을 다시 쓰지 않는다


# ── 리뷰 반영 회귀 테스트 ──
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "9000", -1])
def test_숫자가_아니거나_NaN인_가격은_거부(fresh_kdb, log, bad):
    r = A.change_price(fresh_kdb, "M-A", bad, actor="사장", log_path=log)
    assert r["status"] == "거부" and menu_price(fresh_kdb, "M-A") == 8000


def test_발주수량_NaN과_품절상태_비불리언은_거부(fresh_kdb, log):
    assert A.create_purchase_order(fresh_kdb, "S1", {"ONION": float("nan")}, actor="사장", log_path=log)["status"] == "거부"
    assert A.set_availability(fresh_kdb, "M-A", "false", actor="사장", log_path=log)["status"] == "거부"


def test_AI제안은_허용된_인자만_실행된다(fresh_kdb, log, monkeypatch):
    monkeypatch.setattr(A.W, "DEFAULT_LOG", log)
    sneaky = {"action_type": "create_purchase_order", "reason": "",
              "args": {"supplier_id": "S1", "lines": {"ONION": 1_000_000}, "budget": 1e12}}
    with pytest.raises(ValueError, match="허용"):
        A.execute_proposal(fresh_kdb, sneaky, actor="사장")
    with pytest.raises(ValueError, match="알 수 없는 액션"):
        A.check_proposal({"action_type": "drop_database", "args": {}})
    for hidden in ("log_path", "_replayed"):
        with pytest.raises(ValueError):
            A.check_proposal({"action_type": "change_price", "args": {"menu_id": "M-A", "new_price": 8500, hidden: 1}})


def test_승인화면은_서버가_계산한_변경내용을_보여준다(fresh_kdb):
    assert A.describe_proposal(fresh_kdb, {"action_type": "change_price", "args": {"menu_id": "M-A", "new_price": 8500}}) \
        == "가격 변경 — 김치찌개: 8,000원 → 8500원"
    po = A.describe_proposal(fresh_kdb, {"action_type": "create_purchase_order",
                                         "args": {"supplier_id": "S1", "lines": {"ONION": 5000, "PORK": 2000}}})
    assert "테스트상회" in po and "양파 5,000g" in po and "예상 합계 45,000원" in po


def test_검증후_다른곳에서_바뀌면_쓰지않고_거부(fresh_kdb, log, monkeypatch):
    real = A._menu
    monkeypatch.setattr(A, "_menu", lambda d, m: {**dict(real(d, m)), "price": 7000})   # 검증 땐 옛 가격을 봤다고 가정
    r = A.change_price(fresh_kdb, "M-A", 8500, actor="사장", log_path=log)
    assert r["status"] == "거부" and "먼저 바뀌었" in r["message"]
    assert menu_price(fresh_kdb, "M-A") == 8000
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["status"] == "거부"


def test_재생결과가_원래와_다르면_경고하고_예산도_기록된다(fresh_kdb, log):
    r = A.create_purchase_order(fresh_kdb, "S1", {"ONION": 1_000_000}, actor="사장", log_path=log, budget=5_000_000)
    assert r["status"] == "실행"
    event = json.loads(log.read_text(encoding="utf-8"))
    assert event["budget"] == 5_000_000 and event["status"] == "실행"
    P.load(fresh_kdb, S.load(), tiny_store())
    A.replay(fresh_kdb, log_path=log)                      # 예산이 함께 재생되므로 여전히 실행
    assert one(fresh_kdb, "MATCH (a:ActionLog) RETURN a.status AS s")["s"] == "실행"
    event["status"] = "거부"                               # 기록을 일부러 어긋나게 → 경고
    log.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    P.load(fresh_kdb, S.load(), tiny_store())
    with pytest.warns(UserWarning, match="재생 결과 불일치"):
        A.replay(fresh_kdb, log_path=log)


def test_재생중_한건이_실패해도_나머지는_복원된다(fresh_kdb, log):
    ok = A.change_price(fresh_kdb, "M-B", 9500, actor="사장", log_path=log)
    lines = log.read_text(encoding="utf-8")
    broken = {"kind": "decision", "decision_id": "DEC-X", "executed_at": "2026-10-11T00:00:00", "title": "x",
              "rationale": "", "actor": "사장", "scenario_ids": ["SCN-NONE"], "action_ids": []}
    log.write_text(json.dumps(broken, ensure_ascii=False) + "\n" + lines, encoding="utf-8")
    P.load(fresh_kdb, S.load(), tiny_store())
    with pytest.warns(UserWarning, match="재생 실패"):
        A.replay(fresh_kdb, log_path=log)
    assert menu_price(fresh_kdb, "M-B") == 9500 and ok["status"] == "실행"
