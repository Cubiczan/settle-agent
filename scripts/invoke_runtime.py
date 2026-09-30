"""Drive the demo conversations against the DEPLOYED AgentCore runtime.

    AWS_PROFILE=... python scripts/invoke_runtime.py [jordan_rcs|ana_whatsapp ...]

Same scripts as sim/run_demo.py, but each turn goes through
bedrock-agentcore:InvokeAgentRuntime exactly as the channel Lambdas call it,
so state, audit and tickets land in the stack's DynamoDB tables and emails go
out through SES. Output: sim/out/agentcore/<scenario>.json (video-ready).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.run_demo import SCENARIOS  # noqa: E402
from settle.state import conversation_id  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "sim" / "out" / "agentcore"
outputs = json.loads((Path(__file__).resolve().parents[1] / "build" / "outputs.json").read_text())["Settle"]
core = boto3.client("bedrock-agentcore")
ddb = boto3.resource("dynamodb")
cfn = boto3.client("cloudformation")


def table(logical_prefix: str):
    res = cfn.describe_stack_resources(StackName="Settle")["StackResources"]
    name = next(r["PhysicalResourceId"] for r in res
                if r["LogicalResourceId"].startswith(logical_prefix) and r["ResourceType"] == "AWS::DynamoDB::Table")
    return ddb.Table(name)


def run(name: str) -> dict:
    sc = SCENARIOS[name]
    cid = conversation_id(sc["channel"], sc["address"])
    table("Conversations").delete_item(Key={"conversation_id": cid})     # fresh demo run
    session = "settle-" + hashlib.sha256(f"{cid}-demo".encode()).hexdigest()[:40]
    turns, out = [], None
    for step in sc["turns"]:
        text = step
        if isinstance(step, dict) and "pick" in step:
            chips = (out or {}).get("reply", {}).get("suggestions") or []
            text = next((c for c in chips if re.search(step["pick"], c, re.I)), step["else"])
        elif isinstance(step, dict) and "if_no_plan" in step:
            if out["state"]["plan"]:
                continue
            text = step["if_no_plan"]
        inbound = {"channel": sc["channel"], "address": sc["address"], "text": text, "message_id": "", "to": ""}
        resp = core.invoke_agent_runtime(agentRuntimeArn=outputs["RuntimeArn"], runtimeSessionId=session,
                                         payload=json.dumps({"inbound": inbound}).encode())
        out = json.loads(resp["response"].read())
        turns.append({"patient": text, "agent": out["reply"], "trace": out["trace"], "state": out["state"]})
        print(f"\nPATIENT: {text}")
        for t in out["trace"]:
            print(f"   ⚙ {t['tool']} → {t['outcome']}")
        print(f"AGENT:   {out['reply']['text']}\n         {out['reply']['suggestions']}")

    audit = table("Audit").query(KeyConditionExpression=Key("conversation_id").eq(cid))["Items"]
    audit = sorted(json.loads(json.dumps(audit, default=float)), key=lambda e: e["seq"])
    tickets = []
    for tid in out["state"]["tickets"]:
        item = table("Tickets").get_item(Key={"ticket_id": tid}).get("Item")
        if item:
            tickets.append({"ticket_id": tid, **json.loads(item["doc"])})
    # verify the chain exactly as the runtime wrote it
    from settle.audit import AuditLog, MemoryAuditSink
    sink = MemoryAuditSink()
    run_audit = [e for e in audit]
    for e in run_audit:
        e["seq"], e["ts"] = int(e["seq"]), float(e["ts"])
        sink.append(e)
    ok, bad = AuditLog(sink).verify(cid)
    plan = out["state"]["plan"]
    emails = [{"subject": "Riverbend Family Medicine: your payment arrangement"}] if plan else []
    return {"scenario": name, "engine": "agentcore", "channel": sc["channel"], "outreach": sc["outreach"],
            "turns": turns, "emails": emails, "tickets": tickets,
            "audit": {"entries": run_audit, "chain_ok": ok, "broken_at": bad},
            "runtime_arn": outputs["RuntimeArn"], "session": session}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name in (sys.argv[1:] or SCENARIOS):
        print(f"\n=== {name} (deployed AgentCore runtime) ===")
        res = run(name)
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        print(f"\naudit entries={len(res['audit']['entries'])} chain_ok={res['audit']['chain_ok']} tickets={len(res['tickets'])}")
