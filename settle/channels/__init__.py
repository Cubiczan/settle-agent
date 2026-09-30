"""Channel adapters: parse inbound events from AWS End User Messaging (SMS/RCS),
End User Messaging Social (WhatsApp) and SES; render and send outbound replies."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Inbound:
    channel: str            # rcs | sms | whatsapp | email
    address: str            # patient phone / wa_id / email
    text: str
    message_id: str = ""
    to: str = ""            # our identity that received it (agent ARN/id, phone, phone-number-id)


@dataclass
class Reply:
    text: str
    suggestions: list[str] = field(default_factory=list)
    link: str | None = None


# -- inbound ------------------------------------------------------------------
def parse_eum_sms(sns_message: str) -> Inbound:
    """SMS and RCS two-way payloads share one shape. For RCS, destinationNumber is
    the RCS agent identifier rather than an E.164 number."""
    m = json.loads(sns_message)
    dest = m.get("destinationNumber", "")
    channel = "sms" if dest.startswith("+") else "rcs"
    return Inbound(channel, m["originationNumber"], m.get("messageBody", ""),
                   m.get("inboundMessageId", ""), dest)


def parse_eum_social(sns_message: str) -> list[Inbound]:
    """WhatsApp events from End User Messaging Social. The Meta webhook entry is a
    JSON string inside the SNS message. Status callbacks are ignored."""
    m = json.loads(sns_message)
    entry = json.loads(m["whatsAppWebhookEntry"])
    out = []
    for change in entry.get("changes", []):
        value = change.get("value", {})
        phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
        for msg in value.get("messages", []):
            if msg["type"] == "text":
                text = msg["text"]["body"]
            elif msg["type"] == "interactive":
                it = msg["interactive"]
                text = (it.get("button_reply") or it.get("list_reply") or {}).get("title", "")
            elif msg["type"] == "button":
                text = msg["button"]["text"]
            else:
                text = f"[{msg['type']}]"
            out.append(Inbound("whatsapp", "+" + msg["from"].lstrip("+"), text, msg["id"], phone_number_id))
    return out


def parse_ses_s3(raw_email: bytes) -> Inbound:
    """SES receipt rule -> S3 -> Lambda. Takes the newest text part, drops the quoted thread."""
    from email import message_from_bytes, policy
    from email.utils import parseaddr
    msg = message_from_bytes(raw_email, policy=policy.default)
    body = msg.get_body(preferencelist=("plain",))
    text = body.get_content() if body else ""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or (line.startswith("On ") and line.rstrip().endswith("wrote:")):
            break
        lines.append(line)
    return Inbound("email", parseaddr(msg["From"])[1], "\n".join(lines).strip(),
                   msg.get("Message-ID", ""), parseaddr(msg["To"])[1])
