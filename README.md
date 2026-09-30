# 회계 계정 온톨로지 실습

재무상태표 계정을 온톨로지로 표현하고, **추론기(reasoner)가 규칙만 보고 계정을 자동 분류**하는 과정을 체험하는 실습입니다.

## 실행

```bash
.venv/bin/python accounting_ontology.py   # 실습 실행
.venv/bin/python -m pytest -q             # 추론 결과 검증
```

추론기(HermiT)는 Java로 동작하므로 `brew install openjdk`가 필요합니다.

## 핵심 개념 5가지

| 개념 | 한 줄 설명 | 이 실습에서 |
|---|---|---|
| 클래스(Class) | 개념, 즉 "종류" | 자산, 유동자산, 특수관계자 |
| 상하 관계(subClassOf) | "A는 B의 한 종류" | 유동자산 ⊂ 자산, 종속회사 ⊂ 특수관계자 |
| 속성(Property) | 개체 사이의 관계 | `has_term`(회수 기간), `has_counterparty`(거래 상대방) |
| 개체(Individual) | 실제 존재하는 하나하나 | 현금, 매출채권, 자회사B |
| 정의 규칙(equivalent_to) | "조건을 만족하면 곧 이것" | 자산이면서 1년 이내 회수되면 곧 유동자산 |

## 실행하면 보이는 것

1. **자동 분류**: 계정에 "자산 + 1년 이내"만 입력했는데, 추론기가 "유동자산"으로 분류합니다.
2. **여러 단계 추론**: 대여금의 상대방인 자회사B를 "종속회사"라고만 입력했습니다. 그런데도 추론기는 두 규칙을 이어서 이 계정을 찾아냅니다.
   - 종속회사는 특수관계자에 속한다
   - 특수관계자와 거래한 계정은 특수관계자 계정이다

   그 결과 대여금이 "특수관계자 계정(추가 검토 대상)"으로 분류됩니다.
3. **열린 세계 가정(Open World Assumption)**: 선급금은 기간 정보가 없어서 유동으로도 비유동으로도 분류되지 않습니다. DB에서는 "정보 없음 = 아님"이지만, 온톨로지에서는 "정보 없음 = 모름"입니다.
4. **모순 탐지**: 자산·부채·자본을 서로 겹칠 수 없다고(`AllDisjoint`) 선언해 두었기 때문에, "자산이면서 부채"인 계정을 넣으면 추론기가 거부합니다.

## 직접 해 볼 과제

`accounting_ontology.py`를 고쳐 가며 해 보세요. 쉬운 순서로 정리했습니다.

1. **계정 추가**: `add_accounts`에 `재고자산`(자산, 1년 이내)을 넣고, 유동자산으로 분류되는지 확인합니다.
2. **선급금 분류**: 선급금에 `has_term=short_term`을 추가하면 분류 결과가 어떻게 바뀌는지 봅니다.
3. **새 개념 추가**: `AssociateCompany`(관계기업)를 `RelatedParty`의 하위 클래스로 만듭니다. 관계기업과 거래한 매출채권이 특수관계자 계정으로 잡히는지 확인합니다.
4. **새 규칙 작성**: 1년 이내 상환 + 특수관계자 거래인 부채를 "단기 특수관계자 부채"로 정의합니다. 힌트는 `Liability & has_term.value(short_term) & has_counterparty.some(RelatedParty)`입니다.
5. **일부러 모순 만들기**: `has_term`은 `FunctionalProperty`(값이 하나뿐인 속성)입니다. 한 계정에 1년 이내와 1년 초과를 동시에 넣으면 어떻게 되는지 확인합니다.
6. **Protégé로 열기**: 실행하면 생기는 `accounting.owl`을 [Protégé](https://protege.stanford.edu)에서 엽니다. `Reasoner → HermiT → Start reasoner`를 눌러 같은 결과를 GUI로 확인합니다.

## 그래프 DB와 그림

```bash
.venv/bin/python graph_db.py     # 온톨로지를 그래프 DB(트리플)에 넣고 SPARQL로 질문
.venv/bin/python graph_view.py   # 그림 파일 graph.html 생성 → 더블클릭해서 브라우저로 열기
```

- `graph_db.py`: 추론 결과까지 (주어, 관계, 대상) 묶음으로 저장하고 질문 4개를 던집니다.
- `graph_view.py` + `graph_template.html`: 같은 내용을 점과 선으로 그립니다.
  - 회색 선은 입력한 사실, 초록 선은 추론기가 찾아낸 결론, 점선은 개념 사이의 규칙입니다.
  - 「추론 결과 보기」를 끄면 추론기를 돌리기 전 상태로 돌아갑니다.
  - 「그래프에 질문하기」 버튼을 누르면 SPARQL 질문의 답에 해당하는 점이 밝아집니다.
  - 점을 누르면 그 점의 사실과 결론이 오른쪽에 나옵니다.
- 실행할 때마다 `accounting.owl`, `accounting_inferred.owl`이 새로 만들어집니다(git 추적 제외).
