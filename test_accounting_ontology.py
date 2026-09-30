"""추론기가 규칙대로 계정을 분류하는지 확인하는 테스트."""
import pytest

from accounting_ontology import add_accounts, build_schema, check_contradiction, reason


@pytest.fixture(scope="module")
def onto():
    onto = build_schema()
    add_accounts(onto)
    reason(onto)
    return onto


@pytest.mark.parametrize("name, expected", [
    ("cash", "CurrentAsset"),
    ("accounts_receivable", "CurrentAsset"),
    ("loan_to_subsidiary", "NonCurrentAsset"),
    ("building", "NonCurrentAsset"),
    ("accounts_payable", "CurrentLiability"),
    ("long_term_borrowings", "NonCurrentLiability"),
])
def test_유동_비유동_자동분류(onto, name, expected):
    assert getattr(onto, expected) in onto[name].is_a


def test_기간정보_없으면_분류하지_않음(onto):
    prepayment = onto.prepayment
    assert onto.CurrentAsset not in prepayment.is_a
    assert onto.NonCurrentAsset not in prepayment.is_a


def test_종속회사_관계기업_거래는_특수관계자_계정으로_추론(onto):
    assert set(onto.RelatedPartyAccount.instances()) == {
        onto.loan_to_subsidiary, onto.ar_associate}


def test_유형자산은_비유동자산이고_특수관계자_계정이_아님(onto):
    assert onto.NonCurrentAsset in onto.tangible_assets.is_a
    assert onto.RelatedPartyAccount not in onto.tangible_assets.is_a


def test_자산이면서_부채인_계정은_모순(onto):
    assert check_contradiction(onto) is True
