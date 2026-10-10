import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from semantic import schema as S  # noqa: E402

SCHEMA = S.load()
PROFILE = S.load_profile()


def test_설계도에_모순이_없다():
    assert S.validate(SCHEMA) == []


def test_네_원천시스템과_온톨로지_쓰기가_모두_객체를_뒷받침한다():
    backed = {sys for obj in SCHEMA["objects"].values() for sys in obj["backing"]}
    assert backed == {"POS", "MES", "SCM", "ERP", "ONTOLOGY"}


def test_네_시스템이_교차연결로_하나로_이어진다():
    # 시스템을 점, 교차 연결을 선으로 보고 모든 시스템이 한 덩어리로 이어지는지 확인
    edges = [set(l["systems"]) for l in SCHEMA["links"] if l.get("cross_system")]
    reached, frontier = {"POS"}, ["POS"]
    while frontier:
        cur = frontier.pop()
        for e in edges:
            if cur in e:
                for nxt in e - reached:
                    reached.add(nxt)
                    frontier.append(nxt)
    assert reached == {"POS", "MES", "SCM", "ERP"}


def test_메뉴는_POS와_MES가_나눠_뒷받침한다():
    assert set(SCHEMA["objects"]["MenuItem"]["backing"]) == {"POS", "MES"}


def test_고객축_분리_성별은_고객에_동행유형은_방문에():
    objs = SCHEMA["objects"]
    assert "gender" in objs["Customer"]["properties"]
    assert "party_type" in objs["Visit"]["properties"]
    assert "party_type" not in objs["Customer"]["properties"]
    assert set(objs["Visit"]["properties"]["party_type"]["values"]) == {"혼밥", "연인", "친구", "가족", "회식", "비즈니스"}


def test_비회원을_위해_성별_연령은_모름을_허용한다():
    props = SCHEMA["objects"]["Customer"]["properties"]
    assert props["gender"]["nullable"] and props["age_group"]["nullable"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s["links"].append({"name": "X", "from": "Ghost", "to": "Visit", "system": "POS"}), "없는 객체"),
        (lambda s: s["objects"]["Visit"].update(key="nope"), "키 nope"),
        (lambda s: s["links"].append({"name": "Y", "from": "Order", "to": "Visit", "cross_system": True, "systems": ["POS", "MES"]}), "join 규칙"),
        (lambda s: s["objects"]["Order"]["backing"].update(CRM="x"), "없는 시스템"),
    ],
)
def test_잘못된_설계도는_잡아낸다(mutate, expected):
    bad = copy.deepcopy(SCHEMA)
    mutate(bad)
    assert any(expected in e for e in S.validate(bad))


def test_확정한_규모_숫자():
    assert sum(t["count"] for t in PROFILE["tables"]) == 18
    assert sum(t["count"] * t["capacity"] for t in PROFILE["tables"]) == 72
    menus = PROFILE["menu_categories"]
    assert len(menus) == 7 and sum(len(v) for v in menus.values()) == 24
    assert len(PROFILE["suppliers"]) == 6
    assert sum(e["count"] for e in PROFILE["employees"]) == 10
    assert len(PROFILE["shifts"]) == 3
    assert PROFILE["ingredient_count"] == 40


def test_제약조건이_객체마다_하나씩_만들어진다():
    stmts = S.constraint_statements(SCHEMA)
    assert len(stmts) == len(SCHEMA["objects"])
    assert any("FOR (n:MenuItem) REQUIRE n.menu_id IS UNIQUE" in s for s in stmts)


def test_Neo4j에_제약조건이_실제로_적용된다():
    from db import get_driver

    try:
        driver = get_driver()
        driver.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j가 꺼져 있음")
    with driver:
        S.apply_constraints(driver, SCHEMA)
        names = {r["name"] for r in driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN name").records}
    assert {f"{label.lower()}_key" for label in SCHEMA["objects"]} <= names
