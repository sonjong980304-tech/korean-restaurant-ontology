"""회계 온톨로지를 그래프 DB(트리플 저장소)에 담아 SPARQL로 질의하는 실습.

실행: .venv/bin/python graph_db.py

  1단계: 온톨로지를 만들고 추론기를 돌린다 (추론 결과까지 포함)
  2단계: 결과를 (주어, 관계, 대상) 트리플로 그래프 DB(rdflib)에 넣는다
  3단계: SPARQL(그래프 질의 언어)로 질문을 던진다
"""
from rdflib import Graph

from accounting_ontology import add_accounts, build_schema, reason

ASSERTED_FILE = "accounting.owl"  # 추론 전: 내가 직접 입력한 것만
INFERRED_FILE = "accounting_inferred.owl"  # 추론 후: 입력 + 추론기가 찾아낸 것

PREFIX = """
PREFIX a:    <http://example.org/accounting.owl#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

# 질문 이름 → SPARQL. 변수(?이름)가 "찾고 싶은 것"이다.
QUERIES = {
    "유동자산 전체": """
        SELECT ?이름 WHERE {
            ?x rdf:type a:CurrentAsset ;
               rdfs:label ?이름 .
        } ORDER BY ?이름
    """,
    "특수관계자와 거래한 계정과 그 상대방": """
        SELECT ?계정 ?상대방 WHERE {
            ?x rdf:type a:RelatedPartyAccount ;
               rdfs:label ?계정 ;
               a:has_counterparty ?p .
            ?p rdfs:label ?상대방 .
        } ORDER BY ?계정
    """,
    "특수관계자와 거래한 비유동 계정 (두 조건을 한 번에)": """
        SELECT ?이름 WHERE {
            ?x rdf:type a:RelatedPartyAccount ;
               rdf:type a:NonCurrentAsset ;
               rdfs:label ?이름 .
        }
    """,
    "기간 정보가 없어 유동/비유동 분류가 안 된 자산": """
        SELECT ?이름 WHERE {
            ?x rdf:type a:Asset ;
               rdfs:label ?이름 .
            FILTER NOT EXISTS { ?x a:has_term ?t }
        }
    """,
}


def load_graphs():
    """1~2단계: 온톨로지를 만들어 (추론 전, 추론 후) 그래프 DB 두 개로 적재한다."""
    onto = build_schema()
    add_accounts(onto)
    onto.save(file=ASSERTED_FILE, format="rdfxml")
    reason(onto)
    onto.save(file=INFERRED_FILE, format="rdfxml")

    asserted, inferred = Graph(), Graph()
    asserted.parse(ASSERTED_FILE, format="xml")
    inferred.parse(INFERRED_FILE, format="xml")
    return asserted, inferred


def load_graph():
    """추론까지 끝난 그래프 DB만 필요할 때."""
    return load_graphs()[1]


def run_query(graph, sparql):
    """3단계: SPARQL을 실행해 결과를 문자열 튜플 목록으로 돌려준다."""
    return [tuple(str(v) for v in row) for row in graph.query(PREFIX + sparql)]


def main():
    graph = load_graph()
    print(f"그래프 DB에 들어간 트리플(주어-관계-대상) 수: {len(graph)}")

    print("\n[저장된 트리플 예시: 재고자산]")
    for s, p, o in graph.triples((None, None, None)):
        if str(s).endswith("#inventory"):
            print(f"  ({s.split('#')[-1]}) ─{p.split('#')[-1]}→ ({o.split('#')[-1]})")

    for title, sparql in QUERIES.items():
        print(f"\n[질문] {title}")
        rows = run_query(graph, sparql)
        if not rows:
            print("  (결과 없음)")
        for row in rows:
            print("  " + "  ↔  ".join(row))


if __name__ == "__main__":
    main()
