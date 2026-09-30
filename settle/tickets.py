"""Staff work queue: tickets land in DynamoDB and the billing team gets an SES
notification carrying only the ticket id, reason and recommended action -
the patient's details stay behind the staff console login."""
from __future__ import annotations

import json
import os
import time

from .tools import new_ticket_id


def open_ticket(ticket: dict) -> str:
    import boto3
    tid = new_ticket_id()
    item = {"ticket_id": tid, "created": int(time.time()), "status": "open",
            "doc": json.dumps(ticket, default=str)}
    boto3.resource("dynamodb").Table(os.environ["SETTLE_TICKETS_TABLE"]).put_item(Item=item)
    staff = os.environ.get("SETTLE_STAFF_EMAIL")
    if staff:
        from .channels.outbound import send_email
        console = os.environ.get("SETTLE_CONSOLE_URL", "")
        send_email(staff, f"[Settle] {ticket.get('kind', 'ticket')} · {tid}",
                   f"Reason: {ticket.get('kind')}\nRecommended: {ticket.get('recommended_action') or '-'}\n"
                   f"Open: {console}/tickets/{tid}\n")
    return tid
