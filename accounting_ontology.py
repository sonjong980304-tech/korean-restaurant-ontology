"""회계 계정 온톨로지 실습.

실행: .venv/bin/python accounting_ontology.py

  1단계: 개념(클래스)과 상하 관계 정의 — "유동자산은 자산의 한 종류"
  2단계: 관계(속성)와 정의 규칙 정의 — "1년 내 회수되는 자산 = 유동자산"
  3단계: 실제 계정(개체) 입력 — 유동/비유동 분류는 일부러 하지 않음
  4단계: 추론기 실행 → 규칙만 보고 자동 분류되는지 확인
  5단계: 모순 탐지 — "자산이면서 부채인 계정"을 넣으면 추론기가 거부하는지 확인
"""
import os

import owlready2
from owlready2 import (
    AllDifferent,
    AllDisjoint,
    FunctionalProperty,
    ObjectProperty,
    OwlReadyInconsistentOntologyError,
    Thing,
    ThingClass,
    World,
    destroy_entity,
    sync_reasoner,
)

# 추론기(HermiT)는 Java로 돌아간다. Homebrew openjdk가 있으면 그것을 쓴다.
HOMEBREW_JAVA = "/opt/homebrew/opt/openjdk/bin/java"
if os.path.exists(HOMEBREW_JAVA):
    owlready2.JAVA_EXE = HOMEBREW_JAVA

IRI = "http://example.org/accounting.owl"


def build_schema():
    """1~2단계: 개념·관계·규칙(스키마)을 만든다. 아직 실제 계정은 없다."""
    world = World()  # 실행마다 깨끗한 저장소를 쓰도록 분리
    onto = world.get_ontology(IRI)

    with onto:
        # ── 1단계: 개념과 상하 관계 ──
        class Account(Thing):
            label = ["계정"]

        class Asset(Account):
            label = ["자산"]

        class Liability(Account):
            label = ["부채"]

        class Equity(Account):
            label = ["자본"]

        # 자산·부채·자본은 서로 겹칠 수 없다 (5단계 모순 탐지의 근거)
        AllDisjoint([Asset, Liability, Equity])

        class Term(Thing):
            label = ["기간 구분"]

        class Entity(Thing):
            label = ["거래 상대방"]

        class RelatedParty(Entity):
            label = ["특수관계자"]

        class Subsidiary(RelatedParty):
            label = ["종속회사"]

        class AssociateCompany(RelatedParty):
            label = ["관계기업"] 

        # ── 2단계: 관계(속성) ──
        class has_term(Account >> Term, FunctionalProperty):
            label = ["회수·상환 기간"]

        class has_counterparty(Account >> Entity, ObjectProperty):
            label = ["거래 상대방"]

        short_term = Term("short_term", label=["1년 이내"])
        long_term = Term("long_term", label=["1년 초과"])
        # 이름이 다르다고 다른 것이라 가정하지 않는 게 OWL의 기본이라, 명시해 준다
        AllDifferent([short_term, long_term])

        # ── 2단계: 정의 규칙(동치 클래스) ──
        # "A = B" 형태라서 조건을 만족하는 개체는 추론기가 자동으로 A로 분류한다
        class CurrentAsset(Asset):
            label = ["유동자산"]
            equivalent_to = [Asset & has_term.value(short_term)]

        class NonCurrentAsset(Asset):
            label = ["비유동자산"]
            equivalent_to = [Asset & has_term.value(long_term)]

        class CurrentLiability(Liability):
            label = ["유동부채"]
            equivalent_to = [Liability & has_term.value(short_term)]

        class NonCurrentLiability(Liability):
            label = ["비유동부채"]
            equivalent_to = [Liability & has_term.value(long_term)]

        class RelatedPartyAccount(Account):
            label = ["특수관계자 계정(추가 검토 대상)"]
            equivalent_to = [Account & has_counterparty.some(RelatedParty)]

    return onto


def add_accounts(onto):
    """3단계: 실제 계정을 넣는다. 자산/부채/자본까지만 알려주고 세부 분류는 맡긴다."""
    with onto:
        short_term, long_term = onto.short_term, onto.long_term

        customer_a = onto.Entity("customer_a", label=["거래처A"])
        bank = onto.Entity("bank_c", label=["은행C"])
        # 자회사B를 '특수관계자'라고 직접 말하지 않았다. 종속회사 ⊂ 특수관계자 규칙으로 추론된다.
        subsidiary_b = onto.Subsidiary("subsidiary_b", label=["자회사B"])

        associate_d = onto.AssociateCompany("associate_d", label=["관계기업D"])

        onto.Asset("cash", label=["현금"], has_term=short_term)
        onto.Asset("accounts_receivable", label=["매출채권"],
                   has_term=short_term, has_counterparty=[customer_a])
        onto.Asset("loan_to_subsidiary", label=["대여금(자회사B)"],
                   has_term=long_term, has_counterparty=[subsidiary_b])
        onto.Asset("ar_associate", label=["매출채권(관계기업D)"],
                   has_term=short_term, has_counterparty=[associate_d])
        onto.Asset("building", label=["건물"], has_term=long_term)
        # 특수관계자 거래 없음: has_counterparty를 일부러 넣지 않았다
        onto.Asset("tangible_assets", label=["유형자산"], has_term=long_term)
        # 기간 정보가 없는 계정: 추론기는 모르는 건 모른다고 둔다(열린 세계 가정)
        onto.Asset("inventory", label=["재고자산"], has_term=short_term)
        onto.Asset("prepayment", label=["선급금"])
        onto.Liability("accounts_payable", label=["매입채무"], has_term=short_term)
        onto.Liability("long_term_borrowings", label=["장기차입금"],
                       has_term=long_term, has_counterparty=[bank])
        onto.Equity("share_capital", label=["자본금"])


def reason(onto):
    """4단계: 추론기를 돌려 추론 결과를 온톨로지에 반영한다."""
    with onto:
        sync_reasoner(onto, debug=0)


def class_labels(individual):
    return [c.label.first() for c in individual.is_a if isinstance(c, ThingClass)]


def print_accounts(onto, title):
    print(f"\n[{title}]")
    for acc in onto.Account.instances():
        print(f"  {acc.label.first():<16} → {', '.join(class_labels(acc))}")


def check_contradiction(onto):
    """5단계: 자산이면서 부채인 계정을 넣고 추론기가 모순을 잡는지 본다."""
    with onto:
        weird = onto.Asset("weird_account", label=["이상한 계정"])
        weird.is_a.append(onto.Liability)
    try:
        reason(onto)
        return False
    except OwlReadyInconsistentOntologyError:
        return True
    finally:
        destroy_entity(weird)


def main():
    onto = build_schema()
    add_accounts(onto)

    # Protégé에서 열어 직접 추론기를 돌려볼 수 있도록 추론 전 상태를 저장
    onto.save(file="accounting.owl", format="rdfxml")

    print_accounts(onto, "추론 전: 내가 직접 입력한 분류")
    reason(onto)
    print_accounts(onto, "추론 후: 추론기가 찾아낸 분류")

    print("\n[특수관계자 계정 — 감사 시 추가 검토 대상]")
    for acc in onto.RelatedPartyAccount.instances():
        print(f"  {acc.label.first()}")

    print("\n[모순 탐지: '자산이면서 부채'인 계정 입력]")
    if check_contradiction(onto):
        print("  추론기가 모순을 발견했습니다 (자산·부채·자본은 서로 겹칠 수 없음)")
    else:
        print("  모순이 발견되지 않았습니다")


if __name__ == "__main__":
    main()
