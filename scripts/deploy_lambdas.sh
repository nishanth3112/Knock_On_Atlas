#!/usr/bin/env bash
# Deploy lambda-dist/lambda.zip to the four functions and move each `live` alias.
#
# Runs in the deploy workflow with the OIDC role (no keys). The zip is over Lambda's
# 50 MB direct upload, so it goes through the artifacts bucket. Every function gets
# the same code; each publishes a version, and `live` moves only after all four
# updated, so a failed update leaves every alias on the old code.
#
# Usage: scripts/deploy_lambdas.sh <commit sha>
set -euo pipefail
cd "$(dirname "$0")/.."

sha=$1
account=$(aws sts get-caller-identity --query Account --output text)
bucket="koa-artifacts-${account}"
key="lambda/${sha}.zip"
functions=(api worker mcp poller)

aws s3 cp --only-show-errors lambda-dist/lambda.zip "s3://${bucket}/${key}"
for fn in "${functions[@]}"; do
  aws lambda update-function-code --function-name "koa-${fn}" \
    --s3-bucket "$bucket" --s3-key "$key" --output text --query LastUpdateStatus
done
declare -A versions
for fn in "${functions[@]}"; do
  aws lambda wait function-updated --function-name "koa-${fn}"
  versions[$fn]=$(aws lambda publish-version --function-name "koa-${fn}" \
    --description "$sha" --query Version --output text)
done
for fn in "${functions[@]}"; do
  aws lambda update-alias --function-name "koa-${fn}" --name live \
    --function-version "${versions[$fn]}" --query FunctionVersion --output text
  echo "koa-${fn}: live -> version ${versions[$fn]}"
done
