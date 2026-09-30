"""The agent's tools. Every guardrail that matters is enforced here, in code:

* No PHI until `verify_identity` succeeds (possession of the number on file +
  date of birth). Three misses locks the conversation and pages staff.
* No visit details on a messaging channel until the patient consents to it.
* Money terms come only from `policy.plan_options`; enrolment requires an
  offer id the policy engine issued AND the patient's own latest message as
  confirmation - the model cannot fabricate consent.
* The agent never moves money or edits the ledger; it escalates with evidence.
* Every call lands in the hash-chained audit log.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from . import carc
from .audit import AuditLog
from .ledger import Ledger, account_summary
from .policy import Policy, plan_options
from .state import ConversationState

AFFIRMATIVE = re.compile(r"^\s*(yes|y|yeah|yep|ok|okay|confirm|sí|si|de acuerdo|acepto|1|2|3|option\s*[123]|opción\s*[123])\b",
                         re.IGNORECASE)


class ToolError(Exception):
    """Returned to the model as a tool error; it must tell the patient, not retry around it."""


@dataclass
class Effects:
    """Side-effect ports. Real adapters live in settle.channels; tests use fakes."""
    send_email: Callable[[str, str, str], str]          # (to, subject, body) -> message id
    open_ticket: Callable[[dict], str]                  # ticket -> ticket id
    payment_link: Callable[[str, str, str], str]        # (patient_id, offer_id, amount) -> url


@dataclass
class ToolContext:
    state: ConversationState
    ledger: Ledger
    audit: AuditLog
    effects: Effects
    last_inbound: str = ""
    policy: Policy = field(default_factory=Policy)
    suggestions: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    # -- plumbing ---------------------------------------------------------
    def _log(self, tool: str, args: dict, outcome: str, result=None):
        self.audit.record(self.state.conversation_id, tool, args, outcome)
        self.trace.append({"tool": tool, "args": args, "outcome": outcome, "result": result,
                           "ts": time.time()})

    def _need_verified(self, tool: str, args: dict):
        if self.state.locked:
            self._log(tool, args, "blocked:locked")
            raise ToolError("Conversation is locked after failed verification. Tell the patient "
                            "a billing specialist will call them; share no account details.")
        if not self.state.verified:
            self._log(tool, args, "blocked:unverified")
            raise ToolError("Identity not verified. Ask for the patient's date of birth first.")

    def _pid(self) -> str:
        assert self.state.patient_id
        return self.state.patient_id

    # -- tools ------------------------------------------------------------
    def verify_identity(self, date_of_birth: str) -> dict:
        args = {"date_of_birth": "***"}
        st = self.state
        if st.locked:
            self._log("verify_identity", args, "blocked:locked")
            raise ToolError("Locked. Do not ask again; a specialist will follow up.")
        patient = self.ledger.patient(st.patient_id) if st.patient_id else None
        if patient and _norm_date(date_of_birth) == patient["dob"]:
            st.verified = True
            self._log("verify_identity", args, "verified")
            return {"verified": True, "first_name": patient["first_name"],
                    "phi_consent_needed": st.channel != "email" and not st.phi_consent}
        st.verify_attempts += 1
        if st.verify_attempts >= 3:
            st.locked = True
            tid = self.effects.open_ticket({"kind": "verification_lockout",
                                            "conversation_id": st.conversation_id})
            st.tickets.append(tid)
            self._log("verify_identity", args, "locked", {"ticket": tid})
            return {"verified": False, "locked": True}
        self._log("verify_identity", args, "mismatch")
        return {"verified": False, "attempts_left": 3 - st.verify_attempts}

    def record_channel_consent(self, show_details_here: bool) -> dict:
        self._need_verified("record_channel_consent", {"show_details_here": show_details_here})
        self.state.phi_consent = bool(show_details_here)
        self._log("record_channel_consent", {"show_details_here": show_details_here}, "recorded")
        return {"phi_consent": self.state.phi_consent}

    def get_account_summary(self) -> dict:
        self._need_verified("get_account_summary", {})
        s = account_summary(self.ledger, self._pid())
        if not self.state.phi_consent:
            # Amounts only - no dates of service, no providers.
            s = {"balance": s["balance"], "open_items": len(s["encounters"]),
                 "has_unapplied_credit": bool(s["unapplied_credits"]),
                 "details_withheld": "patient has not consented to visit details on this channel"}
        self._log("get_account_summary", {}, "ok", s)
        return s

    def explain_statement(self, encounter_id: str) -> dict:
        self._need_verified("explain_statement", {"encounter_id": encounter_id})
        if not self.state.phi_consent:
            self._log("explain_statement", {"encounter_id": encounter_id}, "blocked:no_consent")
            raise ToolError("Patient has not agreed to see visit details on this channel. "
                            "Offer to show them here or email a copy instead.")
        enc = self.ledger.encounters(self._pid()).get(encounter_id)
        if not enc:
            self._log("explain_statement", {"encounter_id": encounter_id}, "not_found")
            raise ToolError(f"No encounter {encounter_id} on this account.")
        lang = self.state.language
        lines, needs_staff = [], False
        for ln in enc["lines"]:
            parts = []
            for a in ln["adjustments"]:
                why = carc.explain_adjustment(a["group"], a["carc"], lang)
                if why is None:
                    needs_staff = True
                    why = "needs review by billing staff"
                parts.append({"group": a["group"], "amount": f"{a['amount']:.2f}", "meaning": why,
                              "patient_owes": a["group"] == "PR"})
            lines.append({"service": carc.cpt_label(ln["cpt"], lang), "cpt": ln["cpt"],
                          "billed": f"{ln['billed']:.2f}", "insurance_allowed": f"{ln['allowed']:.2f}",
                          "insurance_paid": f"{ln['payer_paid']:.2f}", "adjustments": parts})
        out = {"encounter_id": encounter_id, "date": enc["date"], "provider": enc["provider"],
               "payer": enc["payer"], "lines": lines, "needs_staff_review": needs_staff}
        self._log("explain_statement", {"encounter_id": encounter_id}, "ok", out)
        return out

    def get_payment_options(self, requested_monthly: float | None = None) -> dict:
        self._need_verified("get_payment_options", {"requested_monthly": requested_monthly})
        bal = account_summary(self.ledger, self._pid())["balance"]
        opts = plan_options(self._pid(), bal, self.policy, requested_monthly)
        self.state.offers = {o["offer_id"]: o for o in opts["options"]}
        self._log("get_payment_options", {"requested_monthly": requested_monthly}, "ok", opts)
        return opts

    def accept_offer(self, offer_id: str) -> dict:
        """Enrol in a plan or pay in full. Requires an issued offer + the patient's own 'yes'."""
        args = {"offer_id": offer_id}
        self._need_verified("accept_offer", args)
        offer = self.state.offers.get(offer_id)
        if not offer:
            self._log("accept_offer", args, "blocked:unknown_offer")
            raise ToolError("That offer id was not issued by the policy engine. Call "
                            "get_payment_options and present one of its options.")
        if not AFFIRMATIVE.match(self.last_inbound or ""):
            self._log("accept_offer", args, "blocked:no_confirmation")
            raise ToolError("The patient's latest message is not an explicit acceptance. "
                            "Restate the option and ask them to reply YES.")
        pid = self._pid()
        first = offer["amounts"][0]
        url = self.effects.payment_link(pid, offer_id, first)
        patient = self.ledger.patient(pid)
        self.state.plan = {"offer_id": offer_id, "kind": offer["kind"],
                           "installments": offer["installments"], "amounts": offer["amounts"],
                           "accepted_at": time.time(), "confirmation_text": self.last_inbound[:40]}
        body = _confirmation_email(patient, offer, url, self.ledger.practice(), self.state.language)
        mid = self.effects.send_email(patient["email"], body[0], body[1])
        out = {"accepted": True, "payment_url": url, "first_amount": first,
               "installments": offer["installments"], "email_confirmation_id": mid}
        self._log("accept_offer", args, "enrolled", out)
        return out

    def investigate_payment(self, patient_claim: str) -> dict:
        """Look for evidence behind 'I already paid'. Read-only - returns a finding, moves no money."""
        args = {"patient_claim": patient_claim[:80]}
        self._need_verified("investigate_payment", args)
        s = account_summary(self.ledger, self._pid())
        finding = None
        for credit in s["unapplied_credits"]:
            for eid, enc in s["encounters"].items():
                if credit["date"] == enc["date"] and Decimal(enc["balance"]) >= Decimal(credit["amount"]):
                    finding = {"credit_id": credit["id"], "amount": credit["amount"],
                               "date": credit["date"], "source": credit["source"],
                               "likely_encounter": eid,
                               "balance_if_applied": str(Decimal(enc["balance"]) - Decimal(credit["amount"]))}
                    break
        out = {"finding": finding, "requires_staff": True}
        self._log("investigate_payment", args, "found" if finding else "none", out)
        return out

    def escalate_to_staff(self, reason: str, summary: str, recommended_action: str = "") -> dict:
        args = {"reason": reason}
        st = self.state
        tid = self.effects.open_ticket({
            "kind": reason, "conversation_id": st.conversation_id, "patient_id": st.patient_id,
            "channel": st.channel, "verified": st.verified, "summary": summary,
            "recommended_action": recommended_action,
            "evidence": [t for t in self.trace if t["tool"] in ("investigate_payment", "explain_statement")],
        })
        st.tickets.append(tid)
        self._log("escalate_to_staff", args, "opened", {"ticket": tid})
        return {"ticket_id": tid, "sla": "1 business day"}

    def email_statement_copy(self) -> dict:
        self._need_verified("email_statement_copy", {})
        patient = self.ledger.patient(self._pid())
        s = account_summary(self.ledger, self._pid())
        subj, body = _statement_email(patient, s, self.ledger.practice(), self.state.language)
        mid = self.effects.send_email(patient["email"], subj, body)
        self._log("email_statement_copy", {}, "sent", {"message_id": mid})
        return {"sent_to": _mask_email(patient["email"]), "message_id": mid}

    def suggest_replies(self, replies: list[str]) -> dict:
        """Quick-reply chips shown under the next message (RCS chips / WhatsApp buttons / SMS numbered list)."""
        self.suggestions = [r[:25] for r in replies][:4]
        self._log("suggest_replies", {"replies": self.suggestions}, "ok")
        return {"ok": True}

    def tool_names(self) -> list[str]:
        return ["verify_identity", "record_channel_consent", "get_account_summary",
                "explain_statement", "get_payment_options", "accept_offer",
                "investigate_payment", "escalate_to_staff", "email_statement_copy", "suggest_replies"]


# -- helpers --------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
MONTHS.update({m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())})


def _norm_date(s: str) -> str:
    s = s.strip().lower().replace(",", " ")
    m = re.match(r"^(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})$", s)        # 2 de noviembre de 1979
    if m and m[2] in MONTHS:
        return f"{m[3]}-{MONTHS[m[2]]:02d}-{int(m[1]):02d}"
    m = re.match(r"^([a-z]+)\s+(\d{1,2})\s+(\d{4})$", s)                     # march 14 1987
    if m and m[1] in MONTHS:
        return f"{m[3]}-{MONTHS[m[1]]:02d}-{int(m[2]):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", s)
    if m:
        return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    return s


def _mask_email(e: str) -> str:
    user, _, dom = e.partition("@")
    return f"{user[0]}***@{dom}"


def _confirmation_email(patient, offer, url, practice, lang):
    if offer["kind"] == "pay_in_full":
        line = f"Pay in full: ${offer['total']} ({offer['note']})."
    else:
        line = (f"{offer['installments']} monthly payments: " + ", ".join(f"${a}" for a in offer["amounts"])
                + f" (total ${offer['total']}, {offer['note']}).")
    subj = {"en": f"{practice['name']}: your payment arrangement",
            "es": f"{practice['name']}: su acuerdo de pago"}[lang]
    body = (f"Hi {patient['first_name']},\n\n{line}\n\nFirst payment: {url}\n\n"
            f"Questions? Reply to this email or call {practice['billing_phone']}.\n"
            f"Ref {offer['offer_id']}")
    return subj, body


def _statement_email(patient, summary, practice, lang):
    subj = {"en": f"{practice['name']}: your statement", "es": f"{practice['name']}: su estado de cuenta"}[lang]
    rows = "\n".join(f"  {v['date']}  {v['provider']}: ${v['balance']}" for v in summary["encounters"].values())
    return subj, f"Hi {patient['first_name']},\n\nBalance: ${summary['balance']}\n{rows}\n"


def new_ticket_id() -> str:
    return "TCK-" + uuid.uuid4().hex[:6].upper()
