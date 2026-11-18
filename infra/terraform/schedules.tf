# The poller's jobs (spec section 15). With the demo off the poller returns before
# connecting to Neon, so these wake no database between demos.
locals {
  schedules = {
    edgar-poll      = "cron(0/5 10-23 ? * MON-FRI *)"
    refresh-studies = "cron(30 22 * * ? *)" # after the US close
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "koa-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "koa-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_alias.live["poller"].arn
    }]
  })
}

resource "aws_scheduler_schedule" "poller" {
  for_each                     = local.schedules
  name                         = "koa-${each.key}"
  schedule_expression          = each.value
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_alias.live["poller"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = each.key })
    retry_policy {
      maximum_retry_attempts = 0 # the next run picks up anything missed
    }
  }
}
