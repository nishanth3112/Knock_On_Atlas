locals {
  functions = {
    api    = { memory = 512, timeout = 30 }
    worker = { memory = 1024, timeout = 300 } # spec wall clock 240 s, plus slack
    mcp    = { memory = 1024, timeout = 60 }
    poller = { memory = 512, timeout = 300 }
  }
  demo_param = "${var.ssm_prefix}/DEMO_LIVE_UNTIL"
  logfire    = var.with_logfire ? ["LOGFIRE_TOKEN"] : []
  jev_key = {
    off        = []
    typesafe   = ["TYPESAFE_API_KEY"]
    openrouter = ["OPENROUTER_API_KEY"]
  }
  # The SSM parameters each function loads at cold start (lambdas/secrets.py).
  secrets = {
    api = concat(["DATABASE_URL", "JWT_SECRET", "SEC_USER_AGENT"], local.logfire)
    worker = concat(
      ["DATABASE_URL", "MCP_JWT_SECRET", "SEC_USER_AGENT", "GROQ_API_KEY", "MCP_URL"],
      local.jev_key[var.jev_route],
      local.logfire,
    )
    # JWT_SECRET (API logins) stays in koa-api; the worker and MCP share MCP_JWT_SECRET.
    mcp    = concat(["DATABASE_URL", "MCP_JWT_SECRET", "SEC_USER_AGENT"], local.logfire)
    poller = concat(["DATABASE_URL", "SEC_USER_AGENT", "TWELVEDATA_API_KEY"], local.logfire)
  }
  env = {
    api = {
      JOB_QUEUE       = "sqs"
      JOB_QUEUE_URL   = aws_sqs_queue.jobs.url
      DEMO_LIVE_PARAM = local.demo_param
    }
    worker = {
      CHECKPOINTER    = "dynamodb"
      LLM_PROVIDER    = "groq"
      JEV_ROUTE       = var.jev_route
      DEMO_LIVE_PARAM = local.demo_param
    }
    mcp    = {}
    poller = { DEMO_LIVE_PARAM = local.demo_param }
  }
}

# First apply only: CI replaces the code on every merge to main (ignore_changes below).
data "archive_file" "stub" {
  type        = "zip"
  output_path = "${path.module}/.stub/lambda-stub.zip"
  source {
    filename = "knock_on_atlas/__init__.py"
    content  = ""
  }
  source {
    filename = "knock_on_atlas/lambdas/__init__.py"
    content  = ""
  }
  dynamic "source" {
    for_each = local.functions
    content {
      filename = "knock_on_atlas/lambdas/${source.key}.py"
      content  = "def handler(event, context):\n    raise RuntimeError('not deployed yet: merge to main')\n"
    }
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/koa-${each.key}"
  retention_in_days = 7
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "koa-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "knock_on_atlas.lambdas.${each.key}.handler"
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  filename         = data.archive_file.stub.output_path
  source_code_hash = data.archive_file.stub.output_base64sha256
  publish          = true

  environment {
    variables = merge(local.env[each.key], {
      SSM_PARAMETERS = join(",", [for name in local.secrets[each.key] : "${var.ssm_prefix}/${name}"])
    })
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }
  lifecycle {
    ignore_changes = [filename, source_code_hash, s3_bucket, s3_key, s3_object_version]
  }
}

# Function URLs, the SQS trigger and the schedules call `live`; CI moves it to each
# new version. A Terraform change to a function's settings reaches `live` with the
# next deploy (re-run the deploy workflow).
resource "aws_lambda_alias" "live" {
  for_each         = local.functions
  name             = "live"
  function_name    = aws_lambda_function.fn[each.key].function_name
  function_version = aws_lambda_function.fn[each.key].version
  lifecycle {
    ignore_changes = [function_version]
  }
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.fn["api"].function_name
  qualifier          = aws_lambda_alias.live["api"].name
  authorization_type = "NONE" # the API checks its own JWTs
  cors {
    allow_origins = [var.allowed_origin]
    allow_methods = ["GET", "POST"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_lambda_function_url" "mcp" {
  function_name      = aws_lambda_function.fn["mcp"].function_name
  qualifier          = aws_lambda_alias.live["mcp"].name
  authorization_type = "NONE" # fastmcp checks the worker's JWT (aud koa-mcp)
}

# A NONE function URL needs both statements; InvokedViaFunctionUrl keeps the second
# from allowing any other way of invoking the function.
resource "aws_lambda_permission" "url" {
  for_each               = toset(["api", "mcp"])
  statement_id           = "FunctionUrlPublic"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.fn[each.key].function_name
  qualifier              = aws_lambda_alias.live[each.key].name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke" {
  for_each                 = toset(["api", "mcp"])
  statement_id             = "FunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.fn[each.key].function_name
  qualifier                = aws_lambda_alias.live[each.key].name
  principal                = "*"
  invoked_via_function_url = true
}

# The worker reads the MCP URL from SSM: referencing the function URL directly in its
# environment would make the functions depend on themselves.
resource "aws_ssm_parameter" "mcp_url" {
  name  = "${var.ssm_prefix}/MCP_URL"
  type  = "String"
  value = "${aws_lambda_function_url.mcp.function_url}mcp"
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_alias.live["worker"].arn
  batch_size       = 1
  scaling_config {
    maximum_concurrency = 2 # instead of reserved concurrency, which new accounts lack
  }
}
