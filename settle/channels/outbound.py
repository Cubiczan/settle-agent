"""Outbound senders. One function per channel; each renders the channel-native
form of a Reply (RCS suggestion chips, WhatsApp reply buttons, numbered SMS list)."""
from __future__ import annotations

import json
import os
import re

from . import Reply

_boto = {}


def _client(name: str):
    import boto3
    if name not in _boto:
        _boto[name] = boto3.client(name)
    return _boto[name]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:64]


def render_sms(reply: Reply) -> str:
    text = reply.text
    if reply.suggestions:
        text += "\n" + "  ".join(f"[{s}]" for s in reply.suggestions)
    return text + "\nReply STOP to opt out."


def rcs_payload(reply: Reply) -> dict:
    content = {"Content": {"TextMessage": {"Body": reply.text[:3072]}}}
    if reply.suggestions:
        # PostbackData = the label, so a tap reads the same as typing it, whichever the carrier returns.
        content["Suggestions"] = [{"Reply": {"Text": s[:25], "PostbackData": s[:25]}}
                                  for s in reply.suggestions[:4]]
    return content


def statement_card(practice_name: str, image_url: str = "") -> dict:
    """First-touch rich card. Deliberately PHI-free: no amount, no service, no provider."""
    card = {
        "Title": f"{practice_name}",
        "Description": "You have a new statement. Review it here in about a minute — no login or app needed.",
        "Suggestions": [
            {"Reply": {"Text": "Review my bill", "PostbackData": "Review my bill"}},
            {"Reply": {"Text": "Talk to billing", "PostbackData": "Talk to billing"}},
        ]}
    if image_url:
        card["Media"] = {"FileUrl": image_url, "Height": "MEDIUM"}
    return {"Content": {"RichCard": {"CardOrientation": "VERTICAL", "CardContent": card}}}


def send_rcs(to: str, reply_or_content, fallback_text: str | None = None) -> str:
    content = rcs_payload(reply_or_content) if isinstance(reply_or_content, Reply) else reply_or_content
    kwargs = dict(DestinationPhoneNumber=to, OriginationIdentity=os.environ["SETTLE_RCS_AGENT_ARN"],
                  RcsMessageContent=content)
    sms_origin = os.environ.get("SETTLE_SMS_ORIGINATION")
    if sms_origin:
        body = fallback_text or (render_sms(reply_or_content) if isinstance(reply_or_content, Reply) else "")
        kwargs["FallbackConfiguration"] = {"Channel": "SMS", "MessageBody": body[:1600],
                                           "OriginationIdentity": sms_origin}
    return _client("pinpoint-sms-voice-v2").send_rcs_message(**kwargs)["MessageId"]


def send_sms(to: str, reply: Reply) -> str:
    return _client("pinpoint-sms-voice-v2").send_text_message(
        DestinationPhoneNumber=to, OriginationIdentity=os.environ["SETTLE_SMS_ORIGINATION"],
        MessageBody=render_sms(reply)[:1600], MessageType="TRANSACTIONAL")["MessageId"]


def whatsapp_payload(to: str, reply: Reply) -> dict:
    if reply.suggestions:
        return {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "interactive",
                "interactive": {"type": "button", "body": {"text": reply.text[:1024]},
                                "action": {"buttons": [{"type": "reply", "reply": {"id": _slug(s), "title": s[:20]}}
                                                       for s in reply.suggestions[:3]]}}}
    return {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text",
            "text": {"body": reply.text[:4096], "preview_url": True}}


def whatsapp_template(to: str, template: str, lang: str) -> dict:
    """Business-initiated WhatsApp messages must use a Meta-approved template."""
    return {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
            "template": {"name": template, "language": {"code": {"es": "es_MX", "en": "en_US"}[lang]}}}


def send_whatsapp(payload: dict) -> str:
    return _client("socialmessaging").send_whatsapp_message(
        originationPhoneNumberId=os.environ["SETTLE_WA_PHONE_NUMBER_ID"],
        message=json.dumps(payload).encode(), metaApiVersion="v20.0")["messageId"]


def send_email(to: str, subject: str, body: str) -> str:
    # SES sandbox accounts can only deliver to verified addresses; redirect demo mail there.
    override = os.environ.get("SETTLE_EMAIL_OVERRIDE_TO")
    if override:
        subject, to = f"[for {to}] {subject}", override
    return _client("sesv2").send_email(
        FromEmailAddress=os.environ["SETTLE_FROM_EMAIL"],
        Destination={"ToAddresses": [to]},
        Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
        ConfigurationSetName=os.environ.get("SETTLE_SES_CONFIG_SET", "settle"),
    )["MessageId"]


def send(channel: str, to: str, reply: Reply) -> str:
    if channel == "rcs":
        return send_rcs(to, reply)
    if channel == "sms":
        return send_sms(to, reply)
    if channel == "whatsapp":
        return send_whatsapp(whatsapp_payload(to, reply))
    if channel == "email":
        return send_email(to, "Re: your statement", reply.text)
    raise ValueError(channel)
