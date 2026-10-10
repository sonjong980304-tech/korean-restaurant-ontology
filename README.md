# 한식당 온톨로지 실습 — 팔란티어 방식

72석 한식당 '한솥밥상'의 3개월(2026-07~09) 영업을 온톨로지로 만들고, 사장님의 의사결정을 돕는 웹까지 단계별로 쌓아 가는 실습입니다.

## 큰 그림

| 레이어 | 비유 | 폴더 | 상태 |
|---|---|---|---|
| **Semantic** (의미) | 가게의 지도 — 무엇이 있고 어떻게 연결되나 | `semantic/` | ✅ 2·3단계 완료 |
| **Kinetic** (운동) | 가게의 손발 — 지표 계산(Function)과 행동(Action) | `kinetic/` | ✅ 4단계 Function · 5단계 Action |
| **Dynamic** (동적) | 가게의 두뇌 — 예측·시뮬레이션·AI 매니저(GraphRAG) | `dynamic/` | ✅ 6단계 예측·시나리오·결정 · 7단계 GraphRAG |
| 앱 | 사장님 대시보드(Streamlit) | `app/` | ✅ 8단계 |

## 원천 시스템 4개 → 온톨로지 하나

팔란티어 방식은 기존 시스템을 고치지 않습니다. 그 위에 온톨로지를 얹어 **서로 다른 시스템을 연결**합니다.

| 시스템 | 음식점에서 | 데이터 |
|---|---|---|
| POS | 포스기·웨이팅 앱 | 회원, 방문, 대기, 테이블, 주문, 결제, 메뉴(가격) |
| MES | 주방 모니터(KDS) | 레시피, 조리지시, 재료 소진·폐기 |
| SCM | 발주·재고 | 재료, 공급업체, 발주, 입고, 실사 |
| ERP | 회계·급여 | 계정과목, 전표, 직원, 근무조, 근태 |

시스템끼리는 서로를 모릅니다. 생성기는 일부러 형식을 다르게 만들고, 파이프라인이 이를 풀어냅니다.

| 차이 | 예 | 푸는 곳 |
|---|---|---|
| 코드 | POS `P-101` / MES `R-JJ-KIMCHI` / SCM `VEG-ONION-1KG` | 주방코드, 매핑표 |
| 이름 | MES `양파` / SCM `양파(국산) 1kg망` | `semantic/mappings/material_map.csv` |
| 단위 | MES g·ml / SCM kg·L | 파이프라인에서 기준단위(g·ml·ea)로 환산 |
| 코드값 | `M`/`F`, 출생연도, `FAMILY` | 남/여, 연령대, 가족으로 번역 |
| 시각 | `2026-07-01 12:03:00` / `20260701T120300` / `2026/07/01` | 형식 통일 |

**계보(lineage)**: 시스템을 넘나드는 선 7종(USES·TRIGGERED·COOKS·MOVES·POSTS_SALES·POSTS_PURCHASE·POSTS_COGS)에는
`_rule`(schema.yaml의 join 규칙)과 `_join_key`(실제로 쓴 키값, 예: `CL-20260904`, `양파→VEG-ONION-1KG`)가 붙습니다.
Neo4j 화면에서 선을 클릭하면 "이 선이 왜 생겼는지"를 바로 확인할 수 있습니다(`queries.cypher` ⑤).

## 실행

```bash
# 0) Neo4j (이 실습 전용: 화면 http://localhost:7475, 접속 bolt://localhost:7688)
#    비밀번호는 .env 의 NEO4J_PASSWORD. 설정은 docker-compose.yml 참고
.venv/bin/python db.py                    # 접속 확인

# 1) 원천 데이터 생성 → data/raw/{pos,mes,scm,erp}/*.csv (seed 고정, 약 1초)
.venv/bin/python sources/generate.py

# 2) 온톨로지로 묶어 Neo4j에 적재 (약 30초)
.venv/bin/python semantic/pipeline.py

# 3) 테스트
.venv/bin/python -m pytest -q tests
```

**Neo4j 화면 로그인**: http://localhost:7475 → Connect URL `bolt://localhost:7688`, 아이디 `neo4j`, 비밀번호는 `.env`의 `NEO4J_PASSWORD`.
(7687은 다른 Neo4j의 주소입니다. 접속이 안 되면 Connect URL이 7688인지 먼저 확인하세요.)

Neo4j 화면에서 `queries.cypher`의 예시 질문 4개를 붙여 넣어 실행해 보세요.
(양파 → 메뉴 → 매출 / 전표 → 결제 → 고객 / 근태 → 조리시간 / 동행유형 → 이탈률)

## 파일

| 파일 | 하는 일 |
|---|---|
| `semantic/profile.yaml` | 확정한 가게 규모(테이블 18·메뉴 24·재료 40·업체 6·직원 10·근무조 3) |
| `semantic/schema.yaml` | **설계도(유일한 원본)**: 객체 19종, 연결 25종, 객체마다 뒷받침하는 시스템, 시스템 간 join 규칙 |
| `semantic/schema.py` | 설계도 모순 검사 + Neo4j 제약조건 적용 |
| `semantic/pipeline.py` | 원천 4개 → 정규화 → 연결 → 설계도 검증 → Neo4j 적재 |
| `semantic/mappings/material_map.csv` | 주방 재료명 ↔ SCM 품목코드 매핑표(온톨로지 팀이 관리하는 파일) |
| `sources/master.yaml` | 가게의 '진짜' 기준정보(메뉴·레시피·재료·직원). **파이프라인은 이 파일을 보지 않음** |
| `sources/generate.py` | 4개 시스템의 가상 데이터 생성기 |

## Kinetic Function — 읽기 전용 지표 (`kinetic/functions.py`)

온톨로지를 읽어 계산만 하고 데이터는 바꾸지 않습니다. 모두 `driver`와 기간(또는 기준일)을 받습니다.

| 함수 | 답하는 질문 | 계산 방법 |
|---|---|---|
| `menu_costs(as_of)` | 메뉴 하나 팔면 얼마 남나? | 레시피 사용량 × 기준일까지 가장 최근 매입단가 |
| `menu_engineering(start, end)` | 어떤 메뉴를 밀고 어떤 메뉴를 뺄까? | Kasavana–Smith 4분면: 인기(판매비중 ≥ 0.7 ÷ 메뉴 수) × 마진(≥ 판매 가중평균) → Star·Plowhorse·Puzzle·Dog (주류·음료 제외) |
| `table_turnover(start, end)` | 테이블이 몇 번 돌았나? | 날짜·점심/저녁별 착석 팀 ÷ 테이블 수, 착석 인원 ÷ 좌석 수 |
| `waiting_stats(start, end)` | 누구를 놓치나? | 동행유형별 대기 이탈률, 착석 팀 평균 대기시간 |
| `monthly_cost_ratio()` | 원가율이 오르나? | ERP 전표: 식재료비 차변 ÷ 수익 계정 대변 |
| `usage_variance(start, end)` | 레시피보다 더 쓰나, 얼마나 버리나? | 조리지시 기준 레시피량 vs 실제 소진, 폐기 ÷ 전체 사용(반찬 포함) |
| `stock_status(as_of)` | 무엇이 곧 떨어지나? | 재고이동 합계, 최근 7일 하루 평균 사용, 소진 예상일, 안전재고 미달 |
| `customer_segments(as_of)` | 단골·VIP는 누구, 떠나는 손님은? | 아래 규칙(착석한 방문만 셈) |

고객 세그먼트 규칙(위에서부터 우선): **이탈위험** 마지막 방문 시점엔 단골이었는데 30일 이상 미방문 → **단골** 최근 90일 4회 이상 + 마지막 방문 30일 이내 → **VIP** 단골 중 1회 평균 결제액 상위 20% → **재방문** 누적 2회 이상 → **신규** 1회.

## 테스트 구조

| 파일 | 대상 DB | 확인하는 것 |
|---|---|---|
| `test_schema.py`, `test_pipeline.py` | 본 Neo4j(7688) | 설계도·파이프라인·계보·적재 |
| `test_functions.py` + `conftest.py` | **테스트 전용 Neo4j(7689)** | 손으로 정답을 계산한 작은 가게로 각 함수의 정답 검증(TDD) |
| `test_kinetic_acceptance.py` | 본 Neo4j(7688) | 실데이터에서 '심어 둔 패턴'이 잡히는지 |
| `test_actions.py`, `test_dynamic.py` | 테스트 전용 Neo4j(7689) | 액션 검증·기록·write-back 재생, 예측·시나리오·결정 |
| `test_graphrag.py` | 테스트 전용 Neo4j(7689) | 가짜 LLM으로 에이전트 루프·제안·오류, 읽기 전용 안전장치(실호출 1개는 `RUN_LIVE_LLM=1` + 본 DB) |
| `test_app.py` | 본 Neo4j(7688) | Streamlit AppTest로 6개 화면 렌더

Docker 메모리가 2GB 이하면 두 Neo4j를 동시에 띄울 수 없습니다(메모리 부족으로 종료됨). 번갈아 실행하세요.

```bash
# 테스트 전용 Neo4j 최초 1회 생성(저장소 없음, 끄면 데이터 사라짐)
docker run -d --name restaurant-neo4j-test -p 127.0.0.1:7689:7687 -e NEO4J_AUTH="neo4j/<.env의 비밀번호>" \
  -e NEO4J_server_memory_heap_max__size=384M -e NEO4J_server_memory_pagecache_size=64M neo4j:5-community

docker stop restaurant-neo4j && docker start restaurant-neo4j-test
.venv/bin/python -m pytest -q tests/test_functions.py tests/test_actions.py tests/test_dynamic.py tests/test_graphrag.py
docker stop restaurant-neo4j-test && docker start restaurant-neo4j
RUN_LIVE_LLM=1 .venv/bin/python -m pytest -q tests        # 나머지(테스트 DB용은 자동으로 건너뜀)
```

## Kinetic Action — 데이터를 바꾸는 행동 (`kinetic/actions.py`)

| 액션 | 거부 규칙 | 실행하면 |
|---|---|---|
| `change_price` 가격 변경 | 새 가격 ≤ 원가, 한 번에 ±30% 초과 | MenuItem.price 변경 |
| `set_availability` 품절·판매 재개 | 이미 그 상태 | MenuItem.is_available 변경 |
| `create_purchase_order` 발주 | 그 업체가 안 파는 재료, 수량 ≤ 0, 합계 300만 원 초과 | PurchaseOrder(요청) + 품목 + 업체 연결 |

- 실행·거부 모두 `ActionLog` 노드로 남고 대상과 연결됩니다(감사 추적).
- **write-back**: 액션·시나리오·결정은 `data/writeback/events.jsonl`에도 한 줄씩 쌓입니다. `semantic/pipeline.py`로 원천에서
  다시 적재하면 마지막에 이 기록을 같은 id·시각으로 재생해 그대로 복원합니다(팔란티어의 write-back 데이터셋 개념).

## Dynamic — 예측·시나리오·의사결정·AI (`dynamic/`)

| 파일 | 내용 |
|---|---|
| `forecast.py` | 같은 요일 최근 N주 평균으로 메뉴 수요 예측 → 레시피로 재료 필요량 → 재고 대비 추천 발주량(필요 + 안전재고 − 재고)과 최저가 업체 |
| `scenario.py` | What-if: 원가 충격(재료 단가 ±%) → 메뉴별 원가·마진·기간 원가 증가액 / 업체 변경 → 기간 절감액. `save_scenario`로 Scenario 노드 저장 |
| `decision.py` | 결정 기록: Decision —근거→ Scenario, —실행→ ActionLog. 결정 추적, 기준일 전후 판매 비교 |
| `graphrag.py` | AI 매니저: OpenAI 함수호출(기본 `gpt-4.1-mini`, `OPENAI_MODEL`로 변경). 도구 = 위 함수들 + 읽기 전용 Cypher + 액션 제안 |

AI 매니저의 원칙: 숫자는 도구 결과에서만, 근거(호출 도구·인자·결과)를 함께 보여 줌, Cypher는 **쓰기 키워드 차단 + 읽기 전용
트랜잭션** 2중 안전장치, 액션은 **제안만**(실행은 화면의 승인 버튼). 시스템 프롬프트는 `schema.yaml`과 DB의 기준정보(업체·재료·메뉴)로 자동 생성.

## 앱 (Streamlit)

```bash
.venv/bin/streamlit run app/main.py --server.port 8766    # http://localhost:8766
```

| 메뉴 | 레이어 | 내용 |
|---|---|---|
| 개요 | - | 월 매출·원가율·회전율·놓친 팀·이탈위험, 재고 경보, 지금 볼 것 |
| 온톨로지 탐색 | Semantic | 설계도 그래프(색 = 원천 시스템), 객체 한 건의 주변 그래프(선에 마우스 → 계보), 시스템별 데이터 양 |
| 운영 현황 | Kinetic Function | 메뉴 엔지니어링 4분면, 회전율 히트맵, 이탈률, 원가율, 로스·재고, 고객 세그먼트 |
| 액션 | Kinetic Action | 가격·품절·발주 실행(검증 메시지), 실행 기록 |
| 예측·시뮬레이션 | Dynamic | 수요 예측과 추천 발주 실행, 원가 충격, 업체 변경, 의사결정 기록, 전후 비교 |
| AI 매니저 | Dynamic | 채팅 + 근거 펼쳐 보기 + 제안 승인 실행 |

색은 dataviz 검증 팔레트(다크 표면에서 색각 이상·대비 검사 통과)를 원천 시스템별로 고정해 씁니다.

## 고객은 3개 축으로 나눈다

| 축 | 어디에 | 값 |
|---|---|---|
| 누구인가 | 고객 | 성별, 연령대 — 비회원(약 40%)은 '모름' |
| 누구와 왔나 | 방문 | 혼밥·연인·친구·가족·회식·비즈니스, 인원, 아이 동반 |
| 얼마나 자주 | 계산 결과(4단계) | 신규·재방문·단골·VIP·이탈위험 |

같은 사람이 평일엔 혼밥, 주말엔 가족과 옵니다. 그래서 동행유형은 사람이 아니라 방문에 붙입니다.

## 심어 둔 패턴 (주의: 분석으로 '발견'하는 것은 여기 적힌 것을 다시 찾는 것)

가상 데이터이므로 실제 경영 인사이트로 착각하지 않도록 목록을 공개합니다. 상세 수치는 `sources/generate.py` 상단에 있습니다.

1. 동행유형 비율이 평일/주말·점심/저녁마다 다름 (금요일 저녁 회식↑, 주말 가족↑)
2. 체류시간: 회식(약 110분) > 가족 > 연인 > 친구 > 비즈니스 > 혼밥
3. 점심 손님은 덜 기다림 → 대기 이탈
4. 동행유형별 메뉴 취향 (가족→불고기·갈비탕, 회식→보쌈·파전+술)
5. 술: 회식 1인당 1.8병
6. 계절: 냉면·국수는 7~8월↑ 9월↓, 갈비탕·순두부는 반대
7. 단가 인상: 양파 +40%(9/1), 돼지앞다리 +15%(8/15), 배추 +60%(8/10) → 9월 원가율 상승
8. 충성 회원 일부가 8/25 이후 발길을 끊음(이탈 단골)
9. 주방 인원 부족 → 조리시간 증가 (화·수 점심, 목 저녁)
10. 홀 아르바이트가 적은 평일 → 테이블 정리 지연
11. 레시피보다 평균 3% 더 씀, 저녁 제육·불고기는 8%
12. 유통기한 4일 이하 재료(상추·콩나물·소불고기·바지락) 폐기율이 높음(약 7~8%, 나머지 냉장 채소 1~4%)
13. (결과로 생긴 패턴) 룸이 4개뿐이라 회식 손님 이탈률이 약 37%

## 단순화한 가정

- 부가세, 4대보험, 주휴수당, 카드수수료는 생략
- 재고가 바닥나는 상황(품절)은 일어나지 않게 발주 정책을 넉넉히 잡음
- 원가는 '그날 주거래처 단가 × 사용량'으로 계산(이동평균 등 원가흐름 가정 미적용)
- 테이블 합석·붙이기 없음
