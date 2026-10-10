"""Kinetic Function 테스트 — 정답은 conftest.tiny_store()를 손으로 계산한 값."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kinetic import functions as F  # noqa: E402

SEP = (date(2026, 9, 1), date(2026, 9, 30))


def by(rows, key):
    return {r[key]: r for r in rows}


# ── 사이클 1: 메뉴 원가·마진 ──
def test_메뉴원가는_레시피_사용량_곱하기_기준일_최근_매입단가(kdb):
    rows = by(F.menu_costs(kdb, as_of=date(2026, 8, 15)), "menu")
    # 김치찌개 = 양파 100g×3.0 + 돼지 100g×12.0 = 1,500원
    assert rows["김치찌개"]["cost"] == pytest.approx(1500)
    assert rows["김치찌개"]["margin"] == pytest.approx(8000 - 1500)
    assert rows["김치찌개"]["cost_ratio"] == pytest.approx(1500 / 8000)


def test_단가가_오르면_기준일에_따라_원가가_바뀐다(kdb):
    rows = by(F.menu_costs(kdb, as_of=date(2026, 9, 30)), "menu")
    # 9/1 양파 4.2원 → 김치찌개 = 420 + 1,200 = 1,620원, 된장찌개 = 50×4.2 + 100×4.0 = 610원
    assert rows["김치찌개"]["cost"] == pytest.approx(1620)
    assert rows["된장찌개"]["cost"] == pytest.approx(610)
    assert len(rows) == 5


# ── 사이클 2: 메뉴 엔지니어링(Kasavana–Smith) ──
def test_메뉴엔지니어링_4분면(kdb):
    """9월 식사 메뉴 판매: 김치찌개10·된장찌개6·비빔밥1·고등어1 (총 18, 주류·음료 제외, 8월 판매 제외)
    인기 기준 = 0.7 × (1/4) = 0.175 → 김치·된장 인기, 비빔밥·고등어 비인기(1/18 = 0.056)
    마진(9/30 단가): 김치 6,380 / 된장 8,390 / 비빔밥 9,800 / 고등어 2,200
    마진 기준 = 판매수량 가중평균 = (10×6380 + 6×8390 + 9800 + 2200) / 18 = 7,007.8
    """
    rows = by(F.menu_engineering(kdb, *SEP), "menu")
    assert set(rows) == {"김치찌개", "된장찌개", "비빔밥", "고등어구이"}
    assert rows["김치찌개"]["sold"] == 10
    assert rows["김치찌개"]["mix"] == pytest.approx(10 / 18)
    assert rows["된장찌개"]["quadrant"] == "Star"
    assert rows["김치찌개"]["quadrant"] == "Plowhorse"
    assert rows["비빔밥"]["quadrant"] == "Puzzle"
    assert rows["고등어구이"]["quadrant"] == "Dog"
    assert rows["김치찌개"]["avg_margin_threshold"] == pytest.approx(126140 / 18)


# ── 사이클 3: 고객 흐름 — 회전율·대기·이탈 ──
def test_테이블_회전율은_착석팀수_나누기_테이블수(kdb):
    """9/1 점심 착석: C1(2명)·가족(3명)·혼밥(1명) = 3팀 6명, 대기 이탈 가족은 제외. 테이블 2개·좌석 6석."""
    rows = {(r["date"], r["slot"]): r for r in F.table_turnover(kdb, date(2026, 9, 1), date(2026, 9, 1))}
    lunch = rows[(date(2026, 9, 1), "점심")]
    assert lunch["parties"] == 3 and lunch["guests"] == 6
    assert lunch["table_turns"] == pytest.approx(3 / 2)
    assert lunch["seat_turns"] == pytest.approx(6 / 6)
    assert rows[(date(2026, 9, 1), "저녁")]["parties"] == 1


def test_동행유형별_대기시간과_이탈률(kdb):
    """9월 가족: 10분 대기 후 착석 1팀 + 대기 이탈 1팀 → 이탈률 50%, 착석팀 평균 대기 10분.
    혼밥: 4분 대기 1팀. 친구: 회원 방문 11팀(대기 0분) + C6 대기 이탈 1팀."""
    rows = by(F.waiting_stats(kdb, *SEP), "party_type")
    assert rows["가족"]["visits"] == 2
    assert rows["가족"]["abandon_rate"] == pytest.approx(0.5)
    assert rows["가족"]["avg_wait_min"] == pytest.approx(10)
    assert rows["혼밥"]["avg_wait_min"] == pytest.approx(4)
    assert rows["친구"]["visits"] == 12
    assert rows["친구"]["abandon_rate"] == pytest.approx(1 / 12)


# ── 사이클 4: 월별 식재료 원가율(ERP 전표) ──
def test_월별_원가율은_식재료비_나누기_매출(kdb):
    """8월: 매출 200,000 / 식재료비 60,000 → 30%, 9월: 100,000 / 35,000 → 35%"""
    rows = by(F.monthly_cost_ratio(kdb), "month")
    assert rows["2026-08"]["sales"] == 200000 and rows["2026-08"]["food_cost"] == 60000
    assert rows["2026-08"]["cost_ratio"] == pytest.approx(0.30)
    assert rows["2026-09"]["cost_ratio"] == pytest.approx(0.35)
    assert list(rows) == ["2026-08", "2026-09"]


# ── 사이클 5: 재고 — 레시피 대비 과다사용·폐기, 소진 예상일 ──
def test_레시피보다_더_쓴_양과_폐기량(kdb):
    """9/27 김치찌개 2인분(조리지시 K1): 레시피상 양파 200g인데 실제 210g → +5%, 돼지 200g 그대로 → 0%.
    9/28 기본반찬으로 양파 60g(조리지시 없음), 9/29 양파 30g 폐기.
    폐기율 = 폐기 ÷ 전체 사용(조리 210 + 반찬 60 + 폐기 30 = 300) = 10%
    — 조리지시에 연결된 소진(210)만 분모로 쓰면 반찬이 많은 재료의 폐기율이 부풀려진다."""
    rows = by(F.usage_variance(kdb, *SEP), "ingredient")
    assert rows["양파"]["theoretical"] == pytest.approx(200)
    assert rows["양파"]["actual"] == pytest.approx(210)
    assert rows["양파"]["variance_rate"] == pytest.approx(0.05)
    assert rows["양파"]["waste"] == pytest.approx(30)
    assert rows["양파"]["total_usage"] == pytest.approx(300)
    assert rows["양파"]["waste_rate"] == pytest.approx(0.10)
    assert rows["돼지앞다리"]["variance_rate"] == pytest.approx(0)


def test_현재고와_소진_예상일(kdb):
    """기준일 9/30. 양파: 입고 10,000 − 조리 210 − 반찬 60 − 폐기 30 = 9,700g, 최근 7일(9/24~9/30) 사용 300g
    → 하루 42.86g → 226.3일. 돼지: 5,000 − 200 = 4,800g < 안전재고 5,000g → 부족 경보."""
    rows = by(F.stock_status(kdb, as_of=date(2026, 9, 30)), "ingredient")
    assert rows["양파"]["stock"] == pytest.approx(9700)
    assert rows["양파"]["daily_usage"] == pytest.approx(300 / 7)
    assert rows["양파"]["days_left"] == pytest.approx(9700 / (300 / 7))
    assert rows["돼지앞다리"]["below_safety"] is True
    assert rows["양파"]["below_safety"] is False
    assert rows["두부"]["days_left"] is None          # 최근 사용이 없으면 '계산 불가'


# ── 사이클 6: 고객 세그먼트 ──
def test_고객_세그먼트_판정(kdb):
    """기준일 9/30.
    C1: 90일 내 4회, 마지막 8일 전 → 단골 / C2: 단골 + 객단가 80,000(단골 중 상위 20%) → VIP
    C3: 마지막 방문(7/22) 기준 직전 90일 4회였지만 70일 미방문 → 이탈위험
    C4: 2회 → 재방문 / C5: 9/25 1회(10/5 방문은 기준일 이후라 제외) → 신규
    C6: 대기 이탈만 → 판정 대상 아님"""
    rows = by(F.customer_segments(kdb, as_of=date(2026, 9, 30)), "customer_id")
    assert {c: r["segment"] for c, r in rows.items()} == {
        "C1": "단골", "C2": "VIP", "C3": "이탈위험", "C4": "재방문", "C5": "신규"}
    assert rows["C3"]["days_since_last"] == 70
    assert rows["C2"]["avg_spend"] == pytest.approx(80000)
    assert rows["C5"]["visits"] == 1


def test_세그먼트_요약(kdb):
    summary = F.segment_summary(kdb, as_of=date(2026, 9, 30))
    assert summary == {"신규": 1, "재방문": 1, "단골": 1, "VIP": 1, "이탈위험": 1}
