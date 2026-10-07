"""Which Bedrock model Settle is allowed to call.

Amazon Nova Pro via the US cross-region inference profile is the default.
Strands' BedrockModel sends that id to the Bedrock Converse API.

Claude on Bedrock is billed through AWS Marketplace, so AWS promo credits do
not cover it, and it is IAM-denied on this account. Any `anthropic.*` model
id (including cross-region forms such as `us.anthropic.*`) is rejected.
"""
from __future__ import annotations

import os
import re

DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
DEFAULT_REGION = "us-east-1"

# Provider segment, so this matches anthropic.claude-*, us.anthropic.*,
# and an ARN whose resource is foundation-model/anthropic.*.
_ANTHROPIC = re.compile(r"(^|[./:])anthropic\.", re.IGNORECASE)

INVOKE_ACTIONS = (
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:GetInferenceProfile",
)


class UnsupportedModelError(ValueError):
    """SETTLE_MODEL_ID or the CDK modelId selects a model this app must not call."""


def resolve_model_id(model_id: str | None = None) -> str:
    """Return the Bedrock model id for the Converse API.

    `None` reads `SETTLE_MODEL_ID`, then falls back to Amazon Nova Pro.
    A blank value also falls back to that default. An explicit id is used
    as given and does not consult the environment.
    """
    if model_id is None:
        model_id = os.environ.get("SETTLE_MODEL_ID")
    mid = (model_id or "").strip()
    if not mid:
        return DEFAULT_MODEL_ID
    if _ANTHROPIC.search(mid):
        raise UnsupportedModelError(
            f"Refusing Anthropic model id {mid!r}. "
            "Claude on Amazon Bedrock is billed through AWS Marketplace, so AWS promo credits "
            "do not cover it, and it is IAM-denied on this account. "
            f"Use an Amazon Nova model id such as {DEFAULT_MODEL_ID} "
            f"(Bedrock Converse API, region {DEFAULT_REGION}). "
            "Override with SETTLE_MODEL_ID or the CDK context key modelId."
        )
    return mid


def bedrock_invoke_policy(region: str, account: str) -> dict[str, list[str]]:
    """IAM actions and resource ARNs for Nova, and not for Anthropic.

    A `us.amazon.nova-*` inference profile routes across US regions, so the
    foundation-model grant is regional-wildcard. The inference-profile grant
    is the system profile in this account (`inference-profile/us.amazon.nova-*`).
    """
    return {
        "actions": list(INVOKE_ACTIONS),
        "resources": [
            f"arn:aws:bedrock:{region}::foundation-model/amazon.nova-*",
            "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
            f"arn:aws:bedrock:{region}:{account}:inference-profile/us.amazon.nova-*",
        ],
    }
