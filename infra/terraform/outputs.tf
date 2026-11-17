output "api_url" {
  description = "VITE_API_URL for Vercel (aws-guide section 9)."
  value       = aws_lambda_function_url.api.function_url
}

output "mcp_url" {
  value = aws_ssm_parameter.mcp_url.value
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "deploy_role_arn" {
  description = "AWS_ROLE_ARN for the GitHub repository variables (aws-guide section 10)."
  value       = aws_iam_role.github_deploy.arn
}
