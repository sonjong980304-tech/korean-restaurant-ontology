"""Dynamic 레이어 테스트 — 예측·시나리오·의사결정. 정답은 conftest.tiny_store() 손계산."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import tiny_store  # noqa: E402
from dynamic import decision as D  # noqa: E402
from dynamic import forecast as FC  # noqa: E402
from dynamic import scenario as SC  # noqa: E402
from kinetic import actions as A  # noqa: E402
from semantic import pipeline as P  # noqa: E402
from semantic import schema as S  # noqa: E402

SEP = (date(2026, 9, 1), date(2026, 9, 30))


def by(rows, key):
    return {r[key]: r for r in rows}


# ── 예측 ──
def test_같은요일_최근4주_평균으로_메뉴수요를_예측한다(kdb):
    """9/10(목) 예측: 최근 4번의 목요일 9/3·8/27·8/20·8/13 판매 → 김치찌개 (10+3)/4, 된장찌개 6/4"""
    rows = by(FC.forecast_menu_demand(kdb, date(2026, 9, 10), weeks=4), "menu_id")
    assert rows["M-A"]["expected_qty"] == pytest.approx(3.25)
    assert rows["M-B"]["expected_qty"] == pytest.approx(1.5)
    assert "M-E" not in rows                      # 목요일엔 안 팔림(금요일 9/4에만 판매)
    friday = by(FC.forecast_menu_demand(kdb, date(2026, 9, 11), weeks=4), "menu_id")
    assert friday["M-D"]["expected_qty"] == pytest.approx(5 / 4)


def test_예측수요로_재료_부족분과_추천발주량을_낸다(kdb):
    """필요량: 양파 3.25×100 + 1.5×50 = 400g, 돼지 325g, 두부 150g. 9/30 재고: 양파 9,700 / 돼지 4,800 / 두부 0
    추천발주 = max(0, 필요량 + 안전재고 − 재고): 두부 150+500−0 = 650, 돼지 325+5,000−4,800 = 525, 양파 0
    추천업체 = 가장 싼 공급처(돼지는 S2 11원)"""
    rows = by(FC.ingredient_requirements(kdb, date(2026, 9, 10), as_of=date(2026, 9, 30), weeks=4), "ingredient_id")
    assert rows["ONION"]["need"] == pytest.approx(400) and rows["ONION"]["recommended_qty"] == 0
    assert rows["TOFU"]["shortage"] == pytest.approx(150)
    assert rows["TOFU"]["recommended_qty"] == pytest.approx(650)
    assert rows["PORK"]["recommended_qty"] == pytest.approx(525)
    assert rows["PORK"]["best_supplier"] == "S2" and rows["PORK"]["best_price"] == pytest.approx(11.0)


# ── 시나리오 ──
def test_원가충격_양파40퍼센트(kdb):
    """양파 4.2 → 5.88원/g(+1.68). 김치찌개 +168원×9월 10그릇 = 1,680, 된장찌개 +84원×6 = 504 → 합계 2,184원"""
    result = SC.price_shock(kdb, "ONION", 0.40, *SEP)
    rows = by(result["menus"], "menu")
    assert rows["김치찌개"]["new_cost"] == pytest.approx(1620 + 168)
    assert rows["김치찌개"]["impact"] == pytest.approx(1680)
    assert rows["된장찌개"]["impact"] == pytest.approx(504)
    assert set(rows) == {"김치찌개", "된장찌개"}
    assert result["impact"] == pytest.approx(2184)


def test_업체변경_돼지를_S2로(kdb):
    """현재 돼지 매입단가 12원(최근 발주) → S2 11원. 9월 사용 200g → 200원 절감(영향 −200)"""
    result = SC.supplier_switch(kdb, ["PORK"], "S2", *SEP)
    row = result["ingredients"][0]
    assert (row["current_price"], row["new_price"], row["usage"]) == (12.0, 11.0, 200.0)
    assert result["impact"] == pytest.approx(-200)


def test_공급하지_않는_재료로_업체변경하면_오류(kdb):
    with pytest.raises(ValueError, match="공급하지 않"):
        SC.supplier_switch(kdb, ["TOFU"], "S2", *SEP)


# ── 시나리오 저장 → 결정 → 액션, 그리고 재적재 후 복원 ──
def test_시나리오_결정_액션이_한줄로_이어지고_재적재후에도_남는다(fresh_kdb, tmp_path):
    log = tmp_path / "events.jsonl"
    shock = SC.price_shock(fresh_kdb, "ONION", 0.40, *SEP)
    sid = SC.save_scenario(fresh_kdb, shock, title="양파 40% 인상", log_path=log)
    act = A.change_price(fresh_kdb, "M-A", 8500, actor="사장", reason="양파 인상 대응", log_path=log)
    did = D.record_decision(fresh_kdb, "김치찌개 500원 인상", rationale="양파 40% 인상 시 월 2,184원 부담",
                            actor="사장", scenario_ids=[sid], action_ids=[act["action_id"]], log_path=log)

    def check():
        t = D.decision_trace(fresh_kdb, did)
        assert t["title"] == "김치찌개 500원 인상"
        assert [s["scenario_id"] for s in t["scenarios"]] == [sid]
        assert t["scenarios"][0]["impact"] == pytest.approx(2184)
        assert [(a["action_id"], a["status"]) for a in t["actions"]] == [(act["action_id"], "실행")]

    check()
    P.load(fresh_kdb, S.load(), tiny_store())
    assert A.replay(fresh_kdb, log_path=log) == 3
    check()


def test_결정_전후_판매비교(kdb):
    """김치찌개, 기준 9/1, 30일: 이전(8/2~8/31) 3그릇 24,000원 → 이후(9/1~9/30) 10그릇 80,000원"""
    r = D.sales_before_after(kdb, "M-A", date(2026, 9, 1), days=30)
    assert (r["before"]["qty"], r["before"]["revenue"]) == (3, 24000)
    assert (r["after"]["qty"], r["after"]["revenue"]) == (10, 80000)
    assert r["qty_change_rate"] == pytest.approx(10 / 3 - 1)
