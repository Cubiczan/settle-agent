# Devpost submission: Settle

**Tagline:** Medical bills, explained and settled by an agent, in the chat app the patient already uses.

## Inspiration
Patient balances have become a large part of what a medical practice collects, and they're the hardest part to explain. A statement full of CPT and claim adjustment codes shows the patient a number, but not why they owe it. So patients call during business hours, wait on hold, or put it off until the balance goes to collections. Billing teams answer the same three questions all day: *why do I owe this, can I split it, and I already paid.*

## What it does
Settle is an agent that handles those conversations over **RCS, SMS, WhatsApp and email**:
- **Explains** the bill line by line from the adjudicated claim: what was billed, what insurance discounted (never owed), and the patient's share and why (deductible, copay, coinsurance).
- **Arranges** payment using only options the practice's policy engine issues: pay in full with a prompt-pay discount, or up to four interest-free installments. Enrolment needs the patient's own "yes". A hosted payment link comes back in the thread, and Amazon SES emails the confirmation.
- **Escalates** disputes, hardship and anything unusual. For "I already paid", it searches the account read-only, finds the unapplied front-desk payment, and opens a staff ticket with the evidence and the exact fix.

It works in the patient's language. The demo shows English over RCS and Spanish over WhatsApp.

## How we built it
- **Amazon Bedrock AgentCore Runtime** hosts a **Strands** agent (Claude on Amazon Bedrock) with 10 tools. There's one isolated runtime session per conversation.
- **AWS End User Messaging**: RCS via `SendRcsMessage`, with rich cards, suggestion chips and SMS `FallbackConfiguration`. SMS via `SendTextMessage`. Two-way traffic for both arrives on SNS.
- **AWS End User Messaging Social**: WhatsApp, using templates for the first touch, reply buttons, and inbound webhook events via SNS.
- **Amazon SES**: plan confirmations, staff alerts, and inbound email through a receipt rule to S3.
- **DynamoDB** holds the ledger, conversation state (with TTL), an append-only audit log and tickets. **Lambda** runs the channel adapters, and **EventBridge** runs daily outreach.
- Everything is defined in **AWS CDK** (Python), including the AgentCore `CfnRuntime` with direct code deployment.

## The key design decision: policy lives in code, not the prompt
The model chooses what to say and which tool to call. The tools decide what's allowed, and each rule has a unit test:
- There's no account access until identity is verified (the number on file plus date of birth). Three misses locks the chat and pages staff.
- Balances can be shared once the patient is verified, but services, dates and providers need consent for that channel.
- Money terms come only from the policy engine. `accept_offer` needs an offer ID the engine issued **and** an explicit yes in the patient's latest message, so the model can't fabricate consent.
- The agent never moves money. Card numbers never enter the chat.
- STOP/HELP are handled before any model call, and unknown senders learn nothing.
- Every tool call goes into a SHA-256 hash-chained audit log that contains no PHI.
- The first touch (an RCS rich card or a WhatsApp template) carries no health information.

## Challenges
- Omnichannel rendering: RCS allows up to 4 suggestion chips, WhatsApp 3 reply buttons, and SMS gets a plain list. One `Reply` object renders natively on each.
- Choosing where PHI can safely appear. The answer is consent-gated detail on top of verification, rather than a single yes/no.
- Making the demo reproducible without live phone numbers. An offline planner drives the exact same tools and guardrails, so the tests and the demo exercise the same code the Bedrock agent uses.

## What's next
- AgentCore Gateway to expose the tools to a practice-management system via MCP
- AgentCore Memory for longitudinal patient preferences
- A staff console for tickets
- Payment-processor integration for the hosted link
- Price estimates before a visit

## Built with
amazon-bedrock-agentcore, amazon-bedrock, strands-agents, aws-end-user-messaging, rcs, sms, whatsapp, amazon-ses, amazon-sns, aws-lambda, amazon-dynamodb, amazon-eventbridge, aws-cdk, python

## WhatsApp integration (for the Best of WhatsApp prize)
The WhatsApp channel runs on AWS End User Messaging Social:
- Business-initiated outreach uses an approved `statement_ready` template (es_MX / en_US).
- Replies use interactive reply buttons, rendered from the agent's `suggest_replies` tool.
- Inbound messages and button taps arrive as `whatsAppWebhookEntry` events on SNS and are parsed in `settle/channels/__init__.py`.
- Replies are sent with `socialmessaging.send_whatsapp_message`.

The same agent, guardrails and audit trail apply as on RCS and SMS.
