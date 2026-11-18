locals {
  account     = data.aws_caller_identity.current.account_id
  param_arn   = "arn:aws:ssm:${var.region}:${local.account}:parameter"
  demo_reader = { actions = ["ssm:GetParameter"], resources = ["${local.param_arn}${local.demo_param}"] }
  # What each function may do beyond logs and reading its own secrets.
  extra = {
    api = [
      local.demo_reader,
      { actions = ["sqs:SendMessage"], resources = [aws_sqs_queue.jobs.arn] },
    ]
    worker = [
      local.demo_reader,
      {
        # The calls DynamoDBSaver makes, on the checkpoint table only.
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"]
        resources = [aws_dynamodb_table.checkpoints.arn]
      },
      {
        actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
        resources = [aws_sqs_queue.jobs.arn]
      },
    ]
    mcp    = []
    poller = [local.demo_reader]
  }
}

# AWS creates the aws/ssm key with the first SecureString (aws-guide section 6), so
# that section comes before the first plan.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "koa-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "fn" {
  for_each = local.functions
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn[each.key].arn}:*"]
  }
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [for name in local.secrets[each.key] : "${local.param_arn}${var.ssm_prefix}/${name}"]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
  dynamic "statement" {
    for_each = local.extra[each.key]
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "koa-${each.key}"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}
