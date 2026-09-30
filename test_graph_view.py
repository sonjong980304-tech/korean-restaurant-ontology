"""그림에 들어갈 데이터가 '입력한 사실'과 '추론 결과'를 올바르게 나누는지 확인한다."""
import pytest

from graph_view import build_graph_data, render


@pytest.fixture(scope="module")
def data():
    return build_graph_data()


def find(data, source, target, relation):
    return [e for e in data["edges"]
            if (e["source"], e["target"], e["relation"]) == (source, target, relation)]


def test_입력한_사실은_fact(data):
    assert find(data, "inventory", "Asset", "type")[0]["status"] == "fact"
    assert find(data, "inventory", "short_term", "has_term")[0]["status"] == "fact"


def test_추론기가_만든_분류는_inferred(data):
    assert find(data, "inventory", "CurrentAsset", "type")[0]["status"] == "inferred"
    assert find(data, "loan_to_subsidiary", "RelatedPartyAccount", "type")[0]["status"] == "inferred"


def test_개념_계층은_rule(data):
    assert find(data, "Subsidiary", "RelatedParty", "subClassOf")[0]["status"] == "rule"


def test_노드_종류_구분(data):
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}
    assert kinds["Asset"] == "class"
    assert kinds["cash"] == "account"
    assert kinds["subsidiary_b"] == "entity"
    assert kinds["short_term"] == "term"


def test_질문_결과가_노드_id로_담김(data):
    result = {q["title"]: set(q["ids"]) for q in data["queries"]}
    assert {"cash", "inventory", "accounts_receivable"} <= result["유동자산 전체"]
    assert result["기간 정보가 없어 유동/비유동 분류가 안 된 자산"] == {"prepayment"}


def test_html에_데이터가_들어감(data):
    html = render(data)
    assert "__GRAPH_DATA__" not in html
    assert '"inventory"' in html
