"""Semantic — 온톨로지 설계도와 객체 이웃 탐색."""
import pandas as pd
import streamlit as st
from common import SYSTEM_COLORS, SYSTEM_KO, db_or_stop, graph, legend, setup, system_of

from semantic import schema as S

setup("온톨로지 탐색", "SEMANTIC · 지도", "객체(점)와 연결(선)로 가게 전체를 표현한 설계도와, 실제 데이터 한 건의 주변을 봅니다.")
d = db_or_stop()
schema = S.load()
legend([(SYSTEM_KO[s], c) for s, c in SYSTEM_COLORS.items()])

tab1, tab2, tab3 = st.tabs(["설계도", "객체 주변 보기", "시스템별 데이터 양"])

with tab1:
    st.caption("점 색 = 그 객체를 뒷받침하는 원천 시스템. 굵은 선 = 시스템을 넘나드는 연결(온톨로지가 잇는 선).")
    nodes = [{"id": label, "label": f"{o['label_ko']}\n{label}", "system": "+".join(o["backing"]),
              "title": f"{o['label_ko']} ({label})\n출처: {' + '.join(o['backing'])}\n키: {o['key']}\n속성: "
                       + ", ".join(o["properties"])} for label, o in schema["objects"].items()]
    edges = [{"from": link["from"], "to": link["to"], "label": link["label_ko"],
              "title": f"{link['name']}\n" + (f"시스템 간: {' → '.join(link['systems'])}\n규칙: {link['join']}"
                                              if link.get("cross_system") else f"{link.get('system')} 내부"),
              "width": 3 if link.get("cross_system") else 1, "dashes": link.get("system") == "ONTOLOGY"}
             for link in schema["links"]]
    graph(nodes, edges, height=620)

with tab2:
    KINDS = {
        "메뉴": ("MenuItem", "menu_id", "MATCH (n:MenuItem) RETURN n.menu_id AS id, n.name AS name ORDER BY id"),
        "재료": ("Ingredient", "ingredient_id", "MATCH (n:Ingredient) RETURN n.ingredient_id AS id, n.name AS name ORDER BY name"),
        "방문(9월 30일)": ("Visit", "visit_id", "MATCH (n:Visit) WHERE date(n.arrived_at) = date('2026-09-30') "
                         "RETURN n.visit_id AS id, n.party_type + ' ' + n.party_size + '명' AS name ORDER BY id"),
        "전표(9월)": ("JournalEntry", "je_id", "MATCH (n:JournalEntry) WHERE n.posted_on >= date('2026-09-01') "
                    "RETURN n.je_id AS id, n.description AS name ORDER BY id"),
        "의사결정": ("Decision", "decision_id", "MATCH (n:Decision) RETURN n.decision_id AS id, n.title AS name"),
        "액션기록": ("ActionLog", "action_id", "MATCH (n:ActionLog) RETURN n.action_id AS id, "
                   "n.action_type + ' ' + n.status AS name ORDER BY n.executed_at DESC"),
    }
    k1, k2, k3 = st.columns([1, 2, 1])
    kind = k1.selectbox("종류", list(KINDS))
    label, key, list_q = KINDS[kind]
    options = [(r["id"], r["name"]) for r in d.execute_query(list_q).records]
    if not options:
        st.info("아직 이 종류의 데이터가 없습니다. 「액션」이나 「예측·시뮬레이션」 화면에서 만들면 여기에 나타납니다.")
    else:
        picked = k2.selectbox("대상", options, format_func=lambda o: f"{o[1]} ({o[0]})")
        per_type = k3.slider("선 종류별 최대 표시", 3, 20, 8)
        rows = d.execute_query(
            f"""
            MATCH (n:{label} {{{key}: $id}})-[r]-(m)
            WITH n, type(r) AS t, count(*) AS total, collect({{r: r, m: m, out: startNode(r) = n}})[..$k] AS xs
            UNWIND xs AS x
            RETURN n, t, total, x.r AS r, x.m AS m, x.out AS out
            """, id=picked[0], k=per_type).records
        if not rows:
            st.info("연결된 객체가 없습니다.")
        else:
            center = rows[0]["n"]
            nodes = {center.element_id: {"id": center.element_id, "label": picked[1], "system": center.get("_system"),
                                         "size": 24, "title": "\n".join(f"{k}: {v}" for k, v in dict(center).items())}}
            edges, totals = [], {}
            for r in rows:
                m = r["m"]
                mlabel = next(iter(m.labels))
                name = m.get("name") or m.get("title") or m.get(schema["objects"].get(mlabel, {}).get("key", ""), "")
                nodes[m.element_id] = {"id": m.element_id, "label": f"{schema['objects'].get(mlabel, {}).get('label_ko', mlabel)}\n{name}",
                                       "system": m.get("_system"),
                                       "title": "\n".join(f"{k}: {v}" for k, v in dict(m).items())}
                props = dict(r["r"])
                tip = r["t"] + ("".join(f"\n{k}: {v}" for k, v in props.items()))
                a, b = (center.element_id, m.element_id) if r["out"] else (m.element_id, center.element_id)
                edges.append({"from": a, "to": b, "label": r["t"], "title": tip,
                              "width": 3 if "_rule" in props else 1})
                totals[r["t"]] = r["total"]
            st.caption("선 위에 마우스를 올리면 연결의 근거(_rule: 적용 규칙, _join_key: 실제 키값)가 보입니다. "
                       + " · ".join(f"{t} {n:,}개" for t, n in totals.items()))
            graph(list(nodes.values()), edges, height=560)

with tab3:
    rows = d.execute_query("MATCH (n) RETURN labels(n)[0] AS label, n._system AS system, count(*) AS n").records
    df = pd.DataFrame([dict(r) for r in rows])
    df["객체"] = df["label"].map(lambda x: f"{schema['objects'].get(x, {}).get('label_ko', x)} ({x})")
    df["시스템"] = df["system"].map(lambda s: SYSTEM_KO.get(system_of(s), s) + (" + MES" if s == "POS+MES" else ""))
    st.dataframe(df[["시스템", "객체", "n"]].rename(columns={"n": "개수"}).sort_values(["시스템", "개수"], ascending=[True, False]),
                 hide_index=True, use_container_width=True, height=560)
