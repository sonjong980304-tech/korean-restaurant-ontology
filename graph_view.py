"""그래프 DB의 내용을 브라우저에서 볼 수 있는 그림(graph.html)으로 만든다.

실행: .venv/bin/python graph_view.py   →  graph.html 생성 (더블클릭하면 열림)

  · 입력한 사실(추론 전)과 추론기가 만든 결론(추론 후)을 비교해 선을 나눠 그린다
  · SPARQL 질문 4개의 결과를 화면에서 버튼으로 강조할 수 있게 함께 담는다
"""
import json
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Namespace, URIRef

from graph_db import QUERIES, load_graphs, run_query

A = Namespace("http://example.org/accounting.owl#")
TEMPLATE = Path(__file__).with_name("graph_template.html")
OUTPUT = Path(__file__).with_name("graph.html")

# 그림에 그릴 관계와 화면에 보일 이름
RELATIONS = {
    RDF.type: ("type", "종류"),
    RDFS.subClassOf: ("subClassOf", "하위 개념"),
    A.has_term: ("has_term", "회수 기간"),
    A.has_counterparty: ("has_counterparty", "거래 상대방"),
}
ROOT_KIND = {"Account": "account", "Entity": "entity", "Term": "term"}


def local(uri):
    return str(uri).split("#")[-1]


def label_of(graph, uri):
    label = graph.value(uri, RDFS.label)
    return str(label) if label else local(uri)


def kind_of(graph, node):
    """개체는 자기 종류를 위로 거슬러 올라가 계정/상대방/기간 중 무엇인지 정한다."""
    if (node, RDF.type, OWL.Class) in graph:
        return "class"
    pending = [o for o in graph.objects(node, RDF.type) if str(o).startswith(str(A))]
    while pending:
        cls = pending.pop()
        if local(cls) in ROOT_KIND:
            return ROOT_KIND[local(cls)]
        pending += [o for o in graph.objects(cls, RDFS.subClassOf) if isinstance(o, URIRef)]
    return "entity"


def build_graph_data():
    """(추론 전, 추론 후) 그래프를 비교해 화면용 노드·선·질문 결과를 만든다."""
    asserted, inferred = load_graphs()

    def in_ns(uri):
        return isinstance(uri, URIRef) and str(uri).startswith(str(A))

    nodes = {}
    for cls in inferred.subjects(RDF.type, OWL.Class):
        if in_ns(cls):
            nodes[local(cls)] = {"id": local(cls), "label": label_of(inferred, cls),
                                 "kind": "class"}
    for ind in inferred.subjects(RDF.type, OWL.NamedIndividual):
        if in_ns(ind):
            nodes[local(ind)] = {"id": local(ind), "label": label_of(inferred, ind),
                                 "kind": kind_of(asserted, ind)}

    edges = []
    for pred, (rel, rel_label) in RELATIONS.items():
        for s, o in inferred.subject_objects(pred):
            if not (in_ns(s) and in_ns(o)):
                continue
            if local(s) not in nodes or local(o) not in nodes:
                continue
            edges.append({
                "source": local(s), "target": local(o),
                "relation": rel, "relationLabel": rel_label,
                # 추론 전 그래프에도 있으면 내가 입력한 것, 없으면 추론기가 만든 것
                "status": "fact" if (s, pred, o) in asserted else "inferred",
            })
    # 개념 사이의 상하 관계는 '규칙'이라 입력한 사실과 색을 구분한다
    for edge in edges:
        if edge["relation"] == "subClassOf":
            edge["status"] = "rule" if edge["status"] == "fact" else "inferred"

    by_label = {n["label"]: n["id"] for n in nodes.values()}
    queries = []
    for title, sparql in QUERIES.items():
        labels = {value for row in run_query(inferred, sparql) for value in row}
        queries.append({"title": title,
                        "ids": sorted(by_label[l] for l in labels if l in by_label)})

    return {"nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": edges, "queries": queries}


def render(data):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__GRAPH_DATA__", payload)


def main():
    data = build_graph_data()
    OUTPUT.write_text(render(data), encoding="utf-8")
    facts = sum(e["status"] == "fact" for e in data["edges"])
    inferred = sum(e["status"] == "inferred" for e in data["edges"])
    print(f"노드 {len(data['nodes'])}개 · 입력한 사실 {facts}개 · 추론 결과 {inferred}개")
    print(f"그림 파일: {OUTPUT}")


if __name__ == "__main__":
    main()
