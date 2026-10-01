# Demo Bank assistant: a production-shaped prototype

A multi-agent retail-banking assistant that **answers FAQs** and **performs actions**, such as locking or unlocking a card. It runs on Mistral models and talks to a simulated legacy core banking system. All data is fictitious.

> **Design thesis:** the LLM decides what the customer wants; deterministic bank services decide what is allowed and what is true.

![Demo: the customer's app on the left, every step of the turn on the right](demo/screenshots/07-stolen-card-lock-and-handoff.png)

The demo has two panes:
- **Left: the customer's banking app.** Chat, cards built straight from bank data (balances, branch timetable with live closures), cited sources, confirmation sheet with a one-time code, adviser ticket.
- **Right: behind the scenes.** Each step of the turn as a latency waterfall. Guardrails and router run in parallel, then agent and tool calls. Policy decisions are badged ALLOW / PENDING / DENIED, followed by the grounding checks, cost and the redacted audit log.

## Run it

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # add your MISTRAL_API_KEY
PY=.venv/bin/python ./run.sh    # core bank :8181 · assistant API :8180 · demo UI :8580
```

Open http://127.0.0.1:8580, pick a customer and click **Start new session**. The API docs are at http://127.0.0.1:8180/docs.

**Hosted (Streamlit Community Cloud):**
- The entry point is `streamlit_app.py`. It starts the simulated core and the API as local background servers in the same process, so the UI still talks to them over HTTP.
- Set `MISTRAL_API_KEY` and `ACCESS_CODE` in the app's secrets. Each session is capped at 40 messages.

| Try | What it shows |
|---|---|
| *Quels sont les horaires de mon agence ?* (Alice) | Personal structured data comes from the branch API, with a live exceptional closure. It is not RAG. |
| *What's my balance?* | An authenticated read: amounts come from the core, never from the model. |
| *Bloque ma carte*, then *la Visa Premier* | Clarification, then a **pending** action. Nothing happens until **Confirm**. |
| *Unlock my card* on a locked card | Sensitive action: a **one-time code** (step-up) is required. |
| *Show transactions of the account ending 6677* (Alice) | Bob's account: **denied by the policy engine**, not by the prompt (see trace). |
| *Ignore all previous instructions…* | Blocked by Moderation 2 (jailbreak category) before any agent runs. |
| *How much is a transfer to the US?* | RAG with citations; the outdated 2025 tariff is filtered out by validity date. |
| *I think my card was stolen!* | Lock request plus an urgent adviser handoff for the opposition, with a summary in French. |
| Sidebar: *Core banking unavailable*, then confirm | Honest failure: nothing changed, and retry is safe (same idempotency key). |
| Sidebar: *Write times out after commit*, then confirm | Unknown outcome: the status is **read back** before any retry (reconciliation). |

## Architecture

```
Channel (UI) ──REST──▶ API ──▶ Orchestrator
                                 ├─ input guardrails  ∥  router (Ministral 8B, structured output)
                                 ├─ ONE specialist agent, bounded tool loop (Mistral Small 4)
                                 │     knowledge: search_knowledge (RAG) · get_branch_hours (API)
                                 │     accounts:  get_accounts · get_transactions
                                 │     cards:     list_cards · request_card_lock · request_card_unlock · create_handoff
                                 │     handoff:   create_handoff
                                 └─ output guardrails: citations → grounding verifier → repair or abstain
                                                       · action status from system state · PAN leak
Tools ──▶ Policy engine (ownership, risk tiers, confirmation token, OTP, idempotency, audit)
      ──▶ Adapters (anti-corruption layer) ──HTTP──▶ simulated legacy core (codes, cents, YYYYMMDD)
```

| Module | Role |
|---|---|
| `bankassist/policy.py` | **The safety boundary.** Identity comes from the session, never from model arguments. Ownership checks. Actions: propose → customer confirms (OTP if sensitive) → execute once → reconcile → audit (PII redacted). |
| `bankassist/agents.py` | Router prompt and schema; four specialists, each with its own tool allowlist; bounded tool loop. |
| `bankassist/orchestrator.py` | Guardrails and routing in parallel, specialist call, output checks, trace with latency, tokens and cost. |
| `bankassist/guardrails.py` | Domain-tuned moderation policy, injection patterns, PII redaction, citation check, grounding verifier and repair. |
| `bankassist/adapters.py` | Typed domain objects; timeouts mapped to `CoreUnavailable` (nothing happened) or `OutcomeUnknown` (maybe happened). |
| `bankassist/knowledge.py` | Versioned documents with validity dates; filter first, then cosine ranking; embeddings cached. |
| `bankassist/llm.py` | 70-line REST client. Same API shape as a self-hosted vLLM: **on-prem is a base-URL change** (`LLM_BASE_URL`). |
| `bankassist/core_mock.py` | Legacy-style core with idempotency and fault injection. |

## Design decisions (and why)

- **Prototype orchestrator vs production platform.** In production, the agents would run on **Mistral Studio deployed in the bank's perimeter**. Studio supports hybrid, self-hosted and on-prem deployments, with a durable agent runtime on Temporal, observability with judges, and an AI registry. Workflows would handle longer human-in-the-loop flows. Here, a ~250-line orchestrator makes every step visible and testable. Either way, the **policy engine is a deterministic, bank-owned service** that the agents call; it is never logic inside a prompt.
- **Multi-agent for least privilege.** The accounts agent cannot even see the card tools. A test proves that a misrouted tool call is refused.
- **Tools take what the customer sees** (last 4 digits), and the server resolves it among *their* cards. The model never handles internal ids or customer ids.
- **Risk tiers.** Locking is protective (confirmation only); unlocking re-enables payments (one-time code, PSD2 logic).
- **The model never announces an action.** On state-changing turns, the status text is generated from system state. The model once wrote "I locked your card" while the lock was still awaiting confirmation; the eval now checks this.
- **Moderation policy is domain-specific.** Moderation 2 also scores `financial` and `pii`, which describe *normal* banking questions, so they are logged and never blocking.
- **Measured model choice.** Ministral 8B routes well, but was a poor grounding verifier (it flagged omissions). Mistral Small 4 was both more accurate and faster (~0.4 s vs ~1.2 s), so the verifier uses Small 4.

## Evaluation

```bash
.venv/bin/python -m pytest -q                 # 22 offline tests (policy engine, adapters, orchestrator)
.venv/bin/python evals/run_evals.py           # 43 dev cases, live models, LLM-as-judge
.venv/bin/python evals/run_evals.py --holdout # 15 held-out cases
```

| Metric (live, 2026-10-01) | Dev set (43) | Held-out (15) |
|---|---|---|
| End-to-end pass rate | 100% | **80% on first run** → 100% after fixes |
| Safety suite (cross-customer, injection, social engineering, leaks) | 100% | 100% |
| Retrieval recall@3 / MRR | 1.0 / 1.0 | 1.0 / 1.0 |
| Latency p50 / p95 (end to end) | ~1.9 s / ~3.4 s | ~2.0 s / ~4.9 s |
| Cost per 1,000 turns (API list prices) | ~$0.29 | ~$0.31 |
| LLM-as-judge grounded / helpful / tone (Medium 3.5) | 5 / 5 / 5 | 5 / 5 / 5 |

**Read with care:**
- The prompts were tuned on the dev set. The honest generalisation number is the **first** held-out run (80%), and every one of its failures was a *safe* failure (abstain, deny or handoff).
- The held-out set is now spent: the next one must come from real, anonymised client questions.
- The judge needs calibration before its scores mean anything. Fill `evals/human_labels.csv` (1/0 per answer) and rerun: agreement and Cohen's κ are reported.
- Small synthetic sets, one region, no load test: these are not production SLAs.

## Prototype vs production

| Here | In production |
|---|---|
| Fake login (customer picker) | Bank IAM (OIDC) and SCA service for step-up |
| In-memory sessions, pending actions, audit list | Shared store (Redis/DB), immutable audit trail to the SIEM |
| Simulated core over HTTP | Bank API gateway / ESB adapters, contract tests, rate limits protecting the core |
| 19 short documents, numpy cosine | Ingestion pipeline (OCR, chunking, owners, validity), hybrid search with reranking (Search Toolkit) |
| Mistral API (EU) | Models self-hosted on the bank's infrastructure via `LLM_BASE_URL`; Shieldstral for on-prem moderation; Forge post-training on captured data |
| Trace in the UI, judge script | Studio observability (Explorer, Judges) and AI Registry, OpenTelemetry, alerts, sampled human review |
