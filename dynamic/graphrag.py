"""Dynamic 레이어 — GraphRAG AI 매니저.

질문 → LLM이 '도구'를 골라 호출(온톨로지 Function·시나리오·읽기 전용 Cypher) → 그 결과만 근거로 답한다.
  - 근거: 호출한 도구와 인자, 결과를 evidence로 함께 돌려준다(화면에서 확인 가능)
  - 쓰기 금지: Cypher는 키워드 차단 + 읽기 전용 트랜잭션의 2중 안전장치
  - 액션: propose_action으로 '제안'만 한다. 실행은 사람이 화면에서 승인할 때 kinetic.actions가 한다
"""
import json
import os
import re
from datetime import date
from itertools import islice
from pathlib import Path

import neo4j
from dotenv import load_dotenv

from dynamic import decision as D
from dynamic import forecast as FC
from dynamic import scenario as SC
from kinetic import functions as F
from semantic import schema as S

ROOT = Path(__file__).resolve().parents[1]
MAX_STEPS = 6            # 도구 호출 라운드 상한
MAX_ROWS = 50            # Cypher 결과 행 상한
MAX_TOOL_CHARS = 6000    # LLM에 돌려주는 도구 결과 길이 상한
DATA_PERIOD = ("2026-07-01", "2026-09-30")

# ── 온톨로지 설명(시스템 프롬프트) ──
RULES = f"""## 해석 규칙
- 데이터 기간 {DATA_PERIOD[0]} ~ {DATA_PERIOD[1]}. 기준일을 말하지 않으면 {DATA_PERIOD[1]}로 본다.
- 점심/저녁: Visit의 arrived_at.hour < 16 이면 점심, 아니면 저녁.
- 착석한 방문은 seated_at IS NOT NULL, 대기 중 이탈은 seated_at IS NULL.
- 재료 수량은 기준단위 g·ml·ea, 단가는 원/기준단위. StockMovement.qty는 입고 +, 소진·폐기 −.
- 매출 = (:Order)-[c:CONTAINS]->(:MenuItem)의 c.qty * c.unit_price 합계 또는 Payment.amount 합계. 금액 단위는 원.
- datetime 속성 비교는 localdatetime('2026-09-01T00:00') 형식을 쓴다. 날짜만 비교할 땐 date(x.arrived_at).
- 모든 노드의 _system 속성은 출처 시스템(POS·MES·SCM·ERP·ONTOLOGY). 시스템 간 연결의 _rule·_join_key는 연결 근거.
"""

INSTRUCTIONS = """너는 한식당 '한솥밥상' 사장님의 AI 매니저다. 아래 온톨로지(그래프)만이 사실의 출처다.
- 숫자는 반드시 도구 결과에서 가져온다. 추측하거나 지어내지 않는다. 결과가 없으면 없다고 말한다.
- 원인·이유는 데이터로 확인한 것만 단정한다. 데이터로 확인하지 못한 해석은 '추정'이라고 밝힌다.
- 방문(Visit)은 팀 단위다. 사람 수는 party_size 합계다.
- 먼저 알맞은 전용 도구를 쓰고, 맞는 도구가 없을 때만 run_cypher(읽기 전용)를 쓴다.
- 답은 한국어로 짧게: 결론 → 핵심 숫자(단위 포함) → 근거(어떤 데이터를 봤는지) → 필요하면 제안.
- 가격 변경·품절·발주가 필요해 보이면 propose_action으로 제안만 한다. 실행했다고 말하지 않는다.
"""


def describe_ontology(schema):
    lines = ["## 객체 (라벨(한글) [뒷받침 시스템] 키: 속성)"]
    for label, obj in schema["objects"].items():
        props = []
        for name, p in obj["properties"].items():
            t = "{" + "|".join(p["values"]) + "}" if p["type"] == "enum" else p["type"]
            props.append(f"{name}:{t}")
        lines.append(f"- {label}({obj['label_ko']}) [{'+'.join(obj['backing'])}] 키 {obj['key']}: {', '.join(props)}")
    lines.append("## 연결")
    for link in schema["links"]:
        extra = ""
        if link.get("cross_system"):
            extra = f" [시스템 간 {'→'.join(link['systems'])}]"
        if link.get("properties"):
            extra += " {" + ", ".join(link["properties"]) + "}"
        lines.append(f"- (:{link['from']})-[:{link['name']}]->(:{link['to']}) {link['label_ko']}{extra}")
    return "\n".join(lines) + "\n" + RULES


# ── 읽기 전용 Cypher ──
WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|GRANT|DENY|REVOKE|ALTER|RENAME|LOAD\s+CSV|"
    r"IN\s+TRANSACTIONS|apoc\.(create|merge|refactor|periodic|load|cypher|do|nodes|trigger|schema)|db\.create|dbms\.)",
    re.IGNORECASE)


def is_read_only(query: str) -> bool:
    no_comments = re.sub(r"//.*?$|/\*.*?\*/", "", query, flags=re.MULTILINE | re.DOTALL)
    return WRITE.search(no_comments) is None


def _read_query(driver, query):
    """읽기 전용 세션에서 실행 — 키워드 필터를 우회한 쓰기도 DB가 거부한다."""
    @neo4j.unit_of_work(timeout=15)
    def work(tx):
        return [r.data() for r in islice(tx.run(query), MAX_ROWS + 1)]

    with driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
        return session.execute_read(work)


def run_cypher(driver, query: str):
    if not is_read_only(query):
        return {"error": "읽기 전용 질의만 허용됩니다(CREATE·MERGE·SET·DELETE 등 금지)."}
    try:
        rows = _read_query(driver, query)
    except Exception as e:  # noqa: BLE001 — 오류 내용을 LLM에 돌려줘 스스로 고치게 한다
        return {"error": str(e)[:400]}
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


# ── 도구 ──
def _d(s):
    return date.fromisoformat(s)


def _turnover_summary(driver, start, end):
    """일별 회전율을 요일·시간대 평균으로 요약(결과가 길어지지 않게)."""
    acc = {}
    for r in F.table_turnover(driver, _d(start), _d(end)):
        key = ("월화수목금토일"[r["date"].weekday()], r["slot"])
        acc.setdefault(key, []).append(r["table_turns"])
    return [{"weekday": k[0], "slot": k[1], "avg_table_turns": sum(v) / len(v), "days": len(v)}
            for k, v in sorted(acc.items())]


DATE = {"type": "string", "description": "YYYY-MM-DD"}
PERIOD = {"start": DATE, "end": DATE}
PROPOSALS_KEY = "_proposals"

TOOLS = {
    "run_cypher": (lambda d, query: run_cypher(d, query),
                   "온톨로지에 읽기 전용 Cypher를 실행한다(최대 50행). 전용 도구로 안 될 때만.", {"query": {"type": "string"}}),
    "menu_costs": (lambda d, as_of: F.menu_costs(d, _d(as_of)),
                   "메뉴별 원가·마진·원가율(기준일까지 최근 매입단가 기준)", {"as_of": DATE}),
    "menu_engineering": (lambda d, start, end: F.menu_engineering(d, _d(start), _d(end)),
                         "기간 메뉴 엔지니어링 4분면(Star·Plowhorse·Puzzle·Dog), 주류·음료 제외", PERIOD),
    "table_turnover_summary": (_turnover_summary, "요일·점심/저녁별 평균 테이블 회전율", PERIOD),
    "waiting_stats": (lambda d, start, end: F.waiting_stats(d, _d(start), _d(end)),
                      "동행유형별 방문 팀 수(visits, 사람 수 아님)·이탈 팀 수·대기 이탈률·착석 팀 평균 대기시간(분)", PERIOD),
    "monthly_cost_ratio": (lambda d: F.monthly_cost_ratio(d), "월별 매출·식재료비·원가율(ERP 전표)", {}),
    "usage_variance": (lambda d, start, end: F.usage_variance(d, _d(start), _d(end)),
                       "재료별 레시피 대비 과다사용률·폐기율", PERIOD),
    "stock_status": (lambda d, as_of: F.stock_status(d, _d(as_of)),
                     "기준일 재고·하루 사용량·소진 예상일·안전재고 미달", {"as_of": DATE}),
    "segment_summary": (lambda d, as_of: F.segment_summary(d, _d(as_of)),
                        "기준일 고객 세그먼트별 회원 수(신규·재방문·단골·VIP·이탈위험)", {"as_of": DATE}),
    "forecast_menu_demand": (lambda d, target_date: FC.forecast_menu_demand(d, _d(target_date)),
                             "특정 날짜 메뉴별 예상 판매량(같은 요일 최근 4주 평균)", {"target_date": DATE}),
    "ingredient_requirements": (lambda d, target_date, as_of: FC.ingredient_requirements(d, _d(target_date), _d(as_of)),
                                "예측 수요 기준 재료 필요량·부족분·추천 발주량·최저가 업체",
                                {"target_date": DATE, "as_of": DATE}),
    "price_shock": (lambda d, ingredient, pct, start, end: SC.price_shock(d, ingredient, float(pct), _d(start), _d(end)),
                    "What-if: 재료 단가가 pct(0.4 = +40%)만큼 변하면 메뉴 원가·마진, impact = 기간 판매량 기준 원가 증가액(원)",
                    {"ingredient": {"type": "string", "description": "재료 이름 또는 id"},
                     "pct": {"type": "number"}, **PERIOD}),
    "supplier_switch": (lambda d, ingredients, supplier_id, start, end:
                        SC.supplier_switch(d, ingredients, supplier_id, _d(start), _d(end)),
                        "What-if: 재료들을 다른 공급업체로 바꾸면 기간 절감액(음수 = 절감)",
                        {"ingredients": {"type": "array", "items": {"type": "string"}},
                         "supplier_id": {"type": "string"}, **PERIOD}),
    "sales_before_after": (lambda d, menu_id, pivot, days=14: D.sales_before_after(d, menu_id, _d(pivot), int(days)),
                           "메뉴의 기준일 전후 days일 판매량·매출 비교",
                           {"menu_id": {"type": "string"}, "pivot": DATE, "days": {"type": "integer"}}),
    "propose_action": (None, "사장님께 액션을 제안한다(실행하지 않음). change_price{menu_id,new_price}, "
                             "set_availability{menu_id,available}, create_purchase_order{supplier_id,lines{재료id:수량}}",
                       {"action_type": {"type": "string",
                                        "enum": ["change_price", "set_availability", "create_purchase_order"]},
                        "args": {"type": "object"}, "reason": {"type": "string"}}),
}

OPTIONAL = {"sales_before_after": {"days"}}


def tool_specs():
    specs = []
    for name, (_, desc, props) in TOOLS.items():
        required = [p for p in props if p not in OPTIONAL.get(name, set())]
        specs.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return specs


def _call_tool(driver, name, args, proposals):
    if name not in TOOLS:
        return {"error": f"알 수 없는 도구: {name}"}
    if name == "propose_action":
        proposals.append({"action_type": args.get("action_type"), "args": args.get("args", {}),
                          "reason": args.get("reason", "")})
        return {"status": "제안됨(사장님 승인 대기, 아직 실행 안 됨)"}
    try:
        return TOOLS[name][0](driver, **args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:400]}


def _to_llm(result):
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + " …(잘림)"


# ── LLM ──
class OpenAIChat:
    def __init__(self, model=None):
        from openai import OpenAI

        load_dotenv(ROOT / ".env")
        self.client = OpenAI()
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    def __call__(self, messages, tools):
        return self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                   temperature=0).choices[0].message


def ask(driver, question, llm=None, history=()):
    """질문 하나에 답한다. 반환: {answer, evidence:[{tool, args, result}], proposals:[...]}"""
    llm = llm or OpenAIChat()
    system = INSTRUCTIONS + "\n" + describe_ontology(S.load())
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]
    evidence, proposals = [], []
    for _ in range(MAX_STEPS):
        msg = llm(messages, tool_specs())
        if not msg.tool_calls:
            return {"answer": msg.content or "", "evidence": evidence, "proposals": proposals}
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in msg.tool_calls]})
        for c in msg.tool_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _call_tool(driver, c.function.name, args, proposals)
            evidence.append({"tool": c.function.name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": _to_llm(result)})
    return {"answer": f"도구 호출이 {MAX_STEPS}단계를 넘어 멈췄습니다. 질문을 더 구체적으로 해 주세요.",
            "evidence": evidence, "proposals": proposals}
