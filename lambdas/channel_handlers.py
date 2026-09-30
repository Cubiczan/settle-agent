"""Channel Lambdas: parse inbound -> invoke the AgentCore runtime -> send the reply
back on the channel it came in on. Subscribed to the EUM SMS/RCS SNS topic, the
EUM Social (WhatsApp) SNS topic, and the SES inbound S3 bucket respectively."""
import json
import os
from dataclasses import asdict

import boto3

from settle.channels import Inbound, Reply, parse_eum_sms, parse_eum_social, parse_ses_s3
from settle.channels import outbound
from settle.state import conversation_id

agentcore = boto3.client("bedrock-agentcore")
RUNTIME_ARN = os.environ["SETTLE_RUNTIME_ARN"]


def _session_id(cid: str) -> str:
    # runtimeSessionId must be >= 33 chars; derive a stable one per conversation.
    import hashlib
    return "settle-" + hashlib.sha256(cid.encode()).hexdigest()[:40]


def _dispatch(inb: Inbound):
    cid = conversation_id(inb.channel, inb.address)
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN, runtimeSessionId=_session_id(cid),
        payload=json.dumps({"inbound": asdict(inb)}).encode())
    out = json.loads(resp["response"].read())
    if out.get("reply"):
        outbound.send(inb.channel, inb.address, Reply(**out["reply"]))
    print(json.dumps({"cid": cid, "tools": [t["tool"] + ":" + t["outcome"] for t in out.get("trace", [])]}))


def eum_sms_handler(event, _ctx):
    for rec in event["Records"]:
        _dispatch(parse_eum_sms(rec["Sns"]["Message"]))


def eum_social_handler(event, _ctx):
    for rec in event["Records"]:
        for inb in parse_eum_social(rec["Sns"]["Message"]):
            _dispatch(inb)


def ses_inbound_handler(event, _ctx):
    s3 = boto3.client("s3")
    for rec in event["Records"]:
        obj = s3.get_object(Bucket=rec["s3"]["bucket"]["name"], Key=rec["s3"]["object"]["key"])
        _dispatch(parse_ses_s3(obj["Body"].read()))
