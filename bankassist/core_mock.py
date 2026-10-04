"""Simulated legacy core banking system (separate process, port 8181).

It deliberately speaks a "legacy" dialect (upper-case codes, amounts in cents as strings,
dates as YYYYMMDD, return codes), so the adapter layer has real translation work to do.
Fault injection lets the demo show outages and a write that succeeds but times out.
Run: uvicorn bankassist.core_mock:app --port 8181
"""

import copy
import time
from datetime import date, timedelta

from fastapi import FastAPI, Header

SEED = {
    "customers": {
        "C001": {"NAME": "Alice Martin", "LANG": "FR", "BR_ID": "BR-OPERA"},
        "C002": {"NAME": "Bob Durand", "LANG": "EN", "BR_ID": "BR-LYON"},
    },
    "accounts": {
        "FR7630004000010001234567890": {"CUST_ID": "C001", "ACC_TYP": "CC", "BAL_CENTS": "124050"},
        "FR7630004000010009876543210": {"CUST_ID": "C001", "ACC_TYP": "LA", "BAL_CENTS": "530000"},
        "FR7630004000020005555666677": {"CUST_ID": "C002", "ACC_TYP": "CC", "BAL_CENTS": "84510"},
    },
    "movements": {
        "FR7630004000010001234567890": [("20260929", "CB CARREFOUR PARIS 09", "-4520"),
                                        ("20260928", "VIR SALAIRE SEPT", "285000"),
                                        ("20260926", "PRLV EDF", "-8930")],
        "FR7630004000020005555666677": [("20260929", "CB FNAC LYON", "-12999")],
    },
    "cards": {
        "K-1001": {"CUST_ID": "C001", "PAN_MASK": "4974********1234", "PRD": "VISA PREMIER", "STAT_CD": "A"},
        "K-1002": {"CUST_ID": "C001", "PAN_MASK": "4974********9876", "PRD": "VISA CLASSIC", "STAT_CD": "A"},
        "K-2001": {"CUST_ID": "C002", "PAN_MASK": "5132********5555", "PRD": "MASTERCARD GOLD", "STAT_CD": "A"},
    },
    "branches": {
        "BR-OPERA": {"NAME": "Paris Opéra", "ADDR": "12 bd des Capucines, 75009 Paris",
                     "HRS": {"MON": "0930-1800", "TUE": "0930-1800", "WED": "0930-1800", "THU": "0930-1800",
                             "FRI": "0930-1800", "SAT": "0930-1300", "SUN": "CLOSED"}},
        "BR-LYON": {"NAME": "Lyon Bellecour", "ADDR": "4 place Bellecour, 69002 Lyon",
                    "HRS": {"MON": "CLOSED", "TUE": "0900-1730", "WED": "0900-1730", "THU": "0900-1730",
                            "FRI": "0900-1730", "SAT": "0900-1600", "SUN": "CLOSED"}},
    },
}

app = FastAPI(title="Legacy core banking (simulated)")
state = {}


def reset():
    state.clear()
    state.update(copy.deepcopy(SEED))
    state["idempotency"] = {}
    state["faults"] = {"latency_ms": 0, "down": False, "timeout_after_commit": False}
    # A live exceptional closure, a few days ahead: data a static FAQ would get wrong.
    closure = (date.today() + timedelta(days=4)).strftime("%Y%m%d")
    state["branches"]["BR-OPERA"]["EXC"] = [{"DT": closure, "HRS": "CLOSED", "RSN": "TRAVAUX"}]


reset()


def _faults():
    f = state["faults"]
    if f["latency_ms"]:
        time.sleep(f["latency_ms"] / 1000)
    return {"RET_CD": "99", "ERR": "SERVICE INDISPONIBLE"} if f["down"] else None


@app.get("/CUSSRV/v1/profile")
def profile(CUST_ID: str):
    return _faults() or ({"RET_CD": "00", **state["customers"][CUST_ID]} if CUST_ID in state["customers"]
                         else {"RET_CD": "14", "ERR": "CLIENT INCONNU"})


@app.get("/ACCSRV/v2/listAccounts")
def list_accounts(CUST_ID: str):
    down = _faults()
    if down:
        return down
    accs = [{"ACC_NO": no, "ACC_TYP": a["ACC_TYP"], "BAL_CENTS": a["BAL_CENTS"], "CCY": "EUR",
             "UPD_DT": date.today().strftime("%Y%m%d")}
            for no, a in state["accounts"].items() if a["CUST_ID"] == CUST_ID]
    return {"RET_CD": "00", "ACC_LIST": accs}


@app.get("/ACCSRV/v2/listMovements")
def list_movements(ACC_NO: str, MAX: int = 5):
    down = _faults()
    if down:
        return down
    mvts = [{"DT": d, "LIB": lib, "AMT_CENTS": amt} for d, lib, amt in state["movements"].get(ACC_NO, [])][:MAX]
    return {"RET_CD": "00", "MVT": mvts}


@app.get("/CRDSRV/v1/cards")
def cards(CUST_ID: str):
    down = _faults()
    if down:
        return down
    return {"RET_CD": "00", "CRD": [{"CRD_ID": cid, **{k: v for k, v in c.items() if k != "CUST_ID"}}
                                    for cid, c in state["cards"].items() if c["CUST_ID"] == CUST_ID]}


@app.post("/CRDSRV/v1/cardStatusUpdate")
def card_status_update(body: dict, x_idempotency_key: str = Header(...)):
    if x_idempotency_key in state["idempotency"]:          # replay: same answer, no second write
        return state["idempotency"][x_idempotency_key]
    down = _faults()
    if down:
        return down
    card = state["cards"].get(body["CRD_ID"])
    if card is None:
        return {"RET_CD": "14", "ERR": "CARTE INCONNUE"}
    card["STAT_CD"] = body["NEW_STAT"]
    result = {"RET_CD": "00", "CRD_ID": body["CRD_ID"], "STAT_CD": card["STAT_CD"],
              "OP_ID": f"OP{len(state['idempotency']) + 1:06d}"}
    state["idempotency"][x_idempotency_key] = result
    if state["faults"]["timeout_after_commit"]:
        time.sleep(3)  # the write is committed, but the caller times out before seeing it
    return result


@app.get("/BRNSRV/v1/branch")
def branch(BR_ID: str):
    down = _faults()
    if down:
        return down
    b = state["branches"].get(BR_ID)
    return {"RET_CD": "00", "BR": {"BR_ID": BR_ID, **b}} if b else {"RET_CD": "14", "ERR": "AGENCE INCONNUE"}


@app.get("/BRNSRV/v1/search")
def branch_search(q: str):
    down = _faults()
    if down:
        return down
    hits = [{"BR_ID": bid, **b} for bid, b in state["branches"].items() if q.lower() in b["NAME"].lower()]
    return {"RET_CD": "00", "BR_LIST": hits}


@app.post("/admin/faults")
def set_faults(body: dict):
    state["faults"].update({k: v for k, v in body.items() if k in state["faults"]})
    return state["faults"]


@app.post("/admin/reset")
def admin_reset():
    reset()
    return {"ok": True}
