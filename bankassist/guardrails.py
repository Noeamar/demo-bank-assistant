"""Guardrails around the model: moderation in, grounding and leak checks out, PII redaction for logs."""

import re
from dataclasses import dataclass, field

from . import config

# Moderation 2 also scores "financial", "pii" and "law": in a bank those describe normal questions,
# so they are logged, never blocking. The policy is domain-specific, not the vendor default.
BLOCKING = {"sexual", "hate_and_discrimination", "violence_and_threats", "dangerous", "selfharm", "jailbreaking"}
THRESHOLD = 0.5

# Cheap second line for instructions smuggled into a message (runs even if moderation is unavailable).
INJECTION = re.compile(r"ignore (all |the )?(previous|prior|above) instructions|system prompt|you are now|"
                       r"admin mode|developer mode|oublie (toutes )?(les |tes )?instructions", re.I)

PII = [
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b"), "[IBAN]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}"), "[PHONE]"),
]
FULL_PAN = re.compile(r"\b(?:\d[ -]?){15,19}\b")
# Claims that an action already happened. Before the customer confirms, any of these is false.
COMPLETION_CLAIM = re.compile(
    r"\b(i have|i've|i|has been|have been|is now|was|we have|we've|we) (locked|unlocked|blocked|frozen|declared|"
    r"cancelled)\b|"
    r"opposition (has been|was|is) (declared|made|registered)|"
    r"\b(j'ai|j’ai) (bloqué|verrouillé|débloqué|déverrouillé|fait opposition)|"
    r"\b(est|a été) (désormais |maintenant )?(bloquée|verrouillée|débloquée|déverrouillée)\b|"
    r"opposition (a été|est) (faite|déclarée|enregistrée)", re.I)
CITATION = re.compile(r"\[([a-z0-9-]+)\]")


def redact(text):
    for pattern, token in PII:
        text = pattern.sub(token, text)
    return text


@dataclass
class Verdict:
    blocked: bool
    reasons: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)


def check_input(llm, history, message):
    """Moderation (with jailbreak detection) + injection patterns. Returns (Verdict, Usage or None)."""
    reasons, scores, usage = [], {}, None
    if INJECTION.search(message):
        reasons.append("injection_pattern")
    try:
        result, usage = llm.moderate([*history[-4:], {"role": "user", "content": message}])
        scores = {k: round(v, 3) for k, v in result["category_scores"].items() if v >= 0.05}
        reasons += [c for c in BLOCKING if result["category_scores"].get(c, 0) >= THRESHOLD]
    except Exception:  # fail closed on patterns only; the policy engine still guards every action
        reasons.append("moderation_unavailable")
    blocked = any(r != "moderation_unavailable" for r in reasons)
    return Verdict(blocked, reasons, scores), usage


def check_grounding(answer, retrieved_ids):
    """Knowledge answers must cite at least one retrieved source, and no source that wasn't retrieved."""
    retrieved = set(retrieved_ids)
    cited = {doc_id for doc_id in retrieved if doc_id in answer}
    invented = set(CITATION.findall(answer)) - retrieved
    return bool(cited) and not invented, sorted(cited)


def leaks_card_number(answer):
    return bool(FULL_PAN.search(answer))


VERIFIER_PROMPT = """You check a bank assistant's answer against the source passages it was given.
supported = false if the answer adds a fact that is not in the passages (an amount, fee, rate, phone number,
URL, deadline, condition or procedure step) or contradicts them. Faithful paraphrases and summaries are
supported. Saying information is unavailable, offering an adviser, and politeness are fine.
A citation to a passage that does not contain the claim does not count as support.
List each unsupported claim briefly."""
VERIFIER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["supported", "unsupported"],
                   "properties": {"supported": {"type": "boolean"},
                                  "unsupported": {"type": "array", "items": {"type": "string"}}}}


def verify_grounding(llm, question, answer, passages):
    """Second-pass check with a small model: citations can be gamed, claims-vs-sources cannot as easily."""
    sources = "\n\n".join(f"[{doc_id}] {text}" for doc_id, text in passages.items())
    verdict, usage = llm.chat_json(config.VERIFIER_MODEL, [
        {"role": "system", "content": VERIFIER_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nPassages:\n{sources}\n\nAnswer: {answer}"}],
        VERIFIER_SCHEMA)
    return verdict, usage


REPAIR_PROMPT = """Rewrite the bank assistant's answer so that it contains only information stated in the passages.
Remove these unsupported claims: {claims}. Keep the [doc_id] citations of what remains, the same language and
tone, at most 80 words. If nothing useful remains, say the information is not available and offer an adviser."""


def repair(llm, answer, passages, claims):
    sources = "\n\n".join(f"[{doc_id}] {text}" for doc_id, text in passages.items())
    msg, usage = llm.chat(config.AGENT_MODEL, [
        {"role": "system", "content": REPAIR_PROMPT.format(claims="; ".join(claims))},
        {"role": "user", "content": f"Passages:\n{sources}\n\nAnswer to rewrite: {answer}"}])
    return msg.get("content") or "", usage


def claims_completion(answer):
    return bool(COMPLETION_CLAIM.search(answer))
