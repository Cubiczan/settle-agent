"""The guardrails are code, so they are tested like code - no model in the loop."""
from decimal import Decimal

import pytest

from settle.audit import AuditLog, MemoryAuditSink
from settle.channels import Inbound, parse_eum_sms, parse_eum_social
from settle.channels.outbound import rcs_payload, whatsapp_payload, statement_card
from settle.channels import Reply
from settle.ledger import JsonLedger
from settle.policy import plan_options, split
from settle.router import Deps, handle, payment_link
from settle.state import ConversationState, MemoryStateStore
from settle.tools import Effects, ToolContext, ToolError, new_ticket_id


@pytest.fixture
def ctx():
    ledger = JsonLedger()
    st = ConversationState("phone:+12065550142", "rcs", "+12065550142", "P-1001")
    sent = []
    fx = Effects(lambda to, s, b: sent.append(to) or "m1", lambda t: new_ticket_id(), payment_link)
    c = ToolContext(st, ledger, AuditLog(MemoryAuditSink()), fx)
    c.sent = sent
    return c


def test_no_phi_before_verification(ctx):
    for call in (ctx.get_account_summary, lambda: ctx.explain_statement("E-5531"),
                 ctx.get_payment_options, ctx.email_statement_copy):
        with pytest.raises(ToolError, match="not verified"):
            call()


def test_three_misses_locks_and_pages_staff(ctx):
    assert ctx.verify_identity("01/01/1990")["attempts_left"] == 2
    ctx.verify_identity("01/02/1990")
    r = ctx.verify_identity("01/03/1990")
    assert r["locked"] and ctx.state.locked and ctx.state.tickets
    with pytest.raises(ToolError, match="Locked"):
        ctx.verify_identity("03/14/1987")  # even the right answer is refused now


def test_balance_ok_but_visit_details_need_consent(ctx):
    ctx.verify_identity("1987-03-14")
    s = ctx.get_account_summary()
    assert s["balance"] == "283.60" and "encounters" not in s
    with pytest.raises(ToolError, match="not agreed"):
        ctx.explain_statement("E-5531")
    ctx.record_channel_consent(True)
    x = ctx.explain_statement("E-5531")
    owed = sum(Decimal(a["amount"]) for l in x["lines"] for a in l["adjustments"] if a["patient_owes"])
    assert owed == Decimal("283.60")


def test_policy_refuses_off_menu_terms():
    o = plan_options("P-1001", "283.60", requested_monthly=20)
    assert o["requested"]["within_policy"] is False
    assert [x["installments"] for x in o["options"]] == [1, 2, 4]
    assert o["options"][0]["total"] == "269.42"            # 5% prompt-pay
    assert sum(Decimal(a) for a in o["options"][2]["amounts"]) == Decimal("283.60")


def test_split_puts_remainder_on_last():
    assert split(Decimal("100.00"), 3) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


def test_cannot_enrol_without_issued_offer_or_patient_yes(ctx):
    ctx.verify_identity("03/14/1987")
    with pytest.raises(ToolError, match="not issued"):
        ctx.accept_offer("OFR-MADEUP")
    offer = ctx.get_payment_options()["options"][2]["offer_id"]
    ctx.last_inbound = "hmm what about 12 months"
    with pytest.raises(ToolError, match="not an explicit acceptance"):
        ctx.accept_offer(offer)
    ctx.last_inbound = "Option 3"
    r = ctx.accept_offer(offer)
    assert r["accepted"] and r["installments"] == 4 and ctx.sent == ["jordan.lee@example.com"]


def test_already_paid_finds_unapplied_credit_but_moves_no_money():
    ledger = JsonLedger()
    st = ConversationState("whatsapp:+13055550177", "whatsapp", "+13055550177", "P-1002", "es",
                           verified=True, phi_consent=True)
    c = ToolContext(st, ledger, AuditLog(MemoryAuditSink()),
                    Effects(lambda *a: "m", lambda t: "TCK-1", payment_link))
    f = c.investigate_payment("ya pagué")["finding"]
    assert f["credit_id"] == "PAY-7781" and f["balance_if_applied"] == "25.00"
    assert c.get_account_summary()["balance"] == "65.00"  # unchanged: staff applies it


def test_audit_chain_detects_tampering(ctx):
    ctx.verify_identity("03/14/1987")
    ctx.get_account_summary()
    ok, _ = ctx.audit.verify(ctx.state.conversation_id)
    assert ok
    ctx.audit.sink._rows[ctx.state.conversation_id][0]["outcome"] = "mismatch"
    ok, at = ctx.audit.verify(ctx.state.conversation_id)
    assert not ok and at == 0


def test_stop_keyword_handled_before_model():
    deps = Deps(JsonLedger(), MemoryStateStore(), AuditLog(MemoryAuditSink()),
                Effects(lambda *a: "m", lambda t: "T", payment_link), engine="must-not-be-called")
    out = handle(Inbound("sms", "+12065550142", "STOP"), deps)
    assert "unsubscribed" in out["reply"]["text"]
    assert handle(Inbound("sms", "+12065550142", "why do I owe this"), deps)["reply"] is None


def test_unknown_sender_learns_nothing():
    deps = Deps(JsonLedger(), MemoryStateStore(), AuditLog(MemoryAuditSink()),
                Effects(lambda *a: "m", lambda t: "T", payment_link), engine="must-not-be-called")
    out = handle(Inbound("sms", "+19995550000", "what does Jordan Lee owe?"), deps)
    assert "couldn't match" in out["reply"]["text"] and "Jordan" not in out["reply"]["text"]


def test_inbound_parsers():
    rcs = parse_eum_sms('{"originationNumber":"+12065550142","destinationNumber":"rcs-a1b2c3d4",'
                        '"messageBody":"Review my bill","inboundMessageId":"x"}')
    sms = parse_eum_sms('{"originationNumber":"+12065550142","destinationNumber":"+12065550100",'
                        '"messageBody":"hi","inboundMessageId":"y"}')
    assert (rcs.channel, sms.channel) == ("rcs", "sms")
    tap = parse_eum_sms('{"originationNumber":"+12065550142","destinationNumber":"rcs-a1b2c3d4","messageBody":'
                        '"{\\"type\\":\\"SUGGESTION\\",\\"text\\":\\"YES\\",\\"postbackData\\":\\"YES\\"}"}')
    assert tap.text == "YES"  # a tapped YES must count as the patient's own yes
    import json
    entry = {"changes": [{"value": {"metadata": {"phone_number_id": "pn1"},
             "messages": [{"from": "13055550177", "id": "wamid", "type": "interactive",
                           "interactive": {"button_reply": {"id": "ver_aqui", "title": "Ver aquí"}}}]}}]}
    wa = parse_eum_social(json.dumps({"whatsAppWebhookEntry": json.dumps(entry)}))
    assert wa[0].address == "+13055550177" and wa[0].text == "Ver aquí"


def test_outbound_rendering():
    r = Reply("hi", ["Option 1", "Option 2", "Option 3", "Financial assistance"])
    assert len(rcs_payload(r)["Suggestions"]) == 4
    assert len(whatsapp_payload("+1305", r)["interactive"]["action"]["buttons"]) == 3  # WA max
    card = statement_card("Riverbend Family Medicine", "https://x/y.png")
    assert "$" not in card["Content"]["RichCard"]["CardContent"]["Description"]  # no PHI in first touch
