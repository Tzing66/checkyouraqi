# The public read-only API (plan §10, Phase 5): FastAPI on Lambda behind a Function URL.
# It reads only the world-readable public/ snapshots over HTTPS, so its role grants nothing
# but its own logs. Two steps, because Lambda needs an image to exist before it is created:
#   1. apply with api_enabled=true            -> ECR repository
#   2. scripts/deploy_api.sh (build + push), then apply with api_image_tag=<tag> -> function + URL
# Cost: ECR storage ~$0.10/GB-month (two images kept, ~$0.05); Lambda and the Function URL sit
# inside the always-free tier (1M requests and 400k GB-s a month) at this traffic.

locals {
  api_function_enabled = var.api_enabled && var.api_image_tag != ""
}

resource "aws_ecr_repository" "api" {
  count                = var.api_enabled ? 1 : 0
  name                 = "${var.project}-api"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  count      = var.api_enabled ? 1 : 0
  repository = aws_ecr_repository.api[0].name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the two newest images (current + one rollback)"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 2 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_iam_role" "api_lambda" {
  count = var.api_enabled ? 1 : 0
  name  = "${var.project}-api-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "api" {
  count             = var.api_enabled ? 1 : 0
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = 7
}

data "aws_iam_policy_document" "api_logs" {
  count = var.api_enabled ? 1 : 0
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "api_logs" {
  count  = var.api_enabled ? 1 : 0
  name   = "logs"
  role   = aws_iam_role.api_lambda[0].id
  policy = data.aws_iam_policy_document.api_logs[0].json
}

resource "aws_lambda_function" "api" {
  count         = local.api_function_enabled ? 1 : 0
  function_name = "${var.project}-api"
  role          = aws_iam_role.api_lambda[0].arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api[0].repository_url}:${var.api_image_tag}"
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = 30

  environment {
    variables = {
      PUBLIC_BASE_URL = "https://${aws_s3_bucket.data.bucket_regional_domain_name}"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api_logs]
}

resource "aws_lambda_function_url" "api" {
  count              = local.api_function_enabled ? 1 : 0
  function_name      = aws_lambda_function.api[0].function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["*"]
    allow_methods = ["GET"]
    max_age       = 3600
  }
}

# A public Function URL needs both permissions (AWS requirement since Oct 2025).
resource "aws_lambda_permission" "api_url" {
  count        = local.api_function_enabled ? 1 : 0
  statement_id = "FunctionUrlPublic"
  # One change at a time: Lambda rejects concurrent updates to a function (409).
  depends_on             = [aws_lambda_function_url.api]
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api[0].function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "api_invoke_via_url" {
  count                    = local.api_function_enabled ? 1 : 0
  statement_id             = "FunctionUrlInvoke"
  depends_on               = [aws_lambda_permission.api_url]
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api[0].function_name
  principal                = "*"
  invoked_via_function_url = true
}
