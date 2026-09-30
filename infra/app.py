#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.settle_stack import SettleStack

app = cdk.App()
SettleStack(app, "Settle", env=cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")))
app.synth()
