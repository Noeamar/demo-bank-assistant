"""The policy engine is the safety boundary: these tests must pass at 100% before any release."""

import pytest

from bankassist.adapters import Card, CoreUnavailable, OutcomeUnknown
from bankassist.policy import ActionError, Audit, Denied, NeedsChoice, Policy, Session


class FakeCore:
    def __init__(self):
        self.cards_by_customer = {"C001": [Card("K-1001", "Visa Premier", "1234", "active"),
                                           Card("K-1002", "Visa Classic", "9876", "active")],
                                  "C002": [Card("K-2001", "Mastercard Gold", "5555", "active")]}
        self.writes, self.down, self.timeout_after_commit = [], False, False

    def cards(self, customer_id):
        if self.down:
            raise CoreUnavailable("down")
        return self.cards_by_customer[customer_id]

    def set_card_locked(self, card_id, locked, idempotency_key):
        if self.down:
            raise CoreUnavailable("down")
        self.writes.append((card_id, locked, idempotency_key))
        for cards in self.cards_by_customer.values():
            for c in cards:
                if c.id == card_id:
                    c.status = "locked" if locked else "active"
        if self.timeout_after_commit:
            raise OutcomeUnknown("timeout")
        return "locked" if locked else "active"


@pytest.fixture
def env():
    core, clock = FakeCore(), [1000.0]
    policy = Policy(core, Audit(), ttl=120, clock=lambda: clock[0])
    alice = Session("s-alice", "C001", "Alice", "fr", "BR-OPERA")
    bob = Session("s-bob", "C002", "Bob", "en", "BR-LYON")
    return core, policy, alice, bob, clock


def test_cannot_touch_another_customers_card(env):
    core, policy, alice, bob, _ = env
    with pytest.raises(Denied):
        policy.propose(alice, "lock_card", "K-2001")
    with pytest.raises(Denied):
        policy.propose(alice, "lock_card", "5555")
    assert core.writes == []
    assert policy.audit.events[-1]["event"] == "deny"


def test_card_resolved_from_last4_or_choice_required(env):
    _, policy, alice, bob, _ = env
    assert policy.card(alice, "9876").id == "K-1002"
    assert policy.card(bob).id == "K-2001"                 # single card: no need to ask
    with pytest.raises(NeedsChoice):
        policy.card(alice)                                  # two cards: ask which one


def test_lock_needs_confirmation_and_executes_exactly_once(env):
    core, policy, alice, _, _ = env
    pending = policy.propose(alice, "lock_card", "1234")
    assert core.writes == []                                # proposing never writes
    result = policy.confirm(alice, pending.token)
    assert result["outcome"] == "done" and result["status"] == "locked"
    assert core.writes == [("K-1001", True, pending.token)]  # idempotency key = confirmation token
    with pytest.raises(ActionError, match="already_used"):
        policy.confirm(alice, pending.token)
    assert len(core.writes) == 1


def test_confirmation_is_bound_to_its_session(env):
    core, policy, alice, bob, _ = env
    pending = policy.propose(bob, "lock_card")
    with pytest.raises(Denied):
        policy.confirm(alice, pending.token)
    assert core.writes == []


def test_confirmation_expires(env):
    core, policy, alice, _, clock = env
    pending = policy.propose(alice, "lock_card", "1234")
    clock[0] += 121
    with pytest.raises(ActionError, match="expired"):
        policy.confirm(alice, pending.token)
    assert core.writes == []


def test_unlock_requires_one_time_code(env):
    core, policy, alice, _, _ = env
    core.cards_by_customer["C001"][0].status = "locked"
    pending = policy.propose(alice, "unlock_card", "1234")
    assert pending.tier == "sensitive" and pending.otp
    with pytest.raises(ActionError, match="bad_otp"):
        policy.confirm(alice, pending.token, otp="000000")
    assert core.writes == []
    assert policy.confirm(alice, pending.token, otp=pending.otp)["status"] == "active"


def test_nothing_to_do_when_already_in_target_state(env):
    _, policy, alice, _, _ = env
    assert policy.propose(alice, "unlock_card", "1234") is None


def test_unknown_outcome_is_reconciled_by_reading_back(env):
    core, policy, alice, _, _ = env
    core.timeout_after_commit = True
    pending = policy.propose(alice, "lock_card", "1234")
    result = policy.confirm(alice, pending.token)
    assert result["outcome"] == "done_after_reconciliation" and result["status"] == "locked"
    assert len(core.writes) == 1                            # no blind retry


def test_core_down_changes_nothing_and_stays_retryable(env):
    core, policy, alice, _, _ = env
    pending = policy.propose(alice, "lock_card", "1234")
    core.down = True
    with pytest.raises(ActionError, match="core_unavailable"):
        policy.confirm(alice, pending.token)
    core.down = False
    assert policy.confirm(alice, pending.token)["outcome"] == "done"
    assert len(core.writes) == 1


def test_audit_log_redacts_personal_data(env):
    _, policy, alice, _, _ = env
    policy.audit.log(alice, "note", {"text": "IBAN FR7630004000010001234567890, card 4974 1234 5678 1234, "
                                             "mail alice@example.com, tel 06 12 34 56 78"})
    text = policy.audit.events[-1]["detail"]["text"]
    assert "[IBAN]" in text and "[CARD]" in text and "[EMAIL]" in text and "[PHONE]" in text
    assert "1234567890" not in text and "alice@" not in text
