"""Semantic 레이어 설계도(schema.yaml)를 읽고 검증한 뒤 Neo4j 제약조건으로 옮긴다."""
from pathlib import Path

import yaml

SCHEMA_PATH = Path(__file__).with_name("schema.yaml")
PROFILE_PATH = Path(__file__).with_name("profile.yaml")
TYPES = {"string", "int", "float", "bool", "date", "datetime", "time", "enum"}


def load(path=SCHEMA_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile(path=PROFILE_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _check_props(owner, props, errors):
    for name, spec in (props or {}).items():
        if spec.get("type") not in TYPES:
            errors.append(f"{owner}.{name}: 알 수 없는 타입 {spec.get('type')}")
        if spec.get("type") == "enum" and not spec.get("values"):
            errors.append(f"{owner}.{name}: enum인데 values가 없음")


def validate(schema):
    """설계도의 내부 모순을 찾아 오류 메시지 목록으로 돌려준다(비어 있으면 통과)."""
    errors = []
    systems = set(schema["systems"])
    objects = schema["objects"]

    for name, obj in objects.items():
        if not set(obj["backing"]) <= systems:
            errors.append(f"{name}: 없는 시스템이 뒷받침함 {set(obj['backing']) - systems}")
        if obj["key"] not in obj["properties"]:
            errors.append(f"{name}: 키 {obj['key']}가 속성에 없음")
        _check_props(name, obj["properties"], errors)
        for prop, spec in obj["properties"].items():
            if "source" in spec and spec["source"] not in obj["backing"]:
                errors.append(f"{name}.{prop}: 출처 {spec['source']}가 backing에 없음")

    seen = set()
    for link in schema["links"]:
        name = link["name"]
        if name in seen:
            errors.append(f"링크 이름 중복: {name}")
        seen.add(name)
        for end in ("from", "to"):
            if link[end] not in objects:
                errors.append(f"{name}: 없는 객체 {link[end]}")
        _check_props(name, link.get("properties"), errors)
        if link.get("cross_system"):
            pair = link.get("systems", [])
            if len(set(pair)) != 2 or not set(pair) <= systems:
                errors.append(f"{name}: 교차 연결인데 서로 다른 두 시스템이 아님 {pair}")
            if not link.get("join"):
                errors.append(f"{name}: 교차 연결인데 join 규칙이 없음")
        elif link.get("system") not in systems:
            errors.append(f"{name}: 내부 연결의 system이 없음")
    return errors


def constraint_statements(schema):
    """객체마다 '키 값은 중복 불가' 제약조건을 만드는 Cypher 문장."""
    return [
        f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.{obj['key']} IS UNIQUE"
        for label, obj in schema["objects"].items()
    ]


def apply_constraints(driver, schema):
    errors = validate(schema)
    if errors:
        raise ValueError("설계도 오류:\n" + "\n".join(errors))
    for stmt in constraint_statements(schema):
        driver.execute_query(stmt)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from db import get_driver

    schema = load()
    with get_driver() as driver:
        apply_constraints(driver, schema)
        rows = driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n").records
    cross = [l for l in schema["links"] if l.get("cross_system")]
    print(f"객체 {len(schema['objects'])}종, 연결 {len(schema['links'])}종(시스템 간 {len(cross)}종)")
    print(f"Neo4j 제약조건 {rows[0]['n']}개 적용 완료")
