"""Read access to the practice-management ledger (patients, encounters, payments).

Two backends behind one interface: a JSON file for local runs and tests, and
DynamoDB for the deployed stack. The agent never writes to the ledger - money
movement (applying a payment, adjusting a balance) is a staff action.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Protocol

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic_ledger.json"


class Ledger(Protocol):
    def practice(self) -> dict: ...
    def patient(self, patient_id: str) -> dict | None: ...
    def patient_by_address(self, channel: str, address: str) -> str | None: ...
    def encounters(self, patient_id: str) -> dict[str, dict]: ...
    def payments(self, patient_id: str) -> list[dict]: ...


CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT)


def encounter_patient_balance(enc: dict) -> Decimal:
    return sum((money(a["amount"]) for line in enc["lines"]
                for a in line["adjustments"] if a["group"] == "PR"), Decimal("0.00"))


def account_summary(ledger: Ledger, patient_id: str) -> dict:
    encs = ledger.encounters(patient_id)
    pays = ledger.payments(patient_id)
    applied = {p["applied_to"]: money(p["amount"]) for p in pays if p["applied_to"]}
    per_enc = {}
    for eid, enc in encs.items():
        owed = encounter_patient_balance(enc) - applied.get(eid, Decimal("0.00"))
        per_enc[eid] = {"date": enc["date"], "provider": enc["provider"], "balance": str(owed)}
    unapplied = [p for p in pays if not p["applied_to"]]
    return {
        "balance": str(sum((Decimal(v["balance"]) for v in per_enc.values()), Decimal("0.00"))),
        "encounters": per_enc,
        "unapplied_credits": [{"id": p["id"], "date": p["date"], "amount": str(money(p["amount"])),
                               "source": p["source"]} for p in unapplied],
    }


class JsonLedger:
    def __init__(self, path: str | Path | None = None):
        self._db = json.loads(Path(path or os.environ.get("SETTLE_LEDGER_PATH", DEFAULT_PATH)).read_text())

    def practice(self) -> dict:
        return self._db["practice"]

    def patient(self, patient_id: str) -> dict | None:
        p = self._db["patients"].get(patient_id)
        return {"id": patient_id, **p} if p else None

    def patient_by_address(self, channel: str, address: str) -> str | None:
        key = "email" if channel == "email" else "phone"
        for pid, p in self._db["patients"].items():
            if p[key].lower() == address.lower():
                return pid
        return None

    def encounters(self, patient_id: str) -> dict[str, dict]:
        return {k: v for k, v in self._db["encounters"].items() if v["patient_id"] == patient_id}

    def payments(self, patient_id: str) -> list[dict]:
        return [p for p in self._db["payments"] if p["patient_id"] == patient_id]


class DynamoLedger:
    """Single-table design: pk = PATIENT#<id> | PRACTICE, sk = PROFILE | ENC#<id> | PAY#<id>.
    GSI `by_address` on (address) -> pk for inbound lookups."""

    def __init__(self, table_name: str):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def _items(self, pk: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key
        return json.loads(json.dumps(
            self.table.query(KeyConditionExpression=Key("pk").eq(pk))["Items"], default=float))

    def practice(self) -> dict:
        return self._items("PRACTICE")[0]["data"]

    def patient(self, patient_id: str) -> dict | None:
        rows = [r for r in self._items(f"PATIENT#{patient_id}") if r["sk"] == "PROFILE"]
        return {"id": patient_id, **rows[0]["data"]} if rows else None

    def patient_by_address(self, channel: str, address: str) -> str | None:
        from boto3.dynamodb.conditions import Key
        resp = self.table.query(IndexName="by_address",
                                KeyConditionExpression=Key("address").eq(address.lower()))
        return resp["Items"][0]["pk"].split("#", 1)[1] if resp["Items"] else None

    def encounters(self, patient_id: str) -> dict[str, dict]:
        return {r["sk"][4:]: r["data"] for r in self._items(f"PATIENT#{patient_id}")
                if r["sk"].startswith("ENC#")}

    def payments(self, patient_id: str) -> list[dict]:
        return [r["data"] for r in self._items(f"PATIENT#{patient_id}") if r["sk"].startswith("PAY#")]


def from_env() -> Ledger:
    table = os.environ.get("SETTLE_LEDGER_TABLE")
    return DynamoLedger(table) if table else JsonLedger()
