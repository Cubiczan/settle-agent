# Deploying Settle

Region: `us-east-1` (RCS, WhatsApp and AgentCore Runtime are all available there).

## 0. Prerequisites

* AWS credentials from an IAM role or OIDC, not long-lived access keys. Locally that is `AWS_PROFILE` (or a role already on the machine). In GitHub Actions, use `aws-actions/configure-aws-credentials` with `role-to-assume`. The AgentCore runtime calls Bedrock with its own IAM role; do not put `AWS_ACCESS_KEY_ID` in its environment.
* Node 22 or 24, for the CDK CLI (`npx aws-cdk@2`)
* Amazon Nova enabled in the account. The default model is Nova Pro, `us.amazon.nova-pro-v1:0`, in `us-east-1` (`infra/cdk.json` → `modelId`, or `SETTLE_MODEL_ID`). The runtime role may invoke `foundation-model/amazon.nova-*` and `inference-profile/us.amazon.nova-*` only. Anthropic model ids are rejected: Claude on Bedrock is Marketplace-billed, so promo credits do not cover it.
* One of each channel you want live:
  * **RCS**: an RCS agent in `ACTIVE` testing state with a verified test device ([AWS sample](https://github.com/aws-samples/sample-rcs-agent-setup-and-send-messages))
  * **SMS** (fallback + SMS channel): an origination number or pool
  * **WhatsApp**: a WhatsApp Business Account linked in End User Messaging Social, with a phone number ID and an approved `statement_ready` template (es_MX + en_US)
  * **SES**: a verified sending identity. For inbound email, an MX record pointing to SES.

## 1. Build and deploy

```bash
export AWS_PROFILE=<name> CDK_DEFAULT_REGION=us-east-1
./scripts/package.sh
cd infra
npx aws-cdk@2 bootstrap
npx aws-cdk@2 deploy \
  -c rcsAgentArn=arn:aws:sms-voice:us-east-1:<acct>:rcs-agent/<id> \
  -c smsOrigination=<phone-or-pool-arn> \
  -c waPhoneNumberId=<phone-number-id> \
  -c fromEmail=billing@<your-verified-domain> \
  -c staffEmail=<staff-inbox> \
  -c emailOverrideTo=<verified-inbox>   # only while SES is in sandbox mode
```

## 2. Seed the synthetic ledger

```bash
.venv/bin/python scripts/seed_ledger.py <LedgerTable output>
```

## 3. Wire the channels to the stack

### RCS test agent (fastest path to a real phone)

```bash
./scripts/create_rcs_test_agent.sh            # creates agent + TEST_RCS_LAUNCH_REGISTRATION, writes build/rcs.env
source build/rcs.env                           # wait for TestingAgent.Status = ACTIVE, then:
aws pinpoint-sms-voice-v2 create-verified-destination-number \
  --destination-phone-number <your test phone> --rcs-agent-id $RCS_AGENT_ID
# accept "Make me a tester" from RBM Tester Management on the phone (iPhone: Unknown Senders)
npx aws-cdk@2 deploy -c rcsAgentArn=$RCS_AGENT_ARN ...     # from infra/
.venv/bin/python scripts/seed_ledger.py <LedgerTable> --phone P-1001=<your test phone>   # DynamoDB only
.venv/bin/python scripts/start_demo.py P-1001  # sends the first-touch rich card to the phone
```

### Two-way routing

```bash
# RCS two-way -> SNS (topic policy already allows sms-voice.amazonaws.com)
aws pinpoint-sms-voice-v2 update-rcs-agent --rcs-agent-id <id> \
  --two-way-enabled --two-way-channel-arn <EumInboundTopicArn>

# SMS number two-way -> the same topic
aws pinpoint-sms-voice-v2 update-phone-number --phone-number-id <id> \
  --two-way-enabled --two-way-channel-arn <EumInboundTopicArn>
```

WhatsApp: in the End User Messaging Social console, set the WABA's event destination to `<SocialInboundTopicArn>`.

## 4. Try it

Without any phone channel wired up, you can drive the deployed runtime exactly as the channel Lambdas do:

```bash
.venv/bin/python scripts/invoke_runtime.py      # both demo conversations -> InvokeAgentRuntime
```

With a channel wired up:

Point a test patient's phone in the ledger at your verified test device. Then either text the RCS agent, or trigger the first touch yourself:

```bash
aws lambda invoke --function-name <OutreachFunction> \
  --payload '{"patient_ids":["P-1001"]}' --cli-binary-format raw-in-base64-out /dev/stdout
```

## Tear down

```bash
cd infra && npx aws-cdk@2 destroy
```

## Project site (Vercel)

```bash
./scripts/build_site.sh              # copies video, screenshots and diagram into site/assets (gitignored)
cd site && vercel deploy --prod      # static; no AWS credentials involved
```
