# One least-privilege policy shared by the local dev user and the (Phase 5) EC2 role:
# this bucket, the aqi_* Glue databases, this Athena workgroup, /checkyouraqi/* SSM params.

locals {
  glue_prefix = "arn:aws:glue:${var.region}:${local.account_id}"
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "S3Bucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.data.arn]
  }

  statement {
    sid       = "S3Objects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }

  statement {
    sid = "Glue"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases",
      "glue:GetTable", "glue:GetTables", "glue:GetTableVersions",
      "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable",
      "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition",
      "glue:CreatePartition", "glue:BatchCreatePartition", "glue:UpdatePartition",
      "glue:DeletePartition", "glue:BatchDeletePartition", "glue:BatchUpdatePartition",
    ]
    resources = [
      "${local.glue_prefix}:catalog",
      "${local.glue_prefix}:database/aqi_*",
      "${local.glue_prefix}:table/aqi_*/*",
    ]
  }

  statement {
    sid = "Athena"
    actions = [
      "athena:StartQueryExecution", "athena:StopQueryExecution",
      "athena:GetQueryExecution", "athena:GetQueryResults", "athena:BatchGetQueryExecution",
      "athena:GetQueryRuntimeStatistics", "athena:ListQueryExecutions", "athena:GetWorkGroup",
      "athena:GetDataCatalog", "athena:GetDatabase", "athena:ListDatabases",
      "athena:GetTableMetadata", "athena:ListTableMetadata",
    ]
    resources = [
      aws_athena_workgroup.main.arn,
      "arn:aws:athena:${var.region}:${local.account_id}:datacatalog/AwsDataCatalog",
    ]
  }

  statement {
    sid       = "SsmRead"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/${var.project}/*"]
  }
}

resource "aws_iam_policy" "pipeline" {
  name   = "${var.project}-pipeline"
  policy = data.aws_iam_policy_document.pipeline.json
}

# Local development / Airflow on the Mac. Access key is created by hand in the console
# (keeps the secret out of Terraform state) and stored as the `checkyouraqi-dev` CLI profile.
resource "aws_iam_user" "dev" {
  name = "${var.project}-dev"
}

resource "aws_iam_user_policy_attachment" "dev" {
  user       = aws_iam_user.dev.name
  policy_arn = aws_iam_policy.pipeline.arn
}

# Role for the Phase 5 EC2 instance. Creating it now is free and keeps permissions in one place.
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project}-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ec2" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.pipeline.arn
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project}-ec2"
  role = aws_iam_role.ec2.name
}
