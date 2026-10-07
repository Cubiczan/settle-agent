"""Amazon Bedrock AgentCore Runtime entrypoint.

Payload in:  {"inbound": {"channel", "address", "text", "message_id", "to"}}
Payload out: {"reply": {"text", "suggestions", "link"} | null, "trace": [...], "conversation_id", "state"}

AgentCore gives each conversation its own isolated session (runtimeSessionId =
conversation id), so one patient's context can never bleed into another's.
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from settle.channels import Inbound
from settle.model import resolve_model_id
from settle.router import default_deps, handle

# Fail at startup if SETTLE_MODEL_ID is an Anthropic id, before the first patient message.
resolve_model_id()

app = BedrockAgentCoreApp()
DEPS = default_deps()


@app.entrypoint
def invoke(payload: dict) -> dict:
    return handle(Inbound(**payload["inbound"]), DEPS)


if __name__ == "__main__":
    app.run()
