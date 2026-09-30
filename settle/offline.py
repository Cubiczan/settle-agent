"""Deterministic stand-in for the LLM, for tests and offline demos.

It calls exactly the same tools (and hits exactly the same guardrails) as the
Bedrock-hosted agent, but picks them with keyword rules and answers from
templates. Set SETTLE_MODEL=offline to use it. It is deliberately dumb: its
job is to prove the tool layer, not to be a good conversationalist.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .tools import ToolContext, ToolError

T = {
    "ask_dob": {"en": "To protect your privacy, please reply with your date of birth (MM/DD/YYYY).",
                "es": "Para proteger su privacidad, responda con su fecha de nacimiento (por ejemplo: 2 de noviembre de 1979)."},
    "dob_retry": {"en": "That doesn't match our records. {n} attempt(s) left.",
                  "es": "Eso no coincide con nuestros registros. Le quedan {n} intento(s)."},
    "locked": {"en": "For your security I've paused this chat. A billing specialist will call you within 1 business day.",
               "es": "Por su seguridad pausé este chat. Un especialista de facturación le llamará en 1 día hábil."},
    "verified": {"en": "Thanks, {name} — you're verified. Your balance is ${bal} for {n} visit. Want the visit details here, or by email?",
                 "es": "Gracias, {name}. Ya está verificada. Su saldo es ${bal} por {n} visita. ¿Desea ver los detalles aquí o por correo?"},
    "consent_chips": {"en": ["Show here", "Email me"], "es": ["Ver aquí", "Por correo"]},
    "emailed": {"en": "Sent to {to}. Anything else?", "es": "Enviado a {to}. ¿Algo más?"},
    "explain": {"en": "Your {date} visit with {provider}: billed ${billed}. {payer} discounted ${disc} — you never owe that. "
                      "The remaining ${owe} went to your deductible, so insurance paid $0 this time.\n{lines}",
                "es": "Su visita del {date} con {provider}: se facturó ${billed}. {payer} descontó ${disc} (usted no debe eso). "
                      "Su parte es ${owe}.\n{lines}"},
    "after_explain_chips": {"en": ["Payment options", "Pay in full", "Talk to billing"],
                            "es": ["Opciones de pago", "Pagar todo", "Hablar con alguien"]},
    "options_intro_ok": {"en": "Here's what I can offer:", "es": "Esto es lo que puedo ofrecer:"},
    "options_intro_no": {"en": "I can't set up ${req}/month — plans here go up to {cap} monthly payments. Here's what I can do:",
                         "es": "No puedo hacer ${req} al mes; los planes llegan a {cap} pagos. Esto es lo que puedo ofrecer:"},
    "options_hardship": {"en": "If none of these work, a billing specialist can screen you for financial assistance.",
                         "es": "Si ninguna funciona, un especialista puede evaluarle para asistencia financiera."},
    "enrolled_plan": {"en": "Done — {n} payments of ${amt}, no interest. First payment: {url}\nConfirmation sent to {to}.",
                      "es": "Listo: {n} pagos de ${amt}, sin intereses. Primer pago: {url}\nConfirmación enviada a {to}."},
    "enrolled_full": {"en": "Done — pay ${amt} here: {url}\nReceipt goes to {to}.",
                      "es": "Listo: pague ${amt} aquí: {url}\nEl recibo va a {to}."},
    "paid_found": {"en": "I found your ${amt} payment from {date} at the front desk. It hasn't been applied to your visit yet — that's why you see ${bal}. "
                         "I've sent it to the billing team ({tid}) to apply it; your balance would then be ${after}. We'll confirm within 1 business day.",
                   "es": "Encontré su pago de ${amt} del {date} en recepción. Aún no se aplicó a su visita; por eso ve ${bal}. "
                         "Lo envié al equipo de facturación ({tid}) para aplicarlo; su saldo quedaría en ${after}. Le confirmaremos en 1 día hábil."},
    "paid_none": {"en": "I don't see that payment yet. I've asked the billing team to look ({tid}) — they'll reply within 1 business day.",
                  "es": "Aún no veo ese pago. Pedí al equipo de facturación que lo revise ({tid}); le responderán en 1 día hábil."},
    "handoff": {"en": "I've asked a billing specialist to reach out ({tid}, within 1 business day).",
                "es": "Pedí que un especialista le contacte ({tid}, en 1 día hábil)."},
    "thanks": {"en": "You're welcome! Reply anytime.", "es": "¡Con gusto! Escríbanos cuando quiera."},
    "fallback": {"en": "I can explain your bill, set up a payment plan, or connect you with billing.",
                 "es": "Puedo explicar su factura, crear un plan de pago o comunicarle con facturación."},
}
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DATEISH = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+de\s+\w+\s+de\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}")
MONEY = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
OPTION = re.compile(r"(?:option|opción|opcion)\s*([1-4])|^\s*([1-4])\s*$", re.I)


def _intent(text: str) -> str:
    t = text.lower()
    if re.search(r"already paid|ya pagu|pagué|pague mi|i paid", t): return "paid"
    if re.search(r"human|person|someone|talk to billing|hablar con|financial assistance|asistencia", t): return "handoff"
    if OPTION.search(text): return "accept"
    if re.search(r"a month|/mo|per month|monthly|payment option|plan|opciones de pago|al mes", t): return "options"
    if re.search(r"pay in full|pagar todo|pay now", t): return "full"
    if re.search(r"email|correo", t): return "email"
    if re.search(r"show here|ver aquí|ver aqui", t): return "consent_yes"
    if re.search(r"review|why|explain|what is|por qué|porque|factura", t): return "explain"
    if re.search(r"thanks|thank you|gracias", t): return "thanks"
    return "unknown"


def _date(d: str, lang: str) -> str:
    y, m, day = d.split("-")
    return f"{int(day)} de {MONTHS_ES[int(m)-1]}" if lang == "es" else f"{MONTHS_EN[int(m)-1]} {int(day)}"


def _mask(e: str) -> str:
    u, _, d = e.partition("@")
    return f"{u[0]}***@{d}"


def run_turn(ctx: ToolContext, text: str) -> str:
    st, lang = ctx.state, ctx.state.language
    try:
        return _turn(ctx, text, st, lang)
    except ToolError as e:
        return f"({e})"


def _turn(ctx, text, st, lang):
    say = lambda k, **kw: T[k][lang].format(**kw)

    if st.locked:
        return say("locked")

    if not st.verified:
        m = DATEISH.search(text)
        if not m:
            st.pending_intent = st.pending_intent or _intent(text)
            return say("ask_dob")
        r = ctx.verify_identity(m.group(0))
        if r.get("locked"):
            return say("locked")
        if not r["verified"]:
            return say("dob_retry", n=r["attempts_left"])
        s = ctx.get_account_summary()
        ctx.suggest_replies(T["consent_chips"][lang])
        return say("verified", name=r["first_name"], bal=s["balance"], n=s["open_items"])

    intent = _intent(text)
    if intent in ("consent_yes", "email") and not st.phi_consent and st.pending_intent in (None, "explain", "unknown", "paid"):
        if intent == "email":
            ctx.record_channel_consent(False)
            r = ctx.email_statement_copy()
            return say("emailed", to=r["sent_to"])
        ctx.record_channel_consent(True)
        intent = st.pending_intent if st.pending_intent in ("paid",) else "explain"
        st.pending_intent = None

    if intent == "explain":
        s = ctx.get_account_summary()
        eid = next(iter(s["encounters"]))
        x = ctx.explain_statement(eid)
        billed = sum(Decimal(l["billed"]) for l in x["lines"])
        disc = sum(Decimal(a["amount"]) for l in x["lines"] for a in l["adjustments"] if a["group"] == "CO")
        owe = sum(Decimal(a["amount"]) for l in x["lines"] for a in l["adjustments"] if a["patient_owes"])
        lines = "\n".join(f"• {l['service']}: ${sum(Decimal(a['amount']) for a in l['adjustments'] if a['patient_owes']):.2f}"
                          for l in x["lines"])
        ctx.suggest_replies(T["after_explain_chips"][lang])
        return say("explain", date=_date(x["date"], lang), provider=x["provider"], billed=f"{billed:.2f}",
                   payer=x["payer"], disc=f"{disc:.2f}", owe=f"{owe:.2f}", lines=lines)

    if intent in ("options", "full"):
        req = MONEY.search(text)
        o = ctx.get_payment_options(float(req.group(1)) if req and intent == "options" else None)
        rows = []
        for i, opt in enumerate(o["options"], 1):
            if opt["kind"] == "pay_in_full":
                rows.append(f"{i}) Pay today ${opt['total']} — {opt['note']}" if lang == "en"
                            else f"{i}) Pagar hoy ${opt['total']} (descuento por pronto pago)")
            else:
                rows.append(f"{i}) {opt['installments']} × ${opt['amounts'][0]}, no interest" if lang == "en"
                            else f"{i}) {opt['installments']} × ${opt['amounts'][0]}, sin intereses")
        rq = o.get("requested")
        head = say("options_intro_no", req=rq["monthly"], cap=o["max_installments"]) if rq and not rq["within_policy"] \
            else say("options_intro_ok")
        chips = [f"Option {i}" if lang == "en" else f"Opción {i}" for i in range(1, len(rows) + 1)]
        ctx.suggest_replies(chips[:3] + (["Financial assistance"] if lang == "en" else ["Asistencia financiera"]))
        return "\n".join([head, *rows, say("options_hardship")])

    if intent == "accept":
        m = OPTION.search(text)
        idx = int(m.group(1) or m.group(2)) - 1
        offers = list(st.offers.values())
        if not offers:
            ctx.get_payment_options()
            offers = list(st.offers.values())
        r = ctx.accept_offer(offers[idx]["offer_id"])
        email = ctx.ledger.patient(st.patient_id)["email"]
        if r["installments"] == 1:
            return say("enrolled_full", amt=r["first_amount"], url=r["payment_url"], to=_mask(email))
        return say("enrolled_plan", n=r["installments"], amt=r["first_amount"], url=r["payment_url"], to=_mask(email))

    if intent == "paid" or st.pending_intent == "paid":
        st.pending_intent = None
        r = ctx.investigate_payment(text)
        f = r["finding"]
        if f:
            s = ctx.get_account_summary()
            t = ctx.escalate_to_staff(
                "payment_not_applied",
                f"Patient reports paying at the front desk. Found unapplied {f['source']} payment "
                f"{f['credit_id']} ${f['amount']} on {f['date']} matching visit {f['likely_encounter']}.",
                f"Apply {f['credit_id']} (${f['amount']}) to {f['likely_encounter']}; "
                f"patient balance becomes ${f['balance_if_applied']}.")
            ctx.suggest_replies(["Gracias", "Hablar con alguien"] if lang == "es" else ["Thanks", "Talk to billing"])
            return say("paid_found", amt=f["amount"], date=_date(f["date"], lang), bal=s["balance"],
                       tid=t["ticket_id"], after=f["balance_if_applied"])
        t = ctx.escalate_to_staff("payment_dispute", "Patient reports a payment not found on the account.")
        return say("paid_none", tid=t["ticket_id"])

    if intent == "handoff":
        t = ctx.escalate_to_staff("patient_request", f"Patient asked for a person: {text[:120]}")
        return say("handoff", tid=t["ticket_id"])
    if intent == "email":
        r = ctx.email_statement_copy()
        return say("emailed", to=r["sent_to"])
    if intent == "thanks":
        return say("thanks")
    return say("fallback")
