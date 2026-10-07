"""Everything Settle needs, in one stack.

  EUM SMS/RCS ──SNS──► λ eum-sms ─┐
  EUM Social (WhatsApp) ─SNS─► λ eum-social ─┼─► AgentCore Runtime (Strands agent) ──► Bedrock (Nova Pro)
  SES inbound ─S3─► λ ses-inbound ┘            │  DynamoDB: ledger · state · audit · tickets
                                               └─► SES (confirmations, staff alerts)
  EventBridge (daily) ─► λ outreach ─► RCS rich card (SMS fallback) / WhatsApp template / email

Run scripts/package.sh first; this stack uploads build/runtime.zip and build/lambda/.
"""
import sys
from pathlib import Path

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_bedrockagentcore as agentcore,
    aws_dynamodb as ddb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as sources,
    aws_s3 as s3,
    aws_s3_assets as assets,
    aws_s3_notifications as s3n,
    aws_ses as ses,
    aws_ses_actions as ses_actions,
    aws_sns as sns,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settle.model import DEFAULT_MODEL_ID, bedrock_invoke_policy, resolve_model_id  # noqa: E402


class SettleStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        ctx = lambda k: self.node.try_get_context(k) or ""

        # --- data -----------------------------------------------------------
        def table(name, pk, sk=None, ttl=None):
            t = ddb.Table(self, name, partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                          sort_key=sk, billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                          encryption=ddb.TableEncryption.AWS_MANAGED, point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
                          time_to_live_attribute=ttl, removal_policy=RemovalPolicy.DESTROY)
            return t

        ledger = table("Ledger", "pk", ddb.Attribute(name="sk", type=ddb.AttributeType.STRING))
        ledger.add_global_secondary_index(index_name="by_address",
                                          partition_key=ddb.Attribute(name="address", type=ddb.AttributeType.STRING))
        state = table("Conversations", "conversation_id", ttl="expires_at")
        audit = table("Audit", "conversation_id", ddb.Attribute(name="seq", type=ddb.AttributeType.NUMBER))
        tickets = table("Tickets", "ticket_id")

        env = {
            "SETTLE_LEDGER_TABLE": ledger.table_name, "SETTLE_STATE_TABLE": state.table_name,
            "SETTLE_AUDIT_TABLE": audit.table_name, "SETTLE_TICKETS_TABLE": tickets.table_name,
            "SETTLE_FROM_EMAIL": ctx("fromEmail"), "SETTLE_STAFF_EMAIL": ctx("staffEmail"),
            "SETTLE_SES_CONFIG_SET": "settle",
            "SETTLE_EMAIL_OVERRIDE_TO": ctx("emailOverrideTo"),
        }

        ses.ConfigurationSet(self, "SesConfig", configuration_set_name="settle")

        # --- AgentCore Runtime ---------------------------------------------
        runtime_role = iam.Role(self, "RuntimeRole",
                                assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        # Nova foundation models and US Nova inference profiles only.
        nova = bedrock_invoke_policy(self.region, self.account)
        runtime_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeNova", actions=nova["actions"], resources=nova["resources"]))
        runtime_role.add_to_policy(iam.PolicyStatement(
            sid="ApplyGuardrail", actions=["bedrock:ApplyGuardrail"],
            resources=[f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/*"]))
        runtime_role.add_to_policy(iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"]))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                     "xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"],
            resources=["*"]))
        ledger.grant_read_data(runtime_role)
        state.grant_read_write_data(runtime_role)
        audit.grant(runtime_role, "dynamodb:PutItem", "dynamodb:Query")  # append + read; no update/delete
        tickets.grant_write_data(runtime_role)

        bundle = assets.Asset(self, "RuntimeBundle", path=str(ROOT / "build" / "runtime.zip"))
        bundle.grant_read(runtime_role)
        runtime = agentcore.CfnRuntime(
            self, "Runtime", agent_runtime_name="settle_billing_agent", role_arn=runtime_role.role_arn,
            description="Settle patient-billing agent (Strands on Bedrock)",
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                code_configuration=agentcore.CfnRuntime.CodeConfigurationProperty(
                    code=agentcore.CfnRuntime.CodeProperty(s3=agentcore.CfnRuntime.S3LocationProperty(
                        bucket=bundle.s3_bucket_name, prefix=bundle.s3_object_key)),
                    entry_point=["main.py"], runtime="PYTHON_3_12")),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
            environment_variables={**env, "SETTLE_MODEL_ID": resolve_model_id(
                                       (ctx("modelId") or "").strip() or DEFAULT_MODEL_ID),
                                   "SETTLE_MODEL": "bedrock"})
        runtime.node.add_dependency(runtime_role)

        # --- channel Lambdas -------------------------------------------------
        code = lambda_.Code.from_asset(str(ROOT / "build" / "lambda"))
        lambda_env = {**env, "SETTLE_RUNTIME_ARN": runtime.attr_agent_runtime_arn,
                      "SETTLE_RCS_AGENT_ARN": ctx("rcsAgentArn"), "SETTLE_SMS_ORIGINATION": ctx("smsOrigination"),
                      "SETTLE_WA_PHONE_NUMBER_ID": ctx("waPhoneNumberId")}

        def fn(name, handler, timeout=60):
            f = lambda_.Function(self, name, runtime=lambda_.Runtime.PYTHON_3_12,
                                 architecture=lambda_.Architecture.ARM_64, code=code, handler=handler,
                                 timeout=Duration.seconds(timeout), memory_size=512, environment=lambda_env)
            f.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[runtime.attr_agent_runtime_arn, f"{runtime.attr_agent_runtime_arn}/*"]))
            f.add_to_role_policy(iam.PolicyStatement(
                actions=["sms-voice:SendRcsMessage", "sms-voice:SendTextMessage",
                         "social-messaging:SendWhatsAppMessage", "ses:SendEmail"], resources=["*"]))
            return f

        eum_topic = sns.Topic(self, "EumInbound", display_name="Settle SMS/RCS inbound")
        eum_topic.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("sms-voice.amazonaws.com")], actions=["sns:Publish"],
            resources=[eum_topic.topic_arn]))
        social_topic = sns.Topic(self, "SocialInbound", display_name="Settle WhatsApp inbound")
        social_topic.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("social-messaging.amazonaws.com")], actions=["sns:Publish"],
            resources=[social_topic.topic_arn]))

        fn("EumSmsFn", "lambdas.channel_handlers.eum_sms_handler").add_event_source(sources.SnsEventSource(eum_topic))
        fn("EumSocialFn", "lambdas.channel_handlers.eum_social_handler").add_event_source(
            sources.SnsEventSource(social_topic))

        mail = s3.Bucket(self, "InboundMail", encryption=s3.BucketEncryption.S3_MANAGED,
                         block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
                         removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)
        ses_fn = fn("SesInboundFn", "lambdas.channel_handlers.ses_inbound_handler")
        mail.grant_read(ses_fn)
        mail.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(ses_fn))
        if ctx("fromEmail"):
            ses.ReceiptRuleSet(self, "Inbound", rules=[ses.ReceiptRuleOptions(
                recipients=[ctx("fromEmail")], actions=[ses_actions.S3(bucket=mail, object_key_prefix="in/")])])

        outreach = fn("OutreachFn", "lambdas.outreach.handler", timeout=300)
        ledger.grant_read_data(outreach)
        events.Rule(self, "DailyOutreach", schedule=events.Schedule.cron(hour="16", minute="0"),
                    targets=[targets.LambdaFunction(outreach)], enabled=False)

        CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, "EumInboundTopicArn", value=eum_topic.topic_arn)
        CfnOutput(self, "SocialInboundTopicArn", value=social_topic.topic_arn)
        CfnOutput(self, "LedgerTable", value=ledger.table_name)
        CfnOutput(self, "OutreachFunction", value=outreach.function_name)
