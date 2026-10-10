"""인수 테스트 — 실데이터(본 Neo4j, 7688)에서 Kinetic 함수가 '심어 둔 패턴'을 잡아내는지 확인한다.

데이터는 seed 고정이라 매번 같다. 함수 정답은 test_functions.py(작은 가게)가, 여기서는
'실제 규모에서도 의도한 신호가 보이는가'를 본다. 패턴 번호는 README의 '심어 둔 패턴'과 같다.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kinetic import functions as F  # noqa: E402

JUL, SEP = (date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 9, 1), date(2026, 9, 30))


@pytest.fixture(scope="module")
def real():
    from db import get_driver

    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception:
        pytest.skip("본 Neo4j(7688)가 꺼져 있음")
    yield driver
    driver.close()


def test_패턴7_9월_단가인상으로_원가율이_오른다(real):
    r = {x["month"]: x["cost_ratio"] for x in F.monthly_cost_ratio(real)}
    assert r["2026-09"] > r["2026-07"] + 0.01 and r["2026-09"] > r["2026-08"] + 0.01


def test_패턴13_룸이_부족해_회식_손님을_가장_많이_놓친다(real):
    rows = F.waiting_stats(real, *SEP)
    assert rows[0]["party_type"] == "회식"


def test_패턴6_9월엔_비빔국수가_Dog(real):
    quad = {r["menu"]: r["quadrant"] for r in F.menu_engineering(real, *SEP)}
    assert quad["비빔국수"] == "Dog"


def test_패턴11_저녁_불고기_과다사용으로_소불고기가_가장_많이_샌다(real):
    rows = F.usage_variance(real, *JUL)
    assert rows[0]["ingredient"] == "소불고기"
    food = [r for r in rows if r["unit"] != "ea"]
    assert all(r["variance_rate"] > 0 for r in food)          # 레시피보다 평균 3% 더 쓴다


def test_패턴12_유통기한_4일_이하_재료의_폐기율이_가장_높다(real):
    rows = sorted(F.usage_variance(real, *JUL), key=lambda r: -(r["waste_rate"] or 0))
    assert {r["ingredient"] for r in rows[:4]} == {"상추", "콩나물", "소불고기", "바지락"}
    assert all(r["waste_rate"] < 0.15 for r in rows if r["waste_rate"])


def test_패턴8_8월말_이후_이탈위험_회원이_급증한다(real):
    before = F.segment_summary(real, date(2026, 8, 20))["이탈위험"]
    after = F.segment_summary(real, date(2026, 9, 30))["이탈위험"]
    assert after > before * 10


def test_재고가_음수로_내려가지_않는다(real):
    assert all(r["stock"] >= 0 for r in F.stock_status(real, date(2026, 9, 30)))
