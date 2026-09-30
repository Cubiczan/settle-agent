"""Load data/synthetic_ledger.json into the deployed Ledger table (single-table layout).

    python scripts/seed_ledger.py <LedgerTable> [--phone P-1001=+1XXXXXXXXXX ...]

--phone points a synthetic patient at a real test device. The number is written
to DynamoDB only - never to a file in this repo.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import boto3.dynamodb.conditions

args = sys.argv[1:]
table = boto3.resource("dynamodb").Table(args[0])
db = json.loads((Path(__file__).parents[1] / "data" / "synthetic_ledger.json").read_text(), parse_float=Decimal)
for i, a in enumerate(args):
    if a == "--phone":
        pid, phone = args[i + 1].split("=", 1)
        db["patients"][pid]["phone"] = phone
# Clear old address rows so a re-seed with a different phone doesn't leave a stale match.
for pid in db["patients"]:
    for row in table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(f"PATIENT#{pid}"))["Items"]:
        table.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})
with table.batch_writer() as w:
    w.put_item(Item={"pk": "PRACTICE", "sk": "PROFILE", "data": db["practice"]})
    for pid, p in db["patients"].items():
        w.put_item(Item={"pk": f"PATIENT#{pid}", "sk": "PROFILE", "data": p, "address": p["phone"].lower()})
        w.put_item(Item={"pk": f"PATIENT#{pid}", "sk": "EMAIL", "address": p["email"].lower()})
    for eid, e in db["encounters"].items():
        w.put_item(Item={"pk": f"PATIENT#{e['patient_id']}", "sk": f"ENC#{eid}", "data": e})
    for pay in db["payments"]:
        w.put_item(Item={"pk": f"PATIENT#{pay['patient_id']}", "sk": f"PAY#{pay['id']}", "data": pay})
print("seeded", args[0], "(test-device overrides applied)" if "--phone" in args else "")
