"""Start a live demo conversation on a real device.

    AWS_PROFILE=... python scripts/start_demo.py P-1001

Clears the patient's conversation state (so verification starts fresh), then
invokes the outreach Lambda to send the first-touch message on the patient's
preferred channel. The patient's phone number is read from the deployed
ledger table, never from this repo.
"""
import json
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settle.state import conversation_id  # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else "P-1001"
out = json.loads((Path(__file__).resolve().parents[1] / "build" / "outputs.json").read_text())["Settle"]
ddb = boto3.resource("dynamodb")
profile = ddb.Table(out["LedgerTable"]).query(KeyConditionExpression=Key("pk").eq(f"PATIENT#{pid}"))["Items"]
patient = next(r["data"] for r in profile if r["sk"] == "PROFILE")

res = boto3.client("cloudformation").describe_stack_resources(StackName="Settle")["StackResources"]
state_table = next(r["PhysicalResourceId"] for r in res
                   if r["LogicalResourceId"].startswith("Conversations") and r["ResourceType"] == "AWS::DynamoDB::Table")
for ch in ("rcs", "whatsapp", "email"):
    addr = patient["email"] if ch == "email" else patient["phone"]
    ddb.Table(state_table).delete_item(Key={"conversation_id": conversation_id(ch, addr)})

resp = boto3.client("lambda").invoke(FunctionName=out["OutreachFunction"],
                                     Payload=json.dumps({"patient_ids": [pid]}).encode())
body = json.loads(resp["Payload"].read())
print(json.dumps(body, indent=2) if "sent" in body else body)
