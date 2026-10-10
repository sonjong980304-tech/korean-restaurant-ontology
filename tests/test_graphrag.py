"""GraphRAG AI 매니저 테스트. LLM은 각본대로 답하는 가짜로 바꿔 끼워 결정적으로 검증한다.
실제 OpenAI 호출은 RUN_LIVE_LLM=1 일 때만(본 Neo4j 필요)."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dynamic import graphrag as G  # noqa: E402
from semantic import schema as S  # noqa: E402


def call(name, **args):
    return SimpleNamespace(id=f"call_{name}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


class FakeLLM:
    """미리 정한 응답을 순서대로 돌려주고, 받은 메시지를 기록한다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append(messages)
        if not self.responses:
            return SimpleNamespace(content=None, tool_calls=[call("monthly_cost_ratio")])   # 끝없이 도구만 부름
        content, calls = self.responses.pop(0)
        return SimpleNamespace(content=content, tool_calls=calls)


# ── 온톨로지 설명(시스템 프롬프트) ──
def test_온톨로지_설명에_객체_속성값_연결_규칙이_들어간다():
    text = G.describe_ontology(S.load())
    for must in ("Visit(방문)", "party_type", "회식", "(:Order)-[:TRIGGERED]->(:CookTicket)", "시스템 간",
                 "arrived_at.hour < 16", "g·ml·ea"):
        assert must in text


def test_기준정보에_업체별_공급재료와_메뉴id가_들어간다(kdb):
    text = G.reference_data(kdb)
    assert "S2 대체정육(육류): PORK(돼지앞다리)" in text
    assert "M-A 김치찌개 8,000원" in text


# ── 읽기 전용 안전장치 ──
@pytest.mark.parametrize("q, ok", [
    ("MATCH (m:MenuItem) RETURN m.name LIMIT 5", True),
    ("MATCH (n) DETACH DELETE n", False),
    ("create (x:Hack)", False),
    ("MATCH (m:MenuItem) SET m.price = 1", False),
    ("MERGE (x:Hack {id: 1})", False),
    ("CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {})", False),
    ("MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS", False),
])
def test_쓰기_쿼리는_차단(q, ok):
    assert G.is_read_only(q) is ok


def test_run_cypher_읽기는_결과를_쓰기는_거부(kdb):
    rows = G.run_cypher(kdb, "MATCH (m:MenuItem) RETURN m.name AS name ORDER BY name")
    assert [r["name"] for r in rows["rows"]][:2] == ["고등어구이", "김치찌개"]
    blocked = G.run_cypher(kdb, "MATCH (m:MenuItem) SET m.price = 1")
    assert "error" in blocked
    assert kdb.execute_query("MATCH (m:MenuItem {menu_id:'M-A'}) RETURN m.price AS p").records[0]["p"] == 8000


def test_필터를_우회해도_DB가_읽기모드로_쓰기를_막는다(kdb):
    with pytest.raises(Exception):
        G._read_query(kdb, "CREATE (:Hack)")
    assert kdb.execute_query("MATCH (h:Hack) RETURN count(h) AS n").records[0]["n"] == 0


# ── 에이전트 루프 ──
def test_도구_결과를_근거로_답한다(kdb):
    llm = FakeLLM((None, [call("menu_costs", as_of="2026-09-30")]),
                  ("김치찌개 원가는 1,620원입니다.", None))
    out = G.ask(kdb, "김치찌개 원가가 얼마야?", llm=llm)
    assert out["answer"] == "김치찌개 원가는 1,620원입니다."
    ev = out["evidence"][0]
    assert ev["tool"] == "menu_costs" and ev["args"] == {"as_of": "2026-09-30"}
    assert any(r["menu"] == "김치찌개" and r["cost"] == pytest.approx(1620) for r in ev["result"])
    tool_msg = llm.seen[1][-1]                          # 두 번째 호출 때 도구 결과가 LLM에 전달됐는지
    assert tool_msg["role"] == "tool" and "1620" in tool_msg["content"]


def test_액션은_제안만_하고_실행하지_않는다(kdb):
    llm = FakeLLM((None, [call("propose_action", action_type="change_price",
                               args={"menu_id": "M-A", "new_price": 8500}, reason="양파 인상 반영")]),
                  ("김치찌개를 8,500원으로 올리는 것을 제안합니다.", None))
    out = G.ask(kdb, "가격 올려줘", llm=llm)
    assert out["proposals"] == [{"action_type": "change_price", "args": {"menu_id": "M-A", "new_price": 8500},
                                 "reason": "양파 인상 반영"}]
    assert kdb.execute_query("MATCH (m:MenuItem {menu_id:'M-A'}) RETURN m.price AS p").records[0]["p"] == 8000


def test_도구를_끝없이_부르면_단계_제한에서_멈춘다(kdb):
    out = G.ask(kdb, "무한루프", llm=FakeLLM())
    assert len(out["evidence"]) == G.MAX_STEPS
    assert "단계" in out["answer"]


def test_알수없는_도구나_잘못된_인자는_오류를_LLM에_돌려준다(kdb):
    llm = FakeLLM((None, [call("delete_everything")]), (None, [call("menu_costs", as_of="어제")]), ("죄송합니다.", None))
    out = G.ask(kdb, "?", llm=llm)
    assert "error" in out["evidence"][0]["result"] and "error" in out["evidence"][1]["result"]
    assert out["answer"] == "죄송합니다."


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="실제 OpenAI 호출은 RUN_LIVE_LLM=1일 때만")
def test_실호출_9월_원가율_질문():
    from db import get_driver

    with get_driver() as driver:
        out = G.ask(driver, "9월 식재료 원가율은 7월보다 몇 %p 올랐어?")
    assert out["evidence"] and "39" in out["answer"]
