"""REST API in front of the assistant: what the mobile app / web chat would call.

Run: uvicorn bankassist.api:app --port 8180
"""

import secrets

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import config
from .adapters import CoreBanking
from .knowledge import Knowledge
from .llm import LLM
from .orchestrator import Assistant
from .policy import ActionError, Audit, Denied, Policy, Session

app = FastAPI(title="Demo Bank assistant API (prototype)")
llm = LLM()
core = CoreBanking()
policy = Policy(core, Audit(path=config.DATA / "audit.jsonl"))
assistant = Assistant(llm, core, policy, Knowledge(llm))
SESSIONS = {}


class Login(BaseModel):
    customer_id: str        # stands in for the bank's IAM: in production the session comes from an OIDC token


class Chat(BaseModel):
    message: str


class Confirm(BaseModel):
    otp: str | None = None


def current(authorization):
    session = SESSIONS.get((authorization or "").removeprefix("Bearer "))
    if session is None:
        raise HTTPException(401, "unknown session")
    return session


@app.post("/sessions")
def login(body: Login):
    try:
        profile = core.profile(body.customer_id)
    except Exception:
        raise HTTPException(404, "unknown customer")
    session = Session(id=secrets.token_urlsafe(12), customer_id=body.customer_id, name=profile["name"],
                      language=profile["language"], branch_id=profile["branch_id"])
    SESSIONS[session.id] = session
    return {"session_id": session.id, "name": session.name, "language": session.language}


@app.post("/chat")
def chat(body: Chat, authorization: str = Header(None)):
    return assistant.handle(current(authorization), body.message)


@app.post("/actions/{token}/confirm")
def confirm(token: str, body: Confirm, authorization: str = Header(None)):
    session = current(authorization)
    try:
        return policy.confirm(session, token, otp=body.otp)
    except Denied:
        raise HTTPException(403, "not allowed")
    except ActionError as exc:
        raise HTTPException(503 if exc.code == "core_unavailable" else 409, exc.code)


@app.post("/actions/{token}/cancel")
def cancel(token: str, authorization: str = Header(None)):
    session = current(authorization)
    pending = policy.pending.get(token)
    if pending and pending.session_id == session.id and pending.status == "pending":
        pending.status = "cancelled"
        policy.audit.log(session, "cancel", {"action": pending.action})
    return {"status": "cancelled"}


@app.get("/audit")
def audit(authorization: str = Header(None)):
    session = current(authorization)
    return [e for e in policy.audit.events if e["session"] == session.id][-20:]


@app.post("/demo/faults")
def faults(body: dict):
    return httpx.post(f"{config.CORE_URL}/admin/faults", json=body).json()


@app.post("/demo/reset")
def reset():
    SESSIONS.clear()
    policy.pending.clear()
    return httpx.post(f"{config.CORE_URL}/admin/reset").json()
