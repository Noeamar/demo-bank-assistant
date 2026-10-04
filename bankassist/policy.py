"""Deterministic policy engine: what the authenticated customer may see and do.

The model proposes; this module decides.
- Identity comes from the session, never from model arguments.
- Every read is ownership-checked.
- Every write is proposed, confirmed by the customer, step-up authenticated when sensitive,
  executed once (idempotency key), reconciled if the outcome is unknown, and audited.
"""

import json
import secrets
import time
from dataclasses import dataclass, field

from . import config
from .adapters import CoreUnavailable, OutcomeUnknown
from .guardrails import redact

# Locking protects the customer: explicit confirmation is enough.
# Unlocking re-enables payments (PSD2 logic): strong customer authentication is required.
RISK_TIERS = {"lock_card": "protective", "unlock_card": "sensitive"}


class Denied(Exception):
    """The resource does not belong to the authenticated customer."""


class NeedsChoice(Exception):
    def __init__(self, options):
        super().__init__("several candidates")
        self.options = options


class ActionError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class Session:
    id: str
    customer_id: str
    name: str
    language: str
    branch_id: str
    history: list = field(default_factory=list)


@dataclass
class PendingAction:
    token: str
    session_id: str
    action: str
    card_id: str
    label: str
    tier: str
    expires_at: float
    otp: str = None
    status: str = "pending"

    def public(self):
        return {"token": self.token, "action": self.action, "card": self.label, "tier": self.tier,
                "requires_otp": self.otp is not None, "status": self.status}


class Audit:
    def __init__(self, path=None):
        self.events, self.path = [], path

    def log(self, session, event, detail):
        record = {"ts": round(time.time(), 3), "session": session.id, "customer": session.customer_id,
                  "event": event, "detail": json.loads(redact(json.dumps(detail, default=str)))}
        self.events.append(record)
        if self.path:
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")


class Policy:
    def __init__(self, core, audit, ttl=config.CONFIRMATION_TTL_S, clock=time.time):
        self.core, self.audit, self.ttl, self.clock = core, audit, ttl, clock
        self.pending = {}

    # ---- reads
    def card(self, session, ref=None):
        """Resolve a card reference (internal id or the last 4 digits the customer sees) among *their* cards."""
        cards = self.core.cards(session.customer_id)
        if ref is None:
            if len(cards) == 1:
                return cards[0]
            raise NeedsChoice([{"card": c.label, "last4": c.last4, "status": c.status} for c in cards])
        for c in cards:
            if ref in (c.id, c.last4):
                return c
        named = [c for c in cards if ref.strip().lower() in c.label.lower()]   # "my Visa Premier"
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise NeedsChoice([{"card": c.label, "last4": c.last4, "status": c.status} for c in named])
        self.audit.log(session, "deny", {"resource": ref})
        raise Denied(ref)

    def account(self, session, account_id=None):
        accounts = self.core.accounts(session.customer_id)
        if account_id is None:
            return accounts[0]
        for a in accounts:
            if a.id == account_id or a.id.endswith(account_id.replace(" ", "")[-4:]):
                return a
        self.audit.log(session, "deny", {"resource": account_id})
        raise Denied(account_id)

    # ---- writes
    def propose(self, session, action, card_ref=None):
        card = self.card(session, card_ref)
        target = "locked" if action == "lock_card" else "active"
        if card.status == target:
            return None
        tier = RISK_TIERS[action]
        pending = PendingAction(token=secrets.token_urlsafe(16), session_id=session.id, action=action,
                                card_id=card.id, label=f"{card.label} •••• {card.last4}", tier=tier,
                                expires_at=self.clock() + self.ttl,
                                otp=f"{secrets.randbelow(10 ** 6):06d}" if tier == "sensitive" else None)
        self.pending[pending.token] = pending
        self.audit.log(session, "propose", {"action": action, "card": card.id, "tier": tier})
        return pending

    def confirm(self, session, token, otp=None):
        p = self.pending.get(token)
        if p is None or p.session_id != session.id:        # a token is bound to one session
            self.audit.log(session, "deny", {"confirmation": "unknown token"})
            raise Denied("confirmation")
        if p.status != "pending":
            raise ActionError("already_used")
        if self.clock() > p.expires_at:
            p.status = "expired"
            raise ActionError("expired")
        if p.otp is not None and otp != p.otp:
            self.audit.log(session, "step_up_failed", {"action": p.action, "card": p.card_id})
            raise ActionError("bad_otp")
        target = "locked" if p.action == "lock_card" else "active"
        try:
            self.card(session, p.card_id)                  # re-check ownership at execution time
            p.status = "executing"
            status = self.core.set_card_locked(p.card_id, p.action == "lock_card", idempotency_key=token)
            outcome = "done"
        except OutcomeUnknown:
            status = self._reconcile(session, p.card_id)   # read back before any retry
            outcome = "done_after_reconciliation" if status == target else "unknown"
        except CoreUnavailable:
            # Nothing was written: the customer may retry this same confirmation (same idempotency key).
            p.status = "pending"
            self.audit.log(session, "execute_failed", {"action": p.action, "card": p.card_id})
            raise ActionError("core_unavailable")
        p.status = outcome
        self.audit.log(session, "execute", {"action": p.action, "card": p.card_id, "outcome": outcome})
        return {"outcome": outcome, "card": p.label, "status": status, "action": p.action}

    def _reconcile(self, session, card_id):
        try:
            return self.card(session, card_id).status
        except CoreUnavailable:
            return "unknown"
