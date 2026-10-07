"""Model resolution: Nova Pro is the default, and Anthropic ids are refused."""
import json
from pathlib import Path

import pytest

from settle.audit import AuditLog, MemoryAuditSink
from settle.ledger import JsonLedger
from settle.model import (
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    UnsupportedModelError,
    bedrock_invoke_policy,
    resolve_model_id,
)
from settle.state import ConversationState
from settle.tools import Effects, ToolContext

ROOT = Path(__file__).resolve().parents[1]


def test_default_model_is_nova_pro(monkeypatch):
    monkeypatch.delenv("SETTLE_MODEL_ID", raising=False)
    assert resolve_model_id() == "us.amazon.nova-pro-v1:0"
    assert DEFAULT_MODEL_ID == "us.amazon.nova-pro-v1:0"
    assert DEFAULT_REGION == "us-east-1"


def test_blank_model_id_falls_back_to_nova(monkeypatch):
    monkeypatch.setenv("SETTLE_MODEL_ID", "   ")
    assert resolve_model_id() == DEFAULT_MODEL_ID


def test_env_override_accepts_other_nova_ids(monkeypatch):
    monkeypatch.setenv("SETTLE_MODEL_ID", " amazon.nova-lite-v1:0 ")
    assert resolve_model_id() == "amazon.nova-lite-v1:0"
    assert resolve_model_id("us.amazon.nova-micro-v1:0") == "us.amazon.nova-micro-v1:0"


@pytest.mark.parametrize("model_id", [
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-opus-5-5",
    "eu.anthropic.claude-sonnet-4-5",
    "global.anthropic.claude-sonnet-4-20250514-v1:0",
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
    "US.ANTHROPIC.claude-opus-5-5",
])
def test_anthropic_ids_are_rejected(model_id):
    with pytest.raises(UnsupportedModelError, match="Amazon Nova") as exc:
        resolve_model_id(model_id)
    message = str(exc.value)
    assert "Marketplace" in message
    assert model_id in message or model_id.lower() in message.lower()


def test_explicit_id_does_not_read_a_bad_environment(monkeypatch):
    monkeypatch.setenv("SETTLE_MODEL_ID", "us.anthropic.claude-opus-5-5")
    assert resolve_model_id(DEFAULT_MODEL_ID) == DEFAULT_MODEL_ID
    with pytest.raises(UnsupportedModelError):
        resolve_model_id()


def test_nova_iam_allows_foundation_models_and_us_inference_profiles():
    policy = bedrock_invoke_policy("us-east-1", "111122223333")
    resources = " ".join(policy["resources"])
    assert "foundation-model/amazon.nova-*" in resources
    assert "inference-profile/us.amazon.nova-*" in resources
    assert "arn:aws:bedrock:us-east-1:111122223333:inference-profile/us.amazon.nova-*" in policy["resources"]
    assert "arn:aws:bedrock:*::foundation-model/amazon.nova-*" in policy["resources"]
    assert "bedrock:InvokeModel" in policy["actions"]
    assert "bedrock:InvokeModelWithResponseStream" in policy["actions"]
    blob = json.dumps(policy).lower()
    assert "anthropic" not in blob
    assert "claude" not in blob


def test_cdk_context_matches_the_nova_default():
    cdk = json.loads((ROOT / "infra" / "cdk.json").read_text())
    stack = (ROOT / "infra" / "stacks" / "settle_stack.py").read_text()
    assert cdk["context"]["modelId"] == DEFAULT_MODEL_ID
    assert "resolve_model_id" in stack
    assert "bedrock_invoke_policy" in stack
    assert "claude" not in stack.lower()
    assert "anthropic" not in stack.lower()
    example = (ROOT / ".env.example").read_text()
    assert "SETTLE_MODEL_ID=us.amazon.nova-pro-v1:0" in example
    assert "AWS_ACCESS_KEY_ID" in example  # named only so operators know not to set it


def _ctx():
    st = ConversationState("phone:+12065550142", "rcs", "+12065550142", "P-1001")
    return ToolContext(
        st, JsonLedger(), AuditLog(MemoryAuditSink()),
        Effects(lambda to, s, b: "m", lambda t: "TCK-1", lambda *a: "https://pay.example"),
    )


def test_strands_bedrock_model_stores_nova_and_region(monkeypatch):
    """Construct the real Strands BedrockModel. That builds a client and does not call Bedrock."""
    monkeypatch.delenv("SETTLE_MODEL_ID", raising=False)
    monkeypatch.delenv("SETTLE_GUARDRAIL_ID", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    from strands.models import BedrockModel

    from settle.agent import bedrock_model_kwargs

    model = BedrockModel(**bedrock_model_kwargs())
    assert model.config["model_id"] == DEFAULT_MODEL_ID
    assert model.client.meta.region_name == "us-east-1"
    assert "anthropic_version" not in model.config
    assert "additional_request_fields" not in model.config


def test_build_agent_uses_nova_converse_without_anthropic_fields(monkeypatch):
    monkeypatch.delenv("SETTLE_MODEL_ID", raising=False)
    monkeypatch.delenv("SETTLE_GUARDRAIL_ID", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    captured = {}

    class FakeModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("settle.agent.BedrockModel", FakeModel)
    monkeypatch.setattr("settle.agent.Agent", FakeAgent)

    from settle.agent import build_agent

    build_agent(_ctx())
    assert captured == {
        "model_id": DEFAULT_MODEL_ID,
        "max_tokens": 4096,
        "region_name": "us-east-1",
    }
    assert "anthropic_version" not in captured
    for secret in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
        assert secret not in captured


def test_guardrail_config_does_not_add_anthropic_request_fields(monkeypatch):
    monkeypatch.setenv("SETTLE_GUARDRAIL_ID", "gr-123")
    monkeypatch.setenv("SETTLE_GUARDRAIL_VERSION", "1")
    from settle.agent import bedrock_model_kwargs

    cfg = bedrock_model_kwargs()
    assert cfg["model_id"] == DEFAULT_MODEL_ID
    assert cfg["guardrail_id"] == "gr-123"
    assert cfg["guardrail_version"] == "1"
    assert cfg["guardrail_redact_input"] is False
    assert "anthropic_version" not in cfg
    assert "additional_request_fields" not in cfg


def test_build_agent_rejects_anthropic_before_constructing_the_model(monkeypatch):
    monkeypatch.setenv("SETTLE_MODEL_ID", "us.anthropic.claude-opus-5-5")
    constructed = []

    class FakeModel:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr("settle.agent.BedrockModel", FakeModel)
    from settle.agent import build_agent

    with pytest.raises(UnsupportedModelError, match="Nova"):
        build_agent(_ctx())
    assert constructed == []


def test_retired_claude_default_is_not_in_source():
    """Product code and docs must not still default to the old Claude id.

    Tests and the OpenSpec scenario name that id on purpose, as the value that is rejected.
    """
    banned = "us.anthropic.claude-opus-5-5"
    roots = ["settle", "infra", "agentcore", "scripts", "lambdas", "sim", "media", "docs", "site"]
    files = ["README.md", ".env.example"]
    hits = []
    paths = [ROOT / name for name in files]
    for name in roots:
        paths.extend(p for p in (ROOT / name).rglob("*") if p.is_file())
    skip_suffixes = {".png", ".jpg", ".jpeg", ".mp4", ".pyc", ".webp"}
    for path in paths:
        if path.suffix.lower() in skip_suffixes or "__pycache__" in path.parts:
            continue
        if banned in path.read_text(errors="ignore"):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
