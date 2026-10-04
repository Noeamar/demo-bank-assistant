"""Supervisor: input guardrails ∥ routing → one bounded specialist → output guardrails → traced answer."""

import time
from concurrent.futures import ThreadPoolExecutor

from . import agents
from .guardrails import (check_grounding, check_input, claims_completion, leaks_card_number, redact, repair,
                         verify_grounding)

TEXT = {
    "blocked": {"en": "I can't help with that request. If you need assistance with your accounts or cards, "
                      "I'm here, or I can connect you with an adviser.",
                "fr": "Je ne peux pas traiter cette demande. Je peux vous aider pour vos comptes et vos cartes, "
                      "ou vous mettre en relation avec un conseiller.",
                "es": "No puedo atender esa solicitud. Puedo ayudarle con sus cuentas y tarjetas, o ponerle en "
                      "contacto con un asesor."},
    "chitchat": {"en": "Hello! I'm Demo Bank's AI assistant. I can answer questions about our products and fees, "
                       "give your balances and recent transactions, tell you your branch's opening hours, and lock "
                       "or unlock your card. For anything else, I'll connect you with an adviser.",
                 "fr": "Bonjour ! Je suis l'assistant IA de Demo Bank. Je peux répondre à vos questions sur nos "
                       "produits et tarifs, vous donner vos soldes et opérations, les horaires de votre agence, et "
                       "verrouiller ou déverrouiller votre carte. Pour le reste, je vous mets en relation avec un "
                       "conseiller.",
                 "es": "¡Hola! Soy el asistente de IA de Demo Bank. Puedo responder a sus preguntas sobre productos "
                       "y tarifas, darle sus saldos y movimientos, el horario de su oficina, y bloquear o desbloquear "
                       "su tarjeta. Para lo demás, le pongo en contacto con un asesor."},
    "abstain": {"en": "I don't have a verified answer to that in our documentation. Would you like me to connect "
                      "you with an adviser?",
                "fr": "Je n'ai pas de réponse vérifiée dans notre documentation. Voulez-vous que je vous mette en "
                      "relation avec un conseiller ?",
                "es": "No tengo una respuesta verificada en nuestra documentación. ¿Quiere que le ponga en contacto "
                      "con un asesor?"},
    "pending_lock": {"en": "To lock your {card}, please confirm below. Nothing happens until you confirm.",
                     "fr": "Pour verrouiller votre {card}, confirmez ci-dessous. Rien ne se passe avant votre "
                           "confirmation.",
                     "es": "Para bloquear su {card}, confirme abajo. No se hará nada sin su confirmación."},
    "pending_unlock": {"en": "To unlock your {card}, confirm below and enter the one-time code sent by SMS.",
                       "fr": "Pour déverrouiller votre {card}, confirmez ci-dessous et saisissez le code reçu par SMS.",
                       "es": "Para desbloquear su {card}, confirme abajo e introduzca el código recibido por SMS."},
    "handoff": {"en": "An adviser will contact you {when} (ticket {ticket}){purpose}.",
                "fr": "Un conseiller vous contactera {when} (ticket {ticket}){purpose}.",
                "es": "Un asesor se pondrá en contacto con usted {when} (ticket {ticket}){purpose}."},
    "error": {"en": "Sorry, the assistant is temporarily unavailable. Nothing was changed on your accounts.",
              "fr": "Désolé, l'assistant est momentanément indisponible. Rien n'a été modifié sur vos comptes.",
              "es": "Lo sentimos, el asistente no está disponible en este momento. No se ha modificado nada en sus "
                    "cuentas."},
}
WHEN = {"fr": {"within 2 business hours": "sous 2 heures ouvrées", "within 24 hours": "sous 24 heures"},
        "es": {"within 2 business hours": "en un plazo de 2 horas hábiles", "within 24 hours": "en 24 horas"}}
REFERENCE_FR = {"es": "La versión de referencia de nuestra documentación está en francés."}
OPPOSITION = {"en": " to declare the opposition (permanent block)",
              "fr": " pour faire opposition (blocage définitif)",
              "es": " para tramitar la oposición (bloqueo definitivo)"}


class Trace:
    def __init__(self):
        self.steps, self.start = [], time.perf_counter()

    def add(self, step, detail="", usage=None, ms=None, start_ms=None, **extra):
        entry = {"step": step, "detail": detail, **extra}
        if usage is not None:
            entry.update(model=usage.model, ms=round(usage.ms), tokens_in=usage.tokens_in,
                         tokens_out=usage.tokens_out, cost_usd=round(usage.cost_usd, 6))
        elif ms is not None:
            entry["ms"] = round(ms)
        end = (time.perf_counter() - self.start) * 1000
        entry["start_ms"] = round(start_ms if start_ms is not None else max(0.0, end - entry.get("ms", 0)))
        self.steps.append(entry)

    def totals(self):
        return {"ms": round((time.perf_counter() - self.start) * 1000),
                "llm_calls": sum(1 for s in self.steps if "model" in s),
                "tokens_in": sum(s.get("tokens_in", 0) for s in self.steps),
                "tokens_out": sum(s.get("tokens_out", 0) for s in self.steps),
                "cost_usd": round(sum(s.get("cost_usd", 0) for s in self.steps), 6)}


class Assistant:
    def __init__(self, llm, core, policy, kb):
        self.llm, self.core, self.policy, self.kb = llm, core, policy, kb
        self.pool = ThreadPoolExecutor(max_workers=4)

    def handle(self, session, message):
        trace = Trace()
        # Guardrails and routing are independent: run them in parallel to save a round trip.
        guard = self.pool.submit(check_input, self.llm, session.history, message)
        routed = self.pool.submit(agents.route, self.llm, session.history, message)
        verdict, mod_usage = guard.result()
        trace.add("guardrails.input", "blocked" if verdict.blocked else "pass", usage=mod_usage,
                  reasons=verdict.reasons, start_ms=0)
        try:
            decision, usage = routed.result()
        except Exception:
            decision, usage = {"route": "handoff", "language": session.language, "flags": []}, None
        lang = decision["language"]
        trace.add("router", f"{decision['route']} · {lang} · {','.join(decision['flags']) or '-'}", usage=usage,
                  start_ms=0)

        ctx = agents.Context(session=session, policy=self.policy, core=self.core, kb=self.kb,
                             language=lang, trace=trace)
        if verdict.blocked:
            self.policy.audit.log(session, "blocked", {"reasons": verdict.reasons, "message": message})
            reply = TEXT["blocked"][lang]
        elif decision["route"] == "chitchat":
            reply = TEXT["chitchat"][lang]
        else:
            try:
                reply = agents.run_specialist(self.llm, decision["route"], ctx, session.history, message)
            except Exception as exc:          # model or network failure: honest message, nothing executed
                trace.add("error", type(exc).__name__)
                reply = TEXT["error"][lang]
            reply = self._check_output(decision["route"], message, reply, ctx, trace, lang)
            if ctx.sources and lang in REFERENCE_FR and reply != TEXT["abstain"][lang]:
                reply += "\n\n" + REFERENCE_FR[lang]     # answered from French content: say which text is binding

        session.history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        return {"reply": reply, "route": decision["route"], "language": lang, "flags": decision["flags"],
                "blocked": verdict.blocked, "pending": [p.public() for p in ctx.pending],
                "otp_demo": {p.token: p.otp for p in ctx.pending if p.otp},   # stands in for the SMS
                "sources": ctx.sources, "handoffs": ctx.handoffs, "tools": ctx.tools_used, "cards": ctx.cards,
                "trace": trace.steps, "totals": trace.totals()}

    def _check_output(self, route, message, reply, ctx, trace, lang):
        if route == "knowledge" and "get_branch_hours" not in ctx.tools_used:
            ok, cited = check_grounding(reply, [s["id"] for s in ctx.sources])
            trace.add("guardrails.citations", f"cited {cited}" if ok else "no valid citation → abstain")
            if not ok:
                return TEXT["abstain"][lang]
            verdict, usage = verify_grounding(self.llm, message, reply, ctx.passages)
            trace.add("guardrails.grounding", "supported" if verdict["supported"] else "unsupported → repair",
                      usage=usage, unsupported=verdict["unsupported"])
            if not verdict["supported"]:
                # One repair attempt: drop the unsupported claims, then verify again; abstain if it still fails.
                reply, usage = repair(self.llm, reply, ctx.passages, verdict["unsupported"])
                trace.add("guardrails.repair", "rewritten without unsupported claims", usage=usage)
                verdict, usage = verify_grounding(self.llm, message, reply, ctx.passages)
                trace.add("guardrails.grounding", "supported" if verdict["supported"] else "unsupported → abstain",
                          usage=usage, unsupported=verdict["unsupported"])
                if not verdict["supported"] or not check_grounding(reply, list(ctx.passages))[0]:
                    return TEXT["abstain"][lang]
        if ctx.pending or ctx.handoffs:
            # State-changing turns: what the customer reads about an action comes from system state, never from
            # the model (it once wrote "I locked your card" while the lock was still awaiting confirmation).
            trace.add("guardrails.action_status", "status text from system state"
                      + (" (model text claimed completion)" if claims_completion(reply) else ""))
            return self._status_from_state(ctx, lang)
        if route == "cards" and claims_completion(reply):
            trace.add("guardrails.action_claim", "completion claimed without any action → withheld")
            return TEXT["abstain"][lang]
        if leaks_card_number(reply):
            trace.add("guardrails.leak", "card number masked")
            return redact(reply)
        return reply

    @staticmethod
    def _status_from_state(ctx, lang):
        lines = [TEXT["pending_lock" if p.action == "lock_card" else "pending_unlock"][lang].format(card=p.label)
                 for p in ctx.pending]
        for h in ctx.handoffs:
            when = WHEN.get(lang, {}).get(h["expected_contact"], h["expected_contact"])
            purpose = OPPOSITION[lang] if h["reason"] == "lost_or_stolen" else ""
            lines.append(TEXT["handoff"][lang].format(when=when, ticket=h["ticket"], purpose=purpose))
        return " ".join(lines)
