import csv
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sources"))
from semantic import pipeline as P  # noqa: E402
from semantic import schema as S  # noqa: E402

SCHEMA = S.load()
PROFILE = S.load_profile()


@pytest.fixture(scope="module")
def g():
    if not (P.RAW / "pos" / "visit.csv").exists():
        import generate

        generate.Store().run()
    return P.build()


def test_설계도를_모두_지킨다(g):
    assert P.conform(SCHEMA, g) == []


def test_확정한_규모가_그래프에_그대로(g):
    n = {label: len(rows) for label, rows in g.nodes.items()}
    assert n["DiningTable"] == 18 and n["Zone"] == 2
    assert n["MenuItem"] == 24 and n["MenuCategory"] == 7
    assert n["Ingredient"] == 40 and n["Supplier"] == 6
    assert n["Employee"] == 10 and n["Shift"] == 3
    assert n["Customer"] == PROFILE["restaurant"]["members"]


def test_설계도의_모든_연결이_실제로_채워진다(g):
    empty = [l["name"] for l in SCHEMA["links"] if not g.links.get(l["name"])]
    assert empty == []


def test_모든_메뉴에_레시피가_있고_모든_재료가_쓰인다(g):
    uses = g.links["USES"]
    assert {a for a, _, _ in uses} == set(g.nodes["MenuItem"])
    used = {b for _, b, _ in uses} | {b for _, b, _ in g.links["MOVES"]}
    assert used == set(g.nodes["Ingredient"])


def test_SCM_kg단가가_g단가로_환산된다(g):
    onion = next(p for a, b, p in g.links["SUPPLIES"] if b == "VEG-ONION-1KG")
    assert g.nodes["Ingredient"]["VEG-ONION-1KG"]["unit"] == "g"
    assert onion["unit_price"] == pytest.approx(4.2)        # 3,000원/kg × 1.4(9월 인상) ÷ 1000


def test_POS_코드값이_번역된다(g):
    visits = g.nodes["Visit"].values()
    assert {v["party_type"] for v in visits} == {"혼밥", "연인", "친구", "가족", "회식", "비즈니스"}
    genders = {c["gender"] for c in g.nodes["Customer"].values()}
    assert genders == {"남", "여", None}                     # 모르는 성별은 '모름'(None)으로 남는다


def test_비회원_방문에는_고객이_연결되지_않는다(g):
    member_visits = {b for _, b, _ in g.links["MADE_VISIT"]}
    ratio = 1 - len(member_visits) / len(g.nodes["Visit"])
    assert 0.35 < ratio < 0.45


def test_전표에서_원천_결제까지_거슬러_올라간다(g):
    """감사 추적: 매출전표의 대변(매출) 금액 = 그 전표에 연결된 POS 결제 합계."""
    pay = g.nodes["Payment"]
    linked = defaultdict(int)
    for je, p, _ in g.links["POSTS_SALES"]:
        linked[je] += pay[p]["amount"]
    sales_credit = {je: x["amount"] for je, acct, x in g.links["CREDITS"] if acct == "401"}
    assert linked and all(linked[je] == sales_credit[je] for je in linked)


def test_모든_전표는_차변과_대변이_같다(g):
    bal = defaultdict(int)
    for je, _, x in g.links["DEBITS"]:
        bal[je] += x["amount"]
    for je, _, x in g.links["CREDITS"]:
        bal[je] -= x["amount"]
    assert all(v == 0 for v in bal.values())


def test_매핑되지_않은_주방재료는_적재를_막는다(tmp_path, g):
    raw = tmp_path / "raw"
    shutil.copytree(P.RAW, raw, ignore=shutil.ignore_patterns("consumption.csv", "kds_ticket.csv"))
    for name in ("consumption.csv", "kds_ticket.csv"):
        with open(raw / "mes" / name, "w", encoding="utf-8") as f:
            f.write(open(P.RAW / "mes" / name, encoding="utf-8").readline())
    with open(raw / "mes" / "recipe_line.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["R-JJ-KIMCHI", "적양파", 10])
    with pytest.raises(P.PipelineError, match="적양파"):
        P.build(raw=raw)


def test_설계도_위반_그래프는_잡아낸다(g):
    bad = P.Graph()
    bad.node("Visit", "V1", {"visit_id": "V1", "party_type": "동창회"}, "POS")
    bad.link("SEATED_AT", "V1", "T99")
    errors = P.conform(SCHEMA, bad)
    assert any("동창회" in e for e in errors)
    assert any("필수 속성 없음" in e for e in errors)
    assert any("끊어진 선" in e for e in errors)


def test_Neo4j_적재_결과가_그래프와_같다(g):
    from db import get_driver

    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j가 꺼져 있음")
    nodes, links = P.summary(g)
    with driver:
        P.load(driver, SCHEMA, g)
        db_nodes = {r["l"]: r["c"] for r in driver.execute_query(
            "MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c").records}
        db_links = {r["t"]: r["c"] for r in driver.execute_query(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c").records}
        # 시스템을 넘나드는 질문 하나: 양파를 쓰는 메뉴(MES→SCM)와 그 메뉴의 매출(POS)
        onion = driver.execute_query(
            "MATCH (i:Ingredient {name:'양파'})<-[:USES]-(m:MenuItem)<-[c:CONTAINS]-(:Order) "
            "RETURN count(DISTINCT m) AS menus, sum(c.qty * c.unit_price) AS sales").records[0]
    assert db_nodes == nodes
    assert db_links == links
    assert onion["menus"] >= 10 and onion["sales"] > 0
