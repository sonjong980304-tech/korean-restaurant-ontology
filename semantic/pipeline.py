"""원천 시스템 4개의 데이터를 하나의 온톨로지 그래프로 묶어 Neo4j에 적재한다.

팔란티어의 '파이프라인 → 온톨로지' 단계에 해당한다.
  1) 읽기:   data/raw/{pos,mes,scm,erp}/*.csv  (시스템마다 코드·단위·시각 형식이 다르다)
  2) 정규화: 코드값 번역(M→남, FAMILY→가족), 단위 환산(kg→g), 시각 형식 통일
  3) 연결:   schema.yaml의 join 규칙대로 시스템을 넘나드는 선을 잇는다
  4) 검증:   모든 노드가 설계도를 지키는지, 끊어진 선이 없는지 확인 — 하나라도 어긋나면 적재 중단
  5) 적재:   Neo4j를 비우고 다시 채운다

이 모듈은 sources/master.yaml(가게의 '진짜 값')을 보지 않는다. 오직 원천 데이터와 매핑표만 본다.
"""
import csv
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from semantic import schema as S  # noqa: E402

RAW = ROOT / "data" / "raw"
MAPPING = Path(__file__).with_name("mappings") / "material_map.csv"
BATCH = 5000

# ── 시스템별 코드값 → 온톨로지 값 ──
GENDER = {"M": "남", "F": "여"}
PARTY = {"SOLO": "혼밥", "COUPLE": "연인", "FRIENDS": "친구", "FAMILY": "가족", "TEAM": "회식", "BIZ": "비즈니스"}
WAIT_OUTCOME = {"SEATED": "착석", "LEFT": "이탈"}
PAY_METHOD = {"CARD": "카드", "CASH": "현금", "EASY": "간편결제"}
SEATING = {"STAND": "입식", "FLOOR": "좌식"}
STATION = {"STEW": "찌개", "GRILL": "구이", "COLD": "찬·면", "BAR": "음료"}
MOVE_KIND = {"USE": "소진", "WASTE": "폐기"}
PO_STATUS = {"REQUESTED": "요청", "CONFIRMED": "확정", "RECEIVED": "입고완료", "CANCELLED": "취소"}
UNIT = {"KG": ("g", 1000), "L": ("ml", 1000), "EA": ("ea", 1)}   # SCM 구매단위 → (기준단위, 배수)


class PipelineError(ValueError):
    pass


# ── 시각 형식(시스템마다 다름) ──
def pos_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def mes_dt(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%S")


def erp_date(s):
    return datetime.strptime(s, "%Y/%m/%d").date()


def age_group(birth_year, as_of=2026):
    if not birth_year:
        return None
    age = as_of - int(birth_year)
    return "60대+" if age >= 60 else f"{max(age // 10, 2) * 10}대"


def read(raw, system, table):
    with open(raw / system / f"{table}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Graph:
    """노드: 라벨 → {키: 속성}, 선: 이름 → [(출발키, 도착키, 속성)]"""

    def __init__(self):
        self.nodes = defaultdict(dict)
        self.links = defaultdict(list)

    def node(self, label, key, props, system):
        props["_system"] = system                 # 어느 시스템에서 왔는지(출처 추적)
        self.nodes[label][key] = props

    def link(self, name, a, b, props=None):
        self.links[name].append((a, b, props or {}))


def build(raw=RAW, mapping=MAPPING, schema=None):
    g = Graph()
    R = lambda s, t: read(raw, s, t)  # noqa: E731
    errors = []
    # 계보(lineage): 시스템을 넘나드는 선마다 '어떤 규칙(_rule)으로, 어떤 키값(_join_key)으로' 이었는지 남긴다
    rules = {l["name"]: l["join"] for l in (schema or S.load())["links"] if l.get("cross_system")}

    def lineage(name, key, props=None):
        return {**(props or {}), "_rule": rules[name], "_join_key": key}

    # ═══ SCM: 재료·공급업체가 기준. 매핑표로 주방(MES) 이름과 잇는다 ═══
    with open(mapping, encoding="utf-8") as f:
        mes_to_item = {r["mes_material_name"]: r["scm_item_code"] for r in csv.DictReader(f)}
    item_to_mes = {v: k for k, v in mes_to_item.items()}
    conv = {}
    for r in R("scm", "item_master"):
        unit, factor = UNIT[r["uom"]]
        conv[r["item_code"]] = factor
        if r["item_code"] not in item_to_mes:
            errors.append(f"매핑표에 없는 SCM 품목: {r['item_code']}")
            continue
        g.node("Ingredient", r["item_code"], {
            "ingredient_id": r["item_code"], "name": item_to_mes[r["item_code"]], "scm_item_code": r["item_code"],
            "mes_material_name": item_to_mes[r["item_code"]], "unit": unit, "storage": r["storage"],
            "shelf_life_days": int(r["shelf_life_days"]), "safety_stock": float(r["safety_stock"]) * factor,
        }, "SCM")

    def material(name, where):
        item = mes_to_item.get(name)
        if item is None or item not in g.nodes["Ingredient"]:
            errors.append(f"{where}: 매핑되지 않은 주방 재료명 '{name}'")
        return item

    for r in R("scm", "vendor"):
        g.node("Supplier", r["vendor_code"], {
            "supplier_id": r["vendor_code"], "name": r["vendor_name"], "category": r["category"],
            "lead_time_days": int(r["lead_days"]), "payment_terms_days": int(r["pay_terms_days"]),
        }, "SCM")
    for r in R("scm", "vendor_price"):
        f = conv[r["item_code"]]
        g.link("SUPPLIES", r["vendor_code"], r["item_code"],
               {"unit_price": float(r["unit_price"]) / f, "min_order_qty": float(r["moq"]) * f})
    for r in R("scm", "purchase_order"):
        g.node("PurchaseOrder", r["po_no"], {
            "po_id": r["po_no"], "ordered_at": pos_dt(r["order_dt"]),
            "expected_at": date.fromisoformat(r["expected_date"]), "status": PO_STATUS[r["status"]],
        }, "SCM")
        g.link("ISSUED_TO", r["po_no"], r["vendor_code"])
    for r in R("scm", "po_line"):
        f = conv[r["item_code"]]
        g.link("ORDERS_ITEM", r["po_no"], r["item_code"],
               {"qty": float(r["qty"]) * f, "unit_price": float(r["unit_price"]) / f})
    for r in R("scm", "receipt"):
        f = conv[r["item_code"]]
        g.node("StockMovement", r["receipt_no"], {
            "movement_id": r["receipt_no"], "moved_at": pos_dt(r["received_dt"]), "movement_type": "입고",
            "qty": float(r["qty"]) * f, "unit_cost": float(r["unit_cost"]) / f,
        }, "SCM")
        g.link("MOVES", r["receipt_no"], r["item_code"], lineage("MOVES", r["item_code"]))
        g.link("RECEIVED_FOR", r["receipt_no"], r["po_no"])
    for r in R("scm", "stock_adjust"):
        g.node("StockMovement", r["adj_no"], {
            "movement_id": r["adj_no"], "moved_at": pos_dt(r["adj_dt"]), "movement_type": "실사조정",
            "qty": float(r["qty"]) * conv[r["item_code"]], "unit_cost": None,
        }, "SCM")
        g.link("MOVES", r["adj_no"], r["item_code"], lineage("MOVES", r["item_code"]))

    # ═══ POS ═══
    for r in R("pos", "member"):
        g.node("Customer", r["member_no"], {
            "customer_id": r["member_no"], "gender": GENDER.get(r["sex"]), "age_group": age_group(r["birth_year"]),
            "joined_at": date.fromisoformat(r["join_date"]),
        }, "POS")
    for r in R("pos", "zone"):
        g.node("Zone", r["zone_cd"], {"zone_id": r["zone_cd"], "name": r["zone_nm"], "seating": SEATING[r["seat_style"]]}, "POS")
    for r in R("pos", "table"):
        g.node("DiningTable", r["table_no"], {"table_id": r["table_no"], "capacity": int(r["seats"])}, "POS")
        g.link("IN_ZONE", r["table_no"], r["zone_cd"])
    for r in R("pos", "menu_category"):
        g.node("MenuCategory", r["category_cd"], {"category_id": r["category_cd"], "name": r["category_nm"]}, "POS")

    # 메뉴 = POS(이름·가격) + MES(레시피). 주방코드로 두 시스템을 맞춘다.
    recipes = {r["recipe_code"]: r for r in R("mes", "recipe")}
    kitchen_to_menu = {}
    for r in R("pos", "menu"):
        if r["kitchen_cd"] not in recipes:
            errors.append(f"POS 메뉴 {r['menu_cd']}의 주방코드 {r['kitchen_cd']}가 MES 레시피에 없음")
        kitchen_to_menu[r["kitchen_cd"]] = r["menu_cd"]
        g.node("MenuItem", r["menu_cd"], {
            "menu_id": r["menu_cd"], "name": r["menu_nm"], "price": int(r["sale_price"]),
            "pos_menu_code": r["menu_cd"], "mes_recipe_code": r["kitchen_cd"], "is_available": r["soldout_yn"] == "N",
        }, "POS+MES")
        g.link("IN_CATEGORY", r["menu_cd"], r["category_cd"])

    for r in R("pos", "visit"):
        g.node("Visit", r["visit_no"], {
            "visit_id": r["visit_no"], "arrived_at": pos_dt(r["arrive_dt"]), "seated_at": pos_dt(r["seat_dt"]),
            "left_at": pos_dt(r["leave_dt"]), "party_type": PARTY[r["party_cd"]], "party_size": int(r["headcount"]),
            "has_child": r["kid_yn"] == "Y", "channel": "예약" if r["rsv_yn"] == "Y" else "워크인",
        }, "POS")
        if r["member_no"]:
            g.link("MADE_VISIT", r["member_no"], r["visit_no"])
        if r["table_no"]:
            g.link("SEATED_AT", r["visit_no"], r["table_no"])
    for r in R("pos", "waiting"):
        g.node("WaitingRecord", r["wait_no"], {
            "waiting_id": r["wait_no"], "queued_at": pos_dt(r["reg_dt"]), "called_at": pos_dt(r["call_dt"]),
            "quoted_wait_min": int(r["quoted_min"]), "outcome": WAIT_OUTCOME[r["status"]],
        }, "POS")
        g.link("WAITED", r["visit_no"], r["wait_no"])
    for r in R("pos", "order"):
        g.node("Order", r["order_no"], {"order_id": r["order_no"], "ordered_at": pos_dt(r["order_dt"])}, "POS")
        g.link("PLACED", r["visit_no"], r["order_no"])
    for r in R("pos", "order_line"):
        g.link("CONTAINS", r["order_no"], r["menu_cd"], {"qty": int(r["qty"]), "unit_price": int(r["price"])})
    payments_by_close = defaultdict(list)
    for r in R("pos", "payment"):
        g.node("Payment", r["pay_no"], {
            "payment_id": r["pay_no"], "paid_at": pos_dt(r["pay_dt"]), "amount": int(r["amount"]),
            "method": PAY_METHOD[r["pay_type"]],
        }, "POS")
        g.link("PAID_BY", r["visit_no"], r["pay_no"])
        payments_by_close[r["close_no"]].append(r["pay_no"])

    # ═══ MES ═══
    for r in R("mes", "recipe_line"):
        item = material(r["material_name"], f"레시피 {r['recipe_code']}")
        menu = kitchen_to_menu.get(r["recipe_code"])
        if menu and item:
            g.link("USES", menu, item, lineage("USES", f"{r['material_name']}→{item}",
                                              {"qty_per_serving": float(r["qty"])}))
    for r in R("mes", "kds_ticket"):
        g.node("CookTicket", r["ticket_id"], {
            "ticket_id": r["ticket_id"], "station": STATION[r["station"]], "qty": int(r["qty"]),
            "started_at": mes_dt(r["start_ts"]), "completed_at": mes_dt(r["done_ts"]),
        }, "MES")
        g.link("TRIGGERED", r["pos_order_no"], r["ticket_id"], lineage("TRIGGERED", r["pos_order_no"]))
        g.link("COOKS", r["ticket_id"], kitchen_to_menu.get(r["recipe_code"], r["recipe_code"]),
               lineage("COOKS", r["recipe_code"]))
    consumption_by_close = defaultdict(list)
    for r in R("mes", "consumption"):
        item = material(r["material_name"], f"소진 {r['cons_id']}")
        g.node("StockMovement", r["cons_id"], {
            "movement_id": r["cons_id"], "moved_at": mes_dt(r["ts"]), "movement_type": MOVE_KIND[r["kind"]],
            "qty": -float(r["qty"]), "unit_cost": None,
        }, "MES")
        if item:
            g.link("MOVES", r["cons_id"], item, lineage("MOVES", f"{r['material_name']}→{item}"))
        if r["ticket_id"]:
            g.link("CONSUMED_BY", r["cons_id"], r["ticket_id"])
        consumption_by_close[r["close_no"]].append(r["cons_id"])

    # ═══ ERP ═══
    for r in R("erp", "hr_employee"):
        g.node("Employee", r["emp_no"], {
            "employee_id": r["emp_no"], "name": r["emp_name"], "role": r["position"], "employment": r["emp_type"],
            "monthly_salary": int(r["monthly_pay"]) if r["monthly_pay"] else None,
            "hourly_wage": int(r["hourly_pay"]) if r["hourly_pay"] else None,
        }, "ERP")
    for r in R("erp", "shift_master"):
        g.node("Shift", r["shift_code"], {"shift_id": r["shift_code"], "name": r["shift_name"]}, "ERP")
    attendance_by_month = defaultdict(list)
    for r in R("erp", "attendance"):
        d = erp_date(r["work_date"])
        at = lambda hm: datetime.combine(d, datetime.strptime(hm, "%H:%M").time())  # noqa: E731
        g.node("Attendance", r["att_no"], {
            "attendance_id": r["att_no"], "work_date": d, "clock_in": at(r["clock_in"]), "clock_out": at(r["clock_out"]),
        }, "ERP")
        g.link("WORKED", r["emp_no"], r["att_no"])
        g.link("OF_SHIFT", r["att_no"], r["shift_code"])
        attendance_by_month[(d.year, d.month)].append(r["att_no"])
    for r in R("erp", "chart_of_accounts"):
        g.node("Account", r["acct_code"], {"account_code": r["acct_code"], "name": r["acct_name"], "account_type": r["acct_type"]}, "ERP")
    payroll = {r["period_no"]: erp_date(r["period_start"]) for r in R("erp", "payroll")}
    for r in R("erp", "journal"):
        src = r["source_doc_no"] or None
        g.node("JournalEntry", r["je_no"], {
            "je_id": r["je_no"], "posted_on": erp_date(r["posting_date"]), "description": r["description"],
            "source_doc_no": src,
        }, "ERP")
        kind = r["source_type"]
        if kind == "SALES":                                   # ERP → POS: 일마감 번호로 하루치 결제
            for pay in payments_by_close.get(src, []):
                g.link("POSTS_SALES", r["je_no"], pay, lineage("POSTS_SALES", src))
        elif kind == "PURCHASE":                              # ERP → SCM: 발주번호
            g.link("POSTS_PURCHASE", r["je_no"], src, lineage("POSTS_PURCHASE", src))
        elif kind == "COGS":                                  # ERP → MES: 원가 마감 번호로 하루치 소진
            for mv in consumption_by_close.get(src, []):
                g.link("POSTS_COGS", r["je_no"], mv, lineage("POSTS_COGS", src))
        elif kind == "PAYROLL":                               # ERP 내부: 급여 기간의 근태
            start = payroll[src]
            for att in attendance_by_month[(start.year, start.month)]:
                g.link("POSTS_PAYROLL", r["je_no"], att)
    for r in R("erp", "journal_line"):
        if int(r["debit"]):
            g.link("DEBITS", r["je_no"], r["acct_code"], {"amount": int(r["debit"])})
        if int(r["credit"]):
            g.link("CREDITS", r["je_no"], r["acct_code"], {"amount": int(r["credit"])})

    if errors:
        raise PipelineError("원천 데이터를 온톨로지로 옮기지 못함:\n" + "\n".join(errors[:20]))
    return g


# ── 검증: 설계도를 지키는가 ──
PY_TYPE = {"string": str, "int": int, "float": (int, float), "bool": bool, "date": date, "datetime": datetime}


def conform(schema, g):
    errors = []
    objects = schema["objects"]
    for label, rows in g.nodes.items():
        if label not in objects:
            errors.append(f"설계도에 없는 객체 {label}")
            continue
        spec = objects[label]["properties"]
        for key, props in rows.items():
            for name, value in props.items():
                if name.startswith("_"):
                    continue
                p = spec.get(name)
                if p is None:
                    errors.append(f"{label}[{key}].{name}: 설계도에 없는 속성")
                elif value is None:
                    if not p.get("nullable"):
                        errors.append(f"{label}[{key}].{name}: 비어 있으면 안 됨")
                elif p["type"] == "enum":
                    if value not in p["values"]:
                        errors.append(f"{label}[{key}].{name}: 허용되지 않은 값 {value}")
                elif not isinstance(value, PY_TYPE[p["type"]]) or (p["type"] == "date" and isinstance(value, datetime)):
                    errors.append(f"{label}[{key}].{name}: 타입이 {p['type']}가 아님")
            missing = [n for n, p in spec.items() if n not in props and not p.get("nullable")]
            if missing:
                errors.append(f"{label}[{key}]: 필수 속성 없음 {missing}")
            if len(errors) > 50:
                return errors
    by_name = {l["name"]: l for l in schema["links"]}
    for name, rows in g.links.items():
        link = by_name.get(name)
        if link is None:
            errors.append(f"설계도에 없는 연결 {name}")
            continue
        src, dst = g.nodes[link["from"]], g.nodes[link["to"]]
        dangling = [(a, b) for a, b, _ in rows if a not in src or b not in dst]
        if dangling:
            errors.append(f"{name}: 끊어진 선 {len(dangling)}개 (예: {dangling[:3]})")
    return errors


# ── 적재 ──
def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def load(driver, schema, g):
    errors = conform(schema, g)
    if errors:
        raise PipelineError("설계도 위반:\n" + "\n".join(errors[:20]))
    with driver.session() as session:
        # 선이 수만 개 달린 노드(예: 양파)를 한 번에 지우면 메모리가 부족하다 → 선부터 나눠 지운다
        session.run("MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 20000 ROWS").consume()
        session.run("MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS OF 20000 ROWS").consume()
    S.apply_constraints(driver, schema)
    objects = schema["objects"]
    for label, rows in g.nodes.items():
        for chunk in _batches(list(rows.values())):
            driver.execute_query(f"UNWIND $rows AS r CREATE (n:{label}) SET n = r", rows=chunk)
    for link in schema["links"]:
        rows = [{"a": a, "b": b, "p": p} for a, b, p in g.links.get(link["name"], [])]
        fk, tk = objects[link["from"]]["key"], objects[link["to"]]["key"]
        cypher = (f"UNWIND $rows AS r MATCH (a:{link['from']} {{{fk}: r.a}}) MATCH (b:{link['to']} {{{tk}: r.b}}) "
                  f"CREATE (a)-[x:{link['name']}]->(b) SET x = r.p")
        for chunk in _batches(rows):
            driver.execute_query(cypher, rows=chunk)


def summary(g):
    return ({label: len(rows) for label, rows in g.nodes.items()},
            {name: len(rows) for name, rows in g.links.items()})


if __name__ == "__main__":
    import time

    from db import get_driver

    t0 = time.time()
    schema = S.load()
    g = build()
    nodes, links = summary(g)
    print(f"그래프 생성: 노드 {sum(nodes.values()):,}개 / 선 {sum(links.values()):,}개 ({time.time() - t0:.1f}초)")
    with get_driver() as driver:
        load(driver, schema, g)
        n = driver.execute_query("MATCH (n) RETURN count(n) AS c").records[0]["c"]
        r = driver.execute_query("MATCH ()-[r]->() RETURN count(r) AS c").records[0]["c"]
    print(f"Neo4j 적재 완료: 노드 {n:,}개 / 선 {r:,}개 ({time.time() - t0:.1f}초)")
