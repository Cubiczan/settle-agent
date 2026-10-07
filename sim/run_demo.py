"""Run the two demo conversations end-to-end through the real router + tools.

    python -m sim.run_demo                     # offline planner, no AWS needed
    SETTLE_MODEL=bedrock python -m sim.run_demo  # real Strands agent on Nova Pro (Bedrock Converse)

Writes sim/out/<engine>/<scenario>.json (transcript + tool trace + audit chain check),
which media/build_video.py renders into the demo frames.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from settle.channels import Inbound
from settle.router import Deps, payment_link
from settle.audit import AuditLog, MemoryAuditSink
from settle.ledger import JsonLedger
from settle.state import MemoryStateStore
from settle.tools import Effects, new_ticket_id

OUT = Path(__file__).parent / "out"

SCENARIOS = {
    "jordan_rcs": {
        "channel": "rcs", "address": "+12065550142",
        "outreach": {"kind": "rich_card", "title": "Riverbend Family Medicine",
                     "text": "You have a new statement. Review it here in about a minute — no login or app needed.",
                     "suggestions": ["Review my bill", "Talk to billing"]},
        # A dict turn taps the first suggestion chip matching `pick` (chip wording varies run to run
        # with a live model); `if_no_plan` is only sent if the patient isn't enrolled yet.
        "turns": ["Review my bill", "03/14/1987", "Show here", "Can I do $20 a month?",
                  {"pick": r"4 (monthly )?payments|4 ×|option 3", "else": "Option 3"},
                  {"if_no_plan": "Yes"}],
    },
    "ana_whatsapp": {
        "channel": "whatsapp", "address": "+13055550177",
        "outreach": {"kind": "template", "title": "Riverbend Family Medicine",
                     "text": "Tiene un nuevo estado de cuenta. Responda a este mensaje para revisarlo.",
                     "suggestions": []},
        "turns": ["Hola, ¿por qué debo $65? Ya pagué mi copago en la recepción.",
                  "2 de noviembre de 1979", "Ver aquí"],
    },
}


def run(name: str, engine: str) -> dict:
    sc = SCENARIOS[name]
    sink = MemoryAuditSink()
    audit = AuditLog(sink)
    emails, tickets = [], []

    def send_email(to, subject, body):
        emails.append({"to": to, "subject": subject, "body": body})
        return f"ses-{len(emails):04d}"

    def open_ticket(t):
        tid = new_ticket_id()
        tickets.append({"ticket_id": tid, **t})
        return tid

    deps = Deps(JsonLedger(), MemoryStateStore(), audit,
                Effects(send_email, open_ticket, payment_link), engine)
    from settle.router import handle
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
        out = handle(Inbound(sc["channel"], sc["address"], text), deps)
        turns.append({"patient": text, "agent": out["reply"], "trace": out["trace"], "state": out["state"]})
    cid = out["conversation_id"]
    ok, bad = audit.verify(cid)
    return {"scenario": name, "engine": engine, "channel": sc["channel"], "outreach": sc["outreach"],
            "turns": turns, "emails": emails, "tickets": tickets,
            "audit": {"entries": sink.entries(cid), "chain_ok": ok, "broken_at": bad}}


def main():
    engine = os.environ.get("SETTLE_MODEL", "offline")
    out = OUT / engine
    out.mkdir(parents=True, exist_ok=True)
    for name in (sys.argv[1:] or SCENARIOS):
        res = run(name, engine)
        (out / f"{name}.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
        print(f"\n=== {name} ({engine}) ===")
        for t in res["turns"]:
            print(f"\nPATIENT: {t['patient']}")
            for tr in t["trace"]:
                print(f"   ⚙ {tr['tool']} → {tr['outcome']}")
            print(f"AGENT:   {t['agent']['text']}")
            if t["agent"]["suggestions"]:
                print(f"         {t['agent']['suggestions']}")
        print(f"\nemails={len(res['emails'])} tickets={len(res['tickets'])} "
              f"audit_entries={len(res['audit']['entries'])} chain_ok={res['audit']['chain_ok']}")


if __name__ == "__main__":
    main()
