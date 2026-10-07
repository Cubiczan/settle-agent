# Switch default LLM from Claude to Amazon Nova Pro

## Why

The owner's AWS promo credits do not cover Claude on Amazon Bedrock. Claude is billed through AWS Marketplace and is IAM-denied on the account. Amazon Nova is credit-eligible.

## What changes

- Default model id becomes `us.amazon.nova-pro-v1:0` in `us-east-1`.
- Strands `BedrockModel` keeps using the Bedrock Converse API. Anthropic request fields such as `anthropic_version` are not sent.
- `SETTLE_MODEL_ID` and CDK `modelId` still override the id. Any `anthropic.*` id raises `UnsupportedModelError`.
- The AgentCore runtime role may invoke `foundation-model/amazon.nova-*` and `inference-profile/us.amazon.nova-*`, not Anthropic.
- Docs and `.env.example` tell operators to use an IAM role or OIDC rather than long-lived access keys.

## Impact

- Deployed stacks must be redeployed so the runtime role and `SETTLE_MODEL_ID` change.
- `SETTLE_MODEL_ID`, if set to a Claude id in an existing environment, must be removed or changed to a Nova id.
- The published demo video and its captions still describe the previous recording.
