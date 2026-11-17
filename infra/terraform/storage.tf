# Checkpoints for analysis runs (workflow/checkpoints.py). Provisioned, not on-demand:
# always-free covers 25 RCU / 25 WCU; a run writes ~17 KB checkpoints in bursts.
resource "aws_dynamodb_table" "checkpoints" {
  name           = "knock-on-atlas-checkpoints" # workflow/checkpoints.py:TABLE
  billing_mode   = "PROVISIONED"
  read_capacity  = 10
  write_capacity = 10
  hash_key       = "PK"
  range_key      = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  ttl {
    attribute_name = "ttl" # the attribute DynamoDBSaver writes (30 days)
    enabled        = true
  }
}

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "koa-jobs-dlq"
  message_retention_seconds = 1209600 # 14 days to look at a failed job
}

resource "aws_sqs_queue" "jobs" {
  name = "koa-jobs"
  # At least 6x the worker's 300 s timeout, so a running job isn't redelivered.
  visibility_timeout_seconds = 1800
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })
}

# The deploy zip (~70 MB) is over Lambda's 50 MB direct upload, so CI uploads it here.
resource "aws_s3_bucket" "artifacts" {
  bucket = "koa-artifacts-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-old-builds"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1 # parts of a failed 70 MB upload
    }
  }
}
