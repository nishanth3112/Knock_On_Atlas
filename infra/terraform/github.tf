# CI deploys code through OIDC: no AWS keys in GitHub. The role can update the four
# functions' code and aliases, upload the zip, and flip the demo switch; nothing else
# (no iam:*, no PassRole), and only from main.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "koa-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:PublishVersion",
      "lambda:UpdateAlias",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
    ]
    resources = flatten([
      for fn in aws_lambda_function.fn : [fn.arn, "${fn.arn}:*"]
    ])
  }
  statement {
    # Lambda reads the uploaded zip with the caller's permissions.
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
    # S3 ARNs carry no account: never a same-named bucket someone else owns.
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account]
    }
  }
  statement {
    actions   = ["ssm:PutParameter"]
    resources = ["${local.param_arn}${local.demo_param}"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "koa-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
