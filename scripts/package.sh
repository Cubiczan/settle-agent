#!/usr/bin/env bash
# Build the two deployment artifacts CDK uploads:
#   build/runtime.zip  - AgentCore Runtime direct-code bundle (linux/arm64, Python 3.12)
#   build/lambda/      - channel Lambdas (bundles a boto3 new enough for SendRcsMessage)
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/runtime build/lambda build/runtime.zip && mkdir -p build/runtime build/lambda

uv pip install -q --python-version 3.12 --python-platform aarch64-manylinux2014 \
  --target build/runtime -r agentcore/requirements.txt
cp -r settle data build/runtime/
cp agentcore/app.py build/runtime/main.py
(cd build/runtime && zip -qr ../runtime.zip . -x '*.pyc' -x '__pycache__/*')

uv pip install -q --python-version 3.12 --python-platform aarch64-manylinux2014 \
  --target build/lambda "boto3>=1.43.37"
cp -r settle data lambdas build/lambda/
echo "built build/runtime.zip ($(du -h build/runtime.zip | cut -f1)) and build/lambda/"
