"""Daily outreach: first touch for new statements, on each patient's preferred
consented channel. The first message carries no PHI - just an invitation."""
import os

from settle.channels import outbound
from settle.ledger import from_env

IMAGE = os.environ.get("SETTLE_CARD_IMAGE_URL", "")


def handler(event, _ctx):
    ledger = from_env()
    practice = ledger.practice()
    sent = []
    for pid in event.get("patient_ids", []):
        p = ledger.patient(pid)
        ch = p["preferred_channel"]
        if not p["consent"].get(ch):
            ch = "email" if p["consent"].get("email") else None
        if ch == "rcs":
            mid = outbound.send_rcs(p["phone"], outbound.statement_card(practice["name"], IMAGE),
                                    fallback_text=f"{practice['name']}: you have a new statement. "
                                                  "Reply REVIEW to go through it by text. Reply STOP to opt out.")
        elif ch == "whatsapp":
            mid = outbound.send_whatsapp(outbound.whatsapp_template(p["phone"], "statement_ready", p["language"]))
        elif ch == "email":
            mid = outbound.send_email(p["email"], f"{practice['name']}: new statement",
                                      "You have a new statement. Reply to this email with any question.")
        else:
            continue
        sent.append({"patient_id": pid, "channel": ch, "message_id": mid})
    return {"sent": sent}
