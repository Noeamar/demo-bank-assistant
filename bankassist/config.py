"""Settings. Everything a bank would change per environment comes from env vars."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env", override=True)

# Same API shape on La Plateforme and on a self-hosted vLLM (e.g. the bank's LLM-as-a-Service):
# switching to on-prem is a base-URL change, not a code change.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1")
API_KEY = os.getenv("MISTRAL_API_KEY", "")

ROUTER_MODEL = os.getenv("ROUTER_MODEL", "ministral-8b-2512")      # fast, cheap, fine-tunable
AGENT_MODEL = os.getenv("AGENT_MODEL", "mistral-small-2603")       # Mistral Small 4: 6B active params
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "mistral-small-2603")  # measured: more accurate AND faster than Ministral 8B here
EMBED_MODEL = os.getenv("EMBED_MODEL", "mistral-embed-2312")
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "mistral-moderation-2603")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "mistral-medium-2604")      # offline evaluation only

CORE_URL = os.getenv("CORE_URL", "http://127.0.0.1:8181")
CORE_TIMEOUT_S = float(os.getenv("CORE_TIMEOUT_S", "1.5"))
CONFIRMATION_TTL_S = int(os.getenv("CONFIRMATION_TTL_S", "120"))
MAX_TOOL_ROUNDS = 3

# USD per million tokens (list prices, Sept 2026) — used to show cost per turn in the trace.
PRICES = {
    "ministral-8b-2512": (0.15, 0.15),
    "mistral-small-2603": (0.15, 0.60),
    "mistral-medium-2604": (1.5, 7.5),
    "mistral-large-2512": (0.5, 1.5),
    "mistral-embed-2312": (0.10, 0.0),
    "mistral-moderation-2603": (0.0, 0.0),
}
