"""Render the ~3 minute submission video from the simulator's real output.

    python -m sim.run_demo          # produces sim/out/*.json
    python media/build_video.py     # produces submission/settle_demo.mp4 (+ .srt, thumbnails, frames)

Pipeline: HTML scene -> PNG (headless Chrome) ; narration -> WAV (macOS `say`) ;
PNG + WAV -> clip (ffmpeg) ; clips -> MP4 (ffmpeg concat). Chat bubbles and the
tool trace on screen are read from sim/out, not typed by hand.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "media" / "work"
OUT = ROOT / "submission"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VOICE, RATE = "Samantha", "188"
CSS = (ROOT / "media" / "style.css").read_text()

esc = lambda s: html.escape(s or "")


def linkify(s: str) -> str:
    return re.sub(r"(https?://\S+)", r'<a>\1</a>', esc(s))


def page(body: str, w=1920, h=1080, extra_css="") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}"
            f"html,body{{width:{w}px;height:{h}px}}{extra_css}</style></head><body>{body}</body></html>")


BRAND = "<div class='brand'><div class='logo'>S</div>Settle</div>"


def frame(tag: str, inner: str, caption: str = "", footer: str = "") -> str:
    cap = f"<div class='caption'>{caption}</div>" if caption else ""
    foot = (f"<div style='position:absolute;right:72px;top:92px;font-size:16px;color:#6B7A99'>{footer}</div>"
            if footer else "")
    return page(f"{BRAND}<div class='tag'>{tag}</div>{foot}{inner}{cap}")


# -- phone rendering ------------------------------------------------------------
def rcs_phone(items: list) -> str:
    rows = []
    for it in items:
        if it["kind"] == "card":
            chips = "".join(f"<span class='chip'>{esc(c)}</span>" for c in it.get("chips", []))
            rows.append(f"<div class='card'><div class='media'>Riverbend</div><div class='body'>"
                        f"<div class='t'>{esc(it['title'])}</div><div class='d'>{esc(it['text'])}</div></div>"
                        f"<div class='chips'>{chips}</div></div>")
        else:
            rows.append(f"<div class='msg {it['kind']}'>{linkify(it['text'])}</div>")
            if it.get("chips"):
                rows.append("<div class='chips'>" + "".join(f"<span class='chip'>{esc(c)}</span>"
                                                             for c in it["chips"]) + "</div>")
    return ("<div class='phone rcs'><div class='screen'><div class='appbar'><div class='avatar'>R</div>"
            "<div>Riverbend Family Medicine<small>✓ Verified business · RCS</small></div></div>"
            f"<div class='thread'>{''.join(rows)}</div></div></div>")


def wa_phone(items: list) -> str:
    rows = []
    for it in items:
        if it["kind"] == "tpl":
            rows.append(f"<div class='tpl'><div class='t'>{esc(it['title'])}</div>{esc(it['text'])}</div>")
        else:
            rows.append(f"<div class='msg {it['kind']}'>{linkify(it['text'])}</div>")
            if it.get("chips"):
                rows.append("<div class='chips'>" + "".join(f"<span class='chip'>{esc(c)}</span>"
                                                             for c in it["chips"]) + "</div>")
    return ("<div class='phone wa'><div class='screen'><div class='appbar'><div class='avatar'>R</div>"
            "<div>Riverbend Family Medicine<small>Cuenta de empresa · WhatsApp</small></div></div>"
            f"<div class='thread'>{''.join(rows)}</div></div></div>")


def pill(outcome: str) -> str:
    cls = "blk" if outcome.startswith("blocked") or outcome in ("mismatch", "locked") else \
        "amb-p" if outcome in ("enrolled", "opened", "found") else "ok"
    return f"<span class='pill {cls}'>{esc(outcome)}</span>"


def side(kicker: str, headline: str, trace: list, detail: str = "") -> str:
    calls = "".join(f"<div class='call'><code>{esc(t['tool'])}()</code>{pill(t['outcome'])}</div>"
                    for t in trace if t["tool"] != "suggest_replies") or \
        "<div class='call' style='color:#94A3C0'>no tools yet — identity first</div>"
    det = f"<div class='detail'>{esc(detail)}</div>" if detail else ""
    return (f"<div class='side'><div class='kicker'>{kicker}</div><h3>{headline}</h3>"
            f"<div class='trace'><div class='hd'>Agent tool calls this turn · AgentCore Runtime</div>{calls}{det}</div></div>")


def thread(sim: dict, upto: int, outreach_kind: str) -> list:
    o = sim["outreach"]
    items = [{"kind": outreach_kind, "title": o["title"], "text": o["text"], "chips": o["suggestions"]}]
    for i, t in enumerate(sim["turns"][:upto]):
        items.append({"kind": "out", "text": t["patient"]})
        items.append({"kind": "in", "text": t["agent"]["text"],
                      "chips": t["agent"]["suggestions"] if i == upto - 1 else []})
    return items


# -- scenes ---------------------------------------------------------------------
def scenes(j: dict, a: dict, tests: dict) -> list[dict]:
    foot = {"agentcore": "Live run · deployed Amazon Bedrock AgentCore Runtime · Claude Opus 5.5 · synthetic patient data",
            "bedrock": "Live run · Strands agent on Amazon Bedrock (Claude Opus 5.5) · synthetic patient data",
            }.get(j["engine"], "Conversations from the Settle simulator (offline planner) · synthetic data")
    merged = lambda turns: [t for turn in turns for t in turn["trace"]]
    S = []
    add = lambda name, narr, htm: S.append({"name": name, "narration": narr, "html": htm})

    add("01_title",
        "Settle. An agentic billing assistant that meets patients where they already are: in their messages.",
        frame("AWS CDS Agentic AI Hackathon", """
        <div style='position:absolute;left:72px;top:250px;width:1100px'>
          <div class='kicker' style='font-size:24px;letter-spacing:4px;color:var(--teal);font-weight:700;margin-bottom:26px'>AGENTIC PATIENT BILLING</div>
          <h1>Your medical bill,<br><span class='hl'>explained and settled</span><br>in the chat you already use.</h1>
          <p class='lede' style='margin-top:36px'>RCS · SMS · WhatsApp · Email</p>
        </div>
        <div style='position:absolute;right:120px;top:300px;width:520px;display:flex;flex-direction:column;gap:18px'>
          """ + "".join(f"<div style='background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px 26px;font-size:26px'>{x}</div>"
                        for x in ["Amazon Bedrock <b class='hl'>AgentCore</b> Runtime",
                                  "AWS End User Messaging · <b class='hl'>RCS + SMS</b>",
                                  "End User Messaging Social · <b class='hl'>WhatsApp</b>",
                                  "Amazon <b class='hl'>SES</b> · email in &amp; out"]) + "</div>"))

    add("02_problem",
        "After a visit, most patients get a paper statement they can't decode: codes, adjustments, a deductible nobody explained. "
        "So they call during business hours, wait on hold, or put it off, and the balance ages into collections.",
        frame("The problem", """
        <div style='position:absolute;left:72px;top:190px;width:900px'>
          <h2>Patient balances are a big part of what practices collect, and <span class='amb'>the hardest part to explain.</span></h2>
          <div style='margin-top:48px;display:flex;flex-direction:column;gap:22px;font-size:32px'>
            <div>▸ Codes and adjustments nobody explained</div>
            <div>▸ Billing phone lines open 9 to 5</div>
            <div>▸ Paper statements, then collections</div>
          </div>
        </div>
        <div style='position:absolute;right:130px;top:170px;width:620px;height:720px;background:#F5F1E8;color:#222;border-radius:8px;padding:40px;font-family:Courier,monospace;font-size:20px;transform:rotate(2deg);box-shadow:0 30px 80px rgba(0,0,0,.5)'>
          <div style='font-weight:700;font-size:24px'>STATEMENT OF ACCOUNT</div><div style='margin:6px 0 26px'>Acct 00P1001 · Page 1 of 2</div>
          <div>DOS 08/12/26</div><div>99214  245.00  CO-45  -76.60</div><div>       PR-1 168.40</div>
          <div>93000  110.00  CO-45  -45.80</div><div>       PR-1  64.20</div>
          <div>80053   98.00  CO-45  -55.90</div><div>       PR-1  42.10</div>
          <div>36415   25.00  CO-45  -16.10</div><div>       PR-1   8.90</div>
          <div style='margin-top:26px;font-weight:700'>PATIENT RESP     283.60</div>
          <div style='margin-top:40px;color:#A33;font-weight:700'>PAST DUE AFTER 30 DAYS</div>
        </div>"""))

    add("03_solution",
        "Settle is an agent on Amazon Bedrock Agent Core that handles billing conversations over R C S, S M S, "
        "WhatsApp and email. It explains the bill line by line, offers only the payment options the practice approved, "
        "and hands disputes to staff with the evidence already gathered.",
        frame("The solution", """
        <div style='position:absolute;left:72px;top:180px;width:1700px'>
          <h2>One agent, every channel. <span class='hl'>Policy in code, not in the prompt.</span></h2>
        </div>
        <div style='position:absolute;left:72px;right:72px;top:420px;display:grid;grid-template-columns:repeat(3,1fr);gap:34px'>
        """ + "".join(f"""<div style='background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:40px;min-height:360px'>
             <div style='font-size:22px;letter-spacing:3px;color:var(--teal);font-weight:800'>{k}</div>
             <div style='font-size:40px;font-weight:800;margin:14px 0 18px'>{t}</div>
             <div style='font-size:26px;color:var(--mute);line-height:1.45'>{d}</div></div>"""
                      for k, t, d in [
                          ("01 · EXPLAIN", "What do I owe, and why?", "Line-by-line from the adjudicated claim: billed, discounted, deductible, copay, coinsurance."),
                          ("02 · ARRANGE", "A plan I actually chose", "Options come from the practice's policy engine. Secure link to pay, and the confirmation arrives by SES email."),
                          ("03 · ESCALATE", "Humans for the hard parts", "Disputes, hardship, anything odd: a staff ticket with the evidence and a recommended fix.")]) + "</div>"))

    jt = j["turns"]
    steps = [
        (0, "A new statement is ready. Jordan gets a rich card over R C S. There's no amount, no service and no provider on it. "
            "The first message carries no health information at all.",
         "First touch", "A rich card with no PHI", [], "send_rcs_message(RichCard)\n+ SMS FallbackConfiguration",
         "RCS rich card via <b>SendRcsMessage</b>, with SMS fallback for non-RCS phones"),
        (1, "Jordan taps Review my bill. First, the agent asks for a date of birth. "
            "The account tools refuse to answer until verification succeeds.",
         "Verify", "Identity before anything else", jt[0]["trace"], "",
         "Possession of the number on file <b>+</b> date of birth. Three misses locks the chat."),
        (2, "Once Jordan is verified, the agent shares the balance. Visit details wait until Jordan agrees to see them on this channel.",
         "Consent", "Balance yes. Visit details: ask first.", jt[1]["trace"], "phi_consent_needed: true",
         "Verified, but services and providers stay hidden until the patient opts in"),
        (3, "Then it explains the bill: four services, an insurance discount Jordan never owes, and the rest applied to the deductible. "
            "It's plain language, and it's grounded in the adjudicated claim.",
         "Explain", "The bill, in plain language", jt[2]["trace"], "PR-1 → deductible · CO-45 → never owed",
         "Claim adjustment codes translated from an allow-list. Unknown codes go to staff."),
        (4, "Jordan asks for twenty dollars a month. That's outside policy, so the agent offers what's possible: "
            "a prompt-pay discount, or up to four payments with no interest, plus financial assistance screening.",
         "Arrange", "Only terms the practice approved", jt[3]["trace"],
         "requested $20.00/mo → within_policy: false\noptions: 1 × $269.42 · 2 × $141.80 · 4 × $70.90",
         "The model can't invent a discount. The policy engine issues every offer."),
        (min(5, len(jt)), "Jordan taps four payments. The agent tries to enroll, and the tool blocks it: "
            "a button tap isn't an explicit yes. So the agent restates the terms and asks.",
         "Guardrail", "Blocked until the patient says yes", jt[4]["trace"] if len(jt) > 4 else [],
         "accept_offer → blocked:no_confirmation\n\"latest message is not an explicit acceptance\"",
         "The model <b>can't fabricate consent</b>. The tool checks the patient's own words."),
        (len(jt), "Jordan says yes. Now enrollment goes through. A secure payment link comes back, and S E S emails the confirmation.",
         "Settle", "Enrolled, with the patient's own yes", jt[-1]["trace"],
         f"email → {j['emails'][0]['subject'] if j['emails'] else ''}",
         "Card numbers never enter the chat. There's a hosted payment link and an SES confirmation."),
    ]
    for i, (upto, narr, kick, head, trace, detail, cap) in enumerate(steps):
        add(f"04_jordan_{i}", narr, frame("Demo 1 · RCS · English",
            rcs_phone([dict(x, kind="card" if x["kind"] == "rich_card" else x["kind"]) for x in thread(j, upto, "card")])
            + side(kick, head, trace, detail), cap, foot))

    at = a["turns"]
    tk = a["tickets"][0] if a["tickets"] else {}
    asteps = [
        (1, "Now Ana. She writes on WhatsApp, in Spanish: why do I owe sixty-five dollars? I already paid my copay at the front desk.",
         "Demo 2", "Same agent, WhatsApp, Spanish", at[0]["trace"], "",
         "Business-initiated WhatsApp uses an approved template. Replies come in via <b>End User Messaging Social</b>."),
        (2, "The agent answers in Spanish and verifies her. Then it investigates: it finds a forty dollar front-desk payment "
            "that was never applied. It can't move money, so it opens a ticket for billing with the exact fix.",
         "Investigate", "Finds the unapplied payment", at[1]["trace"],
         "PAY-7781 $40.00 · 2026-09-02 · front_desk\n→ balance if applied: $25.00",
         "Read-only investigation. Applying money is a human decision."),
        (len(at), "Only after Ana agrees to see details here does it explain the remaining twenty-five dollars: "
            "her coinsurance for a strep test.",
         "Consent", "Details only after she opts in", merged(at[2:]), "record_channel_consent → recorded\nexplain_statement → ok",
         "Balances after verification. Visit details only with consent for this channel."),
    ]
    for i, (upto, narr, kick, head, trace, detail, cap) in enumerate(asteps):
        add(f"05_ana_{i}", narr, frame("Demo 2 · WhatsApp · Español",
            wa_phone(thread(a, upto, "tpl")) + side(kick, head, trace, detail), cap, foot))

    add("05_ana_ticket",
        "Staff get the ticket with the evidence attached and a recommended action. There's no call, no hold and no digging, "
        "and every step is in a tamper-evident audit log.",
        frame("Staff handoff", f"""
        <div style='position:absolute;left:72px;right:72px;top:150px;display:grid;grid-template-columns:1.25fr 1fr;gap:34px'>
          <div class='trace' style='padding:36px 40px'>
            <div class='hd'>Ticket · DynamoDB → billing inbox</div>
            <div style='font-size:44px;font-weight:800;margin:6px 0 4px'>{esc(tk.get('ticket_id',''))} · {esc(tk.get('kind',''))}</div>
            <div style='font-size:22px;color:var(--mute);margin-bottom:26px'>WhatsApp · verified: {tk.get('verified')} · SLA 1 business day</div>
            <div style='font-size:24px;color:var(--mute);letter-spacing:2px'>SUMMARY</div>
            <div style='font-size:27px;line-height:1.45;margin:8px 0 26px'>{esc(tk.get('summary',''))}</div>
            <div style='font-size:24px;color:var(--amber);letter-spacing:2px'>RECOMMENDED ACTION</div>
            <div style='font-size:30px;line-height:1.4;margin-top:8px;font-weight:700'>{esc(tk.get('recommended_action',''))}</div>
          </div>
          <div class='trace' style='padding:36px 40px'>
            <div class='hd'>Audit chain · SHA-256 linked</div>
            {''.join(f"<div class='call' style='font-size:20px'><code>#{e['seq']} {esc(e['tool'])}</code><span class='pill ok'>{esc(e['hash'][:10])}</span></div>" for e in a['audit']['entries'])}
            <div class='detail' style='color:var(--green)'>chain verified: {a['audit']['chain_ok']} · {len(a['audit']['entries'])} entries · no PHI stored</div>
          </div>
        </div>""", "The agent gathers the evidence. <b>Staff</b> make the money decision.", foot))

    add("06_architecture",
        "Here's how it runs. S M S and R C S arrive through End User Messaging, WhatsApp through End User Messaging Social, "
        "and email through S E S. Channel Lambdas invoke the Strands agent on Agent Core Runtime, one isolated session per conversation, "
        "with Claude on Bedrock. State, the ledger, tickets and the audit chain live in Dynamo D B, and replies go back on the same channel.",
        arch_html())

    rows = "".join(
        f"<div class='call' style='font-size:27px;padding:15px 0'><span style='color:{'var(--green)' if ok else 'var(--red)'};font-weight:900;width:34px'>{'✓' if ok else '✗'}</span>{esc(label)}</div>"
        for label, ok in tests.items())
    add("07_guardrails",
        "The guardrails are code, and they're tested: no health information before verification, lockout after three misses, "
        "payment terms only from policy, no enrollment without the patient's yes, stop and help before the model, "
        "and a tamper-evident log of every tool call.",
        frame("Guardrails are tests", f"""
        <div style='position:absolute;left:72px;top:170px;width:620px'>
          <h2>Safety that <span class='hl'>doesn't depend on the model</span> behaving.</h2>
          <p class='lede' style='margin-top:30px'>Tools enforce policy. The model only chooses which tool to call and how to say it.</p>
          <div style='margin-top:40px;font-family:"SF Mono",Menlo,monospace;font-size:24px;color:var(--green)'>pytest · {sum(tests.values())}/{len(tests)} passed</div>
        </div>
        <div class='trace' style='position:absolute;left:760px;right:72px;top:160px;padding:26px 36px'>{rows}</div>"""))

    add("08_close",
        "For practices, it means fewer billing calls and faster patient collections. For patients, it's a bill they understand "
        "and a plan they chose, in the app they already use. Settle.",
        frame("Settle", """
        <div style='position:absolute;left:72px;top:230px;width:1780px'>
          <h1 style='font-size:110px'>Settle.</h1>
          <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:30px;margin-top:60px'>
          """ + "".join(f"<div style='border-top:4px solid var(--teal);padding-top:22px'><div style='font-size:38px;font-weight:800'>{t}</div><div style='font-size:26px;color:var(--mute);margin-top:10px;line-height:1.4'>{d}</div></div>"
                        for t, d in [("Fewer billing calls", "Routine questions are answered around the clock, in the patient's language"),
                                     ("Faster collections", "A plan the patient chose, with a payment link in the same thread"),
                                     ("Staff on exceptions", "Tickets arrive with the evidence and a recommended fix")]) + """
          </div>
          <p class='lede' style='margin-top:70px;font-size:26px'>Amazon Bedrock AgentCore · AWS End User Messaging (RCS, SMS) · End User Messaging Social (WhatsApp) · Amazon SES · DynamoDB · Lambda</p>
        </div>"""))
    return S


def arch_html() -> str:
    return frame("Architecture", (ROOT / "media" / "architecture_body.html").read_text(),
                 "Channels in, <b>one agent</b> in AgentCore, replies out on the same channel.")


# -- tests -> labels ------------------------------------------------------------------
LABELS = {
    "test_no_phi_before_verification": "No account details before identity verification",
    "test_three_misses_locks_and_pages_staff": "3 failed verifications lock the chat and page staff",
    "test_balance_ok_but_visit_details_need_consent": "Visit details need channel consent",
    "test_policy_refuses_off_menu_terms": "Payment terms only from the policy engine",
    "test_cannot_enrol_without_issued_offer_or_patient_yes": "No enrolment without an issued offer + the patient's yes",
    "test_already_paid_finds_unapplied_credit_but_moves_no_money": "Agent investigates but never moves money",
    "test_stop_keyword_handled_before_model": "STOP / HELP handled before the model",
    "test_unknown_sender_learns_nothing": "Unknown senders learn nothing",
    "test_audit_chain_detects_tampering": "Audit chain detects tampering",
    "test_outbound_rendering": "First touch carries no PHI",
}


def run_tests() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", str(ROOT / "tests")],
                       capture_output=True, text=True, cwd=ROOT)
    passed = set(re.findall(r"PASSED tests/\S+::(\w+)", r.stdout))
    return {label: name in passed for name, label in LABELS.items()}


# -- rendering ------------------------------------------------------------------------
def sh(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def screenshot(html_text: str, png: Path, w=1920, h=1080):
    src = WORK / (png.stem + ".html")
    src.write_text(html_text)
    sh(CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
       f"--window-size={w},{h}", "--virtual-time-budget=1500", f"--screenshot={png}", src.as_uri())


def narrate(text: str, wav: Path) -> float:
    aiff = wav.with_suffix(".aiff")
    sh("say", "-v", VOICE, "-r", RATE, "-o", str(aiff), text)
    sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "48000", "-ac", "2", str(wav))
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def clip(png: Path, wav: Path, dur: float, mp4: Path, lead=0.25, tail=0.4):
    total = dur + lead + tail
    sh("ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png),
       "-i", str(wav), "-filter_complex",
       f"[0:v]scale=1920:1080,format=yuv420p,fade=t=in:st=0:d=0.3,fade=t=out:st={total-0.3:.2f}:d=0.3[v];"
       f"[1:a]adelay={int(lead*1000)}|{int(lead*1000)},apad[a]",
       "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium",
       "-crf", "18", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(mp4))
    return total


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def thumbnail():
    for w, h, name in [(1500, 1000, "thumbnail_3x2.png"), (1280, 720, "thumbnail_16x9.png")]:
        s = min(w / 1500, h / 1000)
        body = f"""
        <div style='position:absolute;inset:0;background:radial-gradient({900*s}px {700*s}px at 85% 0%,#1B3A6B 0%,#0B1220 65%)'></div>
        <div style='position:absolute;left:{70*s}px;top:{80*s}px;display:flex;align-items:center;gap:{16*s}px'>
          <div class='logo' style='width:{70*s}px;height:{70*s}px;font-size:{42*s}px;border-radius:{18*s}px'>S</div>
          <div style='font-size:{56*s}px;font-weight:800'>Settle</div></div>
        <div style='position:absolute;left:{70*s}px;top:{220*s}px;width:{760*s}px'>
          <div style='font-size:{86*s}px;font-weight:850;line-height:1.02;letter-spacing:-2px'>Medical bills,<br><span style='color:#2DD4BF'>explained &amp; settled</span><br>by an agent.</div>
          <div style='margin-top:{40*s}px;display:flex;flex-wrap:wrap;gap:{12*s}px'>
          {''.join(f"<span style='font-size:{26*s}px;font-weight:700;padding:{10*s}px {18*s}px;border-radius:999px;border:2px solid #2DD4BF;color:#2DD4BF'>{c}</span>" for c in ['RCS','SMS','WhatsApp','Email'])}
          </div>
          <div style='margin-top:{36*s}px;font-size:{27*s}px;color:#94A3C0'>Bedrock AgentCore · End User Messaging · SES</div>
        </div>
        <div style='position:absolute;right:{70*s}px;top:{110*s}px;width:{520*s}px;height:{800*s}px;border-radius:{50*s}px;background:#05070C;padding:{12*s}px;box-shadow:0 30px 90px rgba(0,0,0,.6),0 0 0 2px #2A3550'>
          <div style='width:100%;height:100%;border-radius:{40*s}px;background:#fff;color:#1F1F1F;display:flex;flex-direction:column;justify-content:flex-end;gap:{12*s}px;padding:{22*s}px;font-size:{21*s}px;line-height:1.35;overflow:hidden'>
            <div style='margin:-{22*s}px -{22*s}px auto;padding:{26*s}px {22*s}px {16*s}px;background:#F3F6FC;border-bottom:1px solid #E2E8F2;display:flex;gap:{12*s}px;align-items:center;font-weight:700;font-size:{21*s}px'><span style='width:{40*s}px;height:{40*s}px;border-radius:50%;background:#0F766E;color:#fff;display:grid;place-items:center'>R</span><span>Riverbend Family Medicine<br><span style='font-weight:500;font-size:{15*s}px;opacity:.7'>✓ Verified business · RCS</span></span></div>
            <div style='align-self:flex-start;width:88%;border:1px solid #DDE3EE;border-radius:{20*s}px;overflow:hidden'><div style='height:{110*s}px;background:linear-gradient(135deg,#0F766E,#0EA5E9)'></div><div style='padding:{12*s}px {16*s}px;font-size:{19*s}px'><b>New statement ready</b><br>Review it here, no login needed.</div><div style='padding:0 {14*s}px {14*s}px;display:flex;gap:{8*s}px'><span style='border:2px solid #1A73E8;color:#1A73E8;padding:{6*s}px {12*s}px;border-radius:{18*s}px;font-weight:700;font-size:{17*s}px'>Review my bill</span></div></div>
            <div style='align-self:flex-end;background:#1A73E8;color:#fff;padding:{12*s}px {16*s}px;border-radius:{20*s}px'>Why do I owe $283.60?</div>
            <div style='align-self:flex-start;background:#E9EEF6;padding:{12*s}px {16*s}px;border-radius:{20*s}px'>Your plan discounted $194.40. You never owe that. The rest went to your deductible.</div>
            <div style='align-self:flex-end;background:#1A73E8;color:#fff;padding:{12*s}px {16*s}px;border-radius:{20*s}px'>Can I split it?</div>
            <div style='align-self:flex-start;background:#E9EEF6;padding:{12*s}px {16*s}px;border-radius:{20*s}px'>Yes: 4 × $70.90, no interest.</div>
            <div style='display:flex;gap:{8*s}px'>{''.join(f"<span style='border:2px solid #1A73E8;color:#1A73E8;padding:{7*s}px {13*s}px;border-radius:{18*s}px;font-weight:700;font-size:{18*s}px'>{c}</span>" for c in ['Option 3','Pay in full'])}</div>
          </div></div>"""
        screenshot(page(body, w, h), OUT / name, w, h)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    engine = sys.argv[1] if len(sys.argv) > 1 else next(
        e for e in ("agentcore", "bedrock", "offline") if (ROOT / "sim" / "out" / e / "ana_whatsapp.json").exists())
    simdir = ROOT / "sim" / "out" / engine
    j = json.loads((simdir / "jordan_rcs.json").read_text())
    a = json.loads((simdir / "ana_whatsapp.json").read_text())
    tests = run_tests()
    if not all(tests.values()):
        print("warning: failing tests will show as ✗ in the video", tests)

    (ROOT / "docs" / "architecture.html").write_text(arch_html())
    screenshot(arch_html(), ROOT / "docs" / "architecture.png")
    thumbnail()

    clips, srt, t = [], [], 0.0
    for n, sc in enumerate(scenes(j, a, tests)):
        png, wav, mp4 = WORK / f"{sc['name']}.png", WORK / f"{sc['name']}.wav", WORK / f"{sc['name']}.mp4"
        screenshot(sc["html"], png)
        dur = narrate(sc["narration"], wav)
        total = clip(png, wav, dur, mp4)
        srt.append(f"{n+1}\n{srt_time(t+0.25)} --> {srt_time(t+0.25+dur)}\n{sc['narration']}\n")
        clips.append(mp4)
        t += total
        print(f"{sc['name']:<18} {total:5.1f}s  (running {t:5.1f}s)")

    lst = WORK / "concat.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips))
    final = OUT / "settle_demo.mp4"
    sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
       "-c", "copy", "-movflags", "+faststart", str(final))
    (OUT / "settle_demo.srt").write_text("\n".join(srt))
    frames = OUT / "frames"
    frames.mkdir(exist_ok=True)
    for c in clips:
        (frames / c.with_suffix(".png").name).write_bytes(c.with_suffix(".png").read_bytes())
    print(f"\n{final}  {t:.1f}s")


if __name__ == "__main__":
    main()
