# llm-selection

How Settle chooses the Amazon Bedrock model for the Strands agent.

## Requirements

### Requirement: Default model is Amazon Nova Pro

The system SHALL use Amazon Nova Pro on the Bedrock Converse API when no model id is configured. The default model id SHALL be `us.amazon.nova-pro-v1:0` and the default region SHALL be `us-east-1`.

#### Scenario: No override

- **WHEN** `SETTLE_MODEL_ID` is unset or blank
- **THEN** the resolved model id is `us.amazon.nova-pro-v1:0`

#### Scenario: CDK context matches the default

- **WHEN** the stack is synthesized with the committed `infra/cdk.json`
- **THEN** the runtime environment variable `SETTLE_MODEL_ID` is `us.amazon.nova-pro-v1:0`

### Requirement: Model id can be overridden

The system SHALL honor `SETTLE_MODEL_ID` and the CDK context key `modelId` when the value is not an Anthropic model id.

#### Scenario: Another Nova id

- **WHEN** `SETTLE_MODEL_ID` is `amazon.nova-lite-v1:0`
- **THEN** the resolved model id is `amazon.nova-lite-v1:0`

### Requirement: Anthropic model ids are rejected

The system SHALL refuse model ids whose provider segment is `anthropic`, including `anthropic.*`, `us.anthropic.*`, `eu.anthropic.*`, `global.anthropic.*`, and foundation-model ARNs for those ids. The error SHALL name Amazon Nova as the replacement and SHALL state that Claude on Bedrock is billed through AWS Marketplace.

#### Scenario: Cross-region Claude id

- **WHEN** the model id is `us.anthropic.claude-opus-5-5`
- **THEN** resolution raises `UnsupportedModelError` before a Bedrock client is constructed

### Requirement: Runtime IAM allows Nova only

The AgentCore runtime role SHALL be allowed to call `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on `foundation-model/amazon.nova-*` and on `inference-profile/us.amazon.nova-*`. The invoke statement SHALL NOT grant Anthropic foundation models or Anthropic inference profiles.

#### Scenario: Policy resources

- **WHEN** the invoke policy is built for region `us-east-1` and an account id
- **THEN** the resources include `arn:aws:bedrock:*::foundation-model/amazon.nova-*` and `arn:aws:bedrock:us-east-1:<account>:inference-profile/us.amazon.nova-*`
- **AND** no resource contains `anthropic`

### Requirement: Converse API without Anthropic request fields

The Strands agent SHALL construct `BedrockModel` for the Converse API. The arguments SHALL NOT include `anthropic_version` or other Anthropic InvokeModel request fields, and SHALL NOT include long-lived AWS access keys.

#### Scenario: Default client arguments

- **WHEN** the agent is built with no guardrail and `AWS_REGION=us-east-1`
- **THEN** the BedrockModel arguments are the Nova Pro model id, `max_tokens` 4096, and `region_name` `us-east-1`

### Requirement: Credentials prefer a role

Operators SHALL authenticate with an IAM role or OIDC. Documentation and `.env.example` SHALL tell operators not to set `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY`. The runtime role, not static keys, SHALL be what calls Bedrock.
