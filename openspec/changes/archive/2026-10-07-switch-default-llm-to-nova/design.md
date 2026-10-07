# Design

## Decision

Call Amazon Nova through Strands `BedrockModel`, which uses the Bedrock Converse API (`Converse` / `ConverseStream`). The default id is the US system inference profile `us.amazon.nova-pro-v1:0`, invoked from `us-east-1`.

Resolution lives in `settle/model.py` so the agent, the runtime entrypoint, and the CDK stack share one check. A blank id falls back to Nova Pro. An explicit id is not combined with the environment. Anthropic is detected by a provider segment (`anthropic.`), which covers on-demand ids, geo inference profiles, and foundation-model ARNs.

The runtime role's invoke statement lists Nova foundation models (this region and `*`, because the US profile can route to other US regions) and `inference-profile/us.amazon.nova-*` in this account. `bedrock:GetInferenceProfile` is included on those resources so the profile can be resolved. `bedrock:ApplyGuardrail` stays on `guardrail/*` in the stack account. Marketplace subscribe actions are not granted.

`BedrockModel` is given `model_id`, `max_tokens`, and `region_name` only (plus guardrail fields when `SETTLE_GUARDRAIL_ID` is set). Access keys are not passed; boto3 uses the default chain (runtime role, `AWS_PROFILE`, or OIDC).

## Not in this change

The checked-in demo video, its `.srt`, and the phone screenshots are recordings. Their source script now says Nova Pro, but the media files were not re-rendered.
