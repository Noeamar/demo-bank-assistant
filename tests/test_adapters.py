"""The anti-corruption layer turns the legacy dialect into clean domain objects."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from bankassist import core_mock
from bankassist.adapters import CoreBanking, CoreUnavailable


@pytest.fixture
def core():
    core_mock.reset()
    bank = CoreBanking.__new__(CoreBanking)
    bank.http = TestClient(core_mock.app)
    return bank


def test_accounts_are_normalised(core):
    accounts = core.accounts("C001")
    assert accounts[0].balance == Decimal("1240.50") and accounts[0].currency == "EUR"
    assert accounts[0].label == "Current account"
    assert accounts[0].iban_masked == "FR76 •••• 7890"


def test_cards_and_status_codes(core):
    cards = core.cards("C001")
    assert [(c.last4, c.status) for c in cards] == [("1234", "active"), ("9876", "active")]
    assert core.set_card_locked("K-1001", True, idempotency_key="k1") == "locked"


def test_write_is_idempotent(core):
    core.set_card_locked("K-1001", True, idempotency_key="same")
    core_mock.state["cards"]["K-1001"]["STAT_CD"] = "A"      # someone unlocks in between
    core.set_card_locked("K-1001", True, idempotency_key="same")  # replay must not write again
    assert core_mock.state["cards"]["K-1001"]["STAT_CD"] == "A"


def test_branch_hours_and_live_closure(core):
    branch = core.branch("BR-OPERA")
    assert branch["opening_hours"]["mon"] == "09:30–18:00" and branch["opening_hours"]["sun"] == "closed"
    assert branch["exceptional_closures"][0]["date"] == (date.today() + timedelta(days=4)).isoformat()


def test_core_errors_become_core_unavailable(core):
    core_mock.state["faults"]["down"] = True
    with pytest.raises(CoreUnavailable):
        core.cards("C001")
