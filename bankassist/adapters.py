"""Anti-corruption layer between the assistant and the legacy core.

The rest of the code only sees clean, typed objects. Legacy codes, cents-as-strings and
YYYYMMDD dates stop here. The LLM never talks to the core: it calls tools, and tools call this.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx

from . import config

ACCOUNT_TYPES = {"CC": "Current account", "LA": "Livret A savings"}
CARD_STATUS = {"A": "active", "B": "locked"}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class CoreUnavailable(Exception):
    """The core refused or could not be reached: nothing happened."""


class OutcomeUnknown(Exception):
    """A write timed out: it may or may not have been applied. Reconcile before any retry."""


@dataclass
class Account:
    id: str
    label: str
    iban_masked: str
    balance: Decimal
    currency: str
    as_of: date


@dataclass
class Card:
    id: str
    label: str
    last4: str
    status: str


def _day(yyyymmdd):
    return datetime.strptime(yyyymmdd, "%Y%m%d").date()


def _hours(code):
    return "closed" if code == "CLOSED" else f"{code[:2]}:{code[2:4]}–{code[5:7]}:{code[7:9]}"


class CoreBanking:
    def __init__(self, base_url=config.CORE_URL, timeout=config.CORE_TIMEOUT_S):
        self.http = httpx.Client(base_url=base_url, timeout=timeout)

    def _call(self, method, path, **kwargs):
        try:
            body = self.http.request(method, path, **kwargs).json()
        except httpx.TimeoutException as exc:
            if method == "POST":
                raise OutcomeUnknown(path) from exc
            raise CoreUnavailable(f"timeout on {path}") from exc
        except httpx.HTTPError as exc:
            raise CoreUnavailable(str(exc)) from exc
        if body.get("RET_CD") != "00":
            raise CoreUnavailable(body.get("ERR", "unknown error"))
        return body

    def profile(self, customer_id):
        body = self._call("GET", "/CUSSRV/v1/profile", params={"CUST_ID": customer_id})
        return {"name": body["NAME"], "language": body["LANG"].lower(), "branch_id": body["BR_ID"]}

    def accounts(self, customer_id):
        body = self._call("GET", "/ACCSRV/v2/listAccounts", params={"CUST_ID": customer_id})
        return [Account(id=a["ACC_NO"], label=ACCOUNT_TYPES.get(a["ACC_TYP"], a["ACC_TYP"]),
                        iban_masked=f"{a['ACC_NO'][:4]} •••• {a['ACC_NO'][-4:]}",
                        balance=Decimal(a["BAL_CENTS"]) / 100, currency=a["CCY"], as_of=_day(a["UPD_DT"]))
                for a in body["ACC_LIST"]]

    def transactions(self, account_id, limit=5):
        body = self._call("GET", "/ACCSRV/v2/listMovements", params={"ACC_NO": account_id, "MAX": limit})
        return [{"date": _day(m["DT"]).isoformat(), "label": m["LIB"].title(),
                 "amount": str(Decimal(m["AMT_CENTS"]) / 100)} for m in body["MVT"]]

    def cards(self, customer_id):
        body = self._call("GET", "/CRDSRV/v1/cards", params={"CUST_ID": customer_id})
        return [Card(id=c["CRD_ID"], label=c["PRD"].title(), last4=c["PAN_MASK"][-4:],
                     status=CARD_STATUS[c["STAT_CD"]]) for c in body["CRD"]]

    def set_card_locked(self, card_id, locked, idempotency_key):
        body = self._call("POST", "/CRDSRV/v1/cardStatusUpdate",
                          json={"CRD_ID": card_id, "NEW_STAT": "B" if locked else "A"},
                          headers={"X-Idempotency-Key": idempotency_key})
        return CARD_STATUS[body["STAT_CD"]]

    def branch(self, branch_id):
        b = self._call("GET", "/BRNSRV/v1/branch", params={"BR_ID": branch_id})["BR"]
        return self._branch(b)

    def find_branch(self, query):
        hits = self._call("GET", "/BRNSRV/v1/search", params={"q": query})["BR_LIST"]
        return [self._branch(b) for b in hits]

    @staticmethod
    def _branch(b):
        return {"id": b["BR_ID"], "name": b["NAME"], "address": b["ADDR"],
                "opening_hours": {day.lower(): _hours(b["HRS"][day]) for day in DAYS},
                "exceptional_closures": [{"date": _day(e["DT"]).isoformat(), "reason": e["RSN"].lower()}
                                         for e in b.get("EXC", [])]}
