"""One inbound message in, one reply out. Runs inside the AgentCore Runtime.

Order of operations for every message:
  1. carrier keywords (STOP / HELP / START) - handled in code, never by the model
  2. load or create the conversation; resolve the sender to a patient on file
  3. run one agent turn (Bedrock via Strands, or the offline planner)
  4. persist state, return reply + tool trace to the channel Lambda
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, asdict

from .audit import AuditLog, MemoryAuditSink, DynamoAuditSink
from .channels import Inbound, Reply
from .ledger import Ledger, from_env as ledger_from_env
from .state import ConversationState, conversation_id, store_from_env
from .tools import Effects, ToolContext, new_ticket_id

STOP = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "baja", "parar"}
HELP = {"help", "info", "ayuda"}
START = {"start", "unstop", "yes start", "alta"}


@dataclass
class Deps:
    ledger: Ledger
    store: object
    audit: AuditLog
    effects: Effects
    engine: str = "bedrock"


def handle(inbound: Inbound, deps: Deps) -> dict:
    cid = conversation_id(inbound.channel, inbound.address)
    st = deps.store.load(cid)
    if st is None:
        pid = deps.ledger.patient_by_address(inbound.channel, inbound.address)
        patient = deps.ledger.patient(pid) if pid else None
        st = ConversationState(cid, inbound.channel, inbound.address, pid,
                               language=(patient or {}).get("language", "en"))
    st.channel = inbound.channel  # RCS may fall back to SMS mid-conversation
    practice = deps.ledger.practice()
    word = inbound.text.strip().lower()
    st.transcript.append({"from": "patient", "text": inbound.text, "ts": time.time(), "channel": inbound.channel})

    if word in STOP:
        st.opted_out = True
        deps.audit.record(cid, "keyword", {"keyword": word}, "opted_out")
        reply = Reply(f"{practice['name']}: you're unsubscribed from billing messages. Reply START to resubscribe.")
        trace = []
    elif word in HELP:
        reply = Reply(f"{practice['name']} billing. Call {practice['billing_phone']} or email "
                      f"{practice['billing_email']}. Reply STOP to opt out.")
        trace = []
    elif st.opted_out and word not in START:
        deps.store.save(st)
        return {"reply": None, "trace": [], "conversation_id": cid}
    elif st.patient_id is None:
        # Unknown sender: never confirm or deny that anyone is a patient here.
        reply = Reply(f"This is {practice['name']} billing. We couldn't match this number to an account. "
                      f"Please call {practice['billing_phone']}.")
        trace = []
    else:
        if word in START:
            st.opted_out = False
        ctx = ToolContext(st, deps.ledger, deps.audit, deps.effects, last_inbound=inbound.text)
        if deps.engine == "offline":
            from .offline import run_turn
        else:
            from .agent import run_turn
        text = run_turn(ctx, inbound.text)
        reply = Reply(text, ctx.suggestions)
        trace = ctx.trace

    st.transcript.append({"from": "agent", "text": reply.text, "suggestions": reply.suggestions,
                          "ts": time.time(), "channel": inbound.channel})
    deps.store.save(st)
    return {"reply": asdict(reply), "trace": trace, "conversation_id": cid,
            "state": {"verified": st.verified, "phi_consent": st.phi_consent, "locked": st.locked,
                      "tickets": st.tickets, "plan": st.plan}}


# -- default wiring -----------------------------------------------------------
def payment_link(patient_id: str, offer_id: str, amount: str) -> str:
    """Hosted payment page URL with an HMAC'd token. Card data never enters chat."""
    base = os.environ.get("SETTLE_PAY_BASE_URL", "https://pay.riverbend.example/p")
    key = os.environ.get("SETTLE_LINK_SECRET", "dev-only-secret").encode()
    token = hmac.new(key, f"{patient_id}|{offer_id}|{amount}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{base}/{offer_id}?t={token}"


def default_deps() -> Deps:
    engine = os.environ.get("SETTLE_MODEL", "bedrock")
    audit_table = os.environ.get("SETTLE_AUDIT_TABLE")
    audit = AuditLog(DynamoAuditSink(audit_table) if audit_table else MemoryAuditSink())
    if os.environ.get("SETTLE_FROM_EMAIL"):
        from .channels.outbound import send_email
        from .tickets import open_ticket
        effects = Effects(send_email=send_email, open_ticket=open_ticket, payment_link=payment_link)
    else:
        effects = Effects(send_email=lambda to, s, b: "local-" + new_ticket_id(),
                          open_ticket=lambda t: new_ticket_id(), payment_link=payment_link)
    return Deps(ledger_from_env(), store_from_env(), audit, effects, engine)
