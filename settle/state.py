"""Per-conversation state. One conversation = one (channel, address) pair.

Stored as a single JSON document (DynamoDB item in the cloud, dict in memory
locally). The verification flag lives here - not in the model's context - so
a prompt-injected "I'm verified" message changes nothing.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict


@dataclass
class ConversationState:
    conversation_id: str
    channel: str                  # rcs | sms | whatsapp | email
    address: str                  # phone number, WhatsApp id or email
    patient_id: str | None = None # resolved from address; NOT proof of identity
    language: str = "en"
    verified: bool = False
    verify_attempts: int = 0
    locked: bool = False
    phi_consent: bool = False     # patient agreed to see visit details on this channel
    opted_out: bool = False
    pending_intent: str | None = None  # what the patient asked before we could answer it
    offers: dict = field(default_factory=dict)
    plan: dict | None = None
    tickets: list = field(default_factory=list)
    history: list = field(default_factory=list)   # model messages (Strands format)
    transcript: list = field(default_factory=list)  # human-readable log for the staff console
    updated: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "ConversationState":
        return cls(**json.loads(raw))


def conversation_id(channel: str, address: str) -> str:
    # RCS and SMS share a phone number, so they share a conversation - the
    # patient can fall back from RCS to SMS without re-verifying.
    family = "phone" if channel in ("rcs", "sms") else channel
    return f"{family}:{address.lower()}"


class MemoryStateStore:
    def __init__(self):
        self._rows: dict[str, str] = {}

    def load(self, cid: str) -> ConversationState | None:
        raw = self._rows.get(cid)
        return ConversationState.from_json(raw) if raw else None

    def save(self, state: ConversationState) -> None:
        state.updated = time.time()
        self._rows[state.conversation_id] = state.to_json()


class DynamoStateStore:
    def __init__(self, table_name: str, ttl_seconds: int = 60 * 60 * 24 * 30):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)
        self.ttl = ttl_seconds

    def load(self, cid: str) -> ConversationState | None:
        item = self.table.get_item(Key={"conversation_id": cid}).get("Item")
        return ConversationState.from_json(item["doc"]) if item else None

    def save(self, state: ConversationState) -> None:
        state.updated = time.time()
        self.table.put_item(Item={"conversation_id": state.conversation_id,
                                  "doc": state.to_json(),
                                  "expires_at": int(time.time()) + self.ttl})


def store_from_env():
    table = os.environ.get("SETTLE_STATE_TABLE")
    return DynamoStateStore(table) if table else MemoryStateStore()
