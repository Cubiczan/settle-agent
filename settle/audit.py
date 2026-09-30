"""Append-only, hash-chained audit log of every tool call the agent makes.

Each entry carries the SHA-256 of the previous entry, so a deleted or edited
row breaks the chain and `verify()` says where. PHI is never written here -
only ids, tool names, argument keys and outcomes.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Protocol

GENESIS = "0" * 64


class AuditSink(Protocol):
    def append(self, entry: dict) -> None: ...
    def entries(self, conversation_id: str) -> list[dict]: ...


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, sink: AuditSink):
        self.sink = sink

    def record(self, conversation_id: str, tool: str, args: dict, outcome: str) -> dict:
        prior = self.sink.entries(conversation_id)
        entry = {
            "conversation_id": conversation_id,
            "seq": len(prior),
            "ts": round(time.time(), 3),
            "tool": tool,
            "arg_keys": sorted(args),
            "outcome": outcome,
            "prev": prior[-1]["hash"] if prior else GENESIS,
        }
        entry["hash"] = _digest(entry)
        self.sink.append(entry)
        return entry

    def verify(self, conversation_id: str) -> tuple[bool, int | None]:
        prev = GENESIS
        for e in self.sink.entries(conversation_id):
            if e["prev"] != prev or _digest(e) != e["hash"]:
                return False, e["seq"]
            prev = e["hash"]
        return True, None


class MemoryAuditSink:
    def __init__(self):
        self._rows: dict[str, list[dict]] = {}

    def append(self, entry: dict) -> None:
        self._rows.setdefault(entry["conversation_id"], []).append(entry)

    def entries(self, conversation_id: str) -> list[dict]:
        return list(self._rows.get(conversation_id, []))


class DynamoAuditSink:
    """Table: pk=conversation_id (S), sk=seq (N). Conditional put blocks overwrites."""

    def __init__(self, table_name: str):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def append(self, entry: dict) -> None:
        self.table.put_item(Item=json.loads(json.dumps(entry), parse_float=str),
                            ConditionExpression="attribute_not_exists(seq)")

    def entries(self, conversation_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key
        resp = self.table.query(KeyConditionExpression=Key("conversation_id").eq(conversation_id))
        items = sorted(resp["Items"], key=lambda i: int(i["seq"]))
        for i in items:
            i["seq"] = int(i["seq"])
            i["ts"] = float(i["ts"])
        return items
