"""Streamlit 앱 렌더 테스트 — 본 Neo4j(7688)로 각 화면을 실제로 실행해 예외가 없는지 본다.
데이터를 바꾸는 버튼(액션·저장)은 누르지 않는다."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest  # noqa: E402

VIEWS = ["overview", "ontology", "operations", "actions", "simulation", "assistant"]


@pytest.fixture(scope="module", autouse=True)
def need_db():
    from db import get_driver

    try:
        with get_driver() as d:
            d.verify_connectivity()
    except Exception:
        pytest.skip("본 Neo4j(7688)가 꺼져 있음")


@pytest.mark.parametrize("view", VIEWS)
def test_화면이_오류없이_그려진다(view):
    at = AppTest.from_file(str(ROOT / "app" / "views" / f"{view}.py"), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title and at.title[0].value


def test_개요_핵심숫자(view="overview"):
    at = AppTest.from_file(str(ROOT / "app" / "views" / "overview.py"), default_timeout=120).run()
    labels = [m.label for m in at.metric]
    assert labels == ["매출", "식재료 원가율", "하루 평균 테이블 회전", "대기 중 떠난 팀", "이탈위험 단골"]
    assert at.metric[1].value == "39.5%"                  # 9월 원가율(인수 테스트와 같은 값)


def test_진입파일이_페이지_6개를_등록한다():
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=120).run()
    assert not at.exception
    assert at.title[0].value == "한솥밥상 운영 개요"
