"""그래프 DB에 담긴 추론 결과를 SPARQL로 질의해 확인하는 테스트."""
import pytest

from graph_db import QUERIES, load_graph, run_query


@pytest.fixture(scope="module")
def graph():
    return load_graph()


def names(graph, title):
    return {row[0] for row in run_query(graph, QUERIES[title])}


def test_추론된_유동자산이_그래프에_저장됨(graph):
    assert {"현금", "매출채권", "재고자산"} <= names(graph, "유동자산 전체")


def test_특수관계자_계정과_상대방(graph):
    rows = set(run_query(graph, QUERIES["특수관계자와 거래한 계정과 그 상대방"]))
    assert ("대여금(자회사B)", "자회사B") in rows
    assert ("매출채권(관계기업D)", "관계기업D") in rows


def test_특수관계자_비유동_계정은_대여금만(graph):
    title = "특수관계자와 거래한 비유동 계정 (두 조건을 한 번에)"
    assert names(graph, title) == {"대여금(자회사B)"}


def test_기간없는_자산은_선급금뿐(graph):
    assert names(graph, "기간 정보가 없어 유동/비유동 분류가 안 된 자산") == {"선급금"}
