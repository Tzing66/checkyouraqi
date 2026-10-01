# public/ holds the small Parquet snapshots the hosted dashboard reads over HTTPS (owner decision
# 3a). Only that prefix is world-readable; everything else stays private. ACLs stay blocked;
# only a bucket policy grants access (see aws_s3_bucket_public_access_block.data).

data "aws_iam_policy_document" "data_bucket_public_read" {
  statement {
    sid       = "PublicReadOfPublicPrefixOnly"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/public/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "data_public_read" {
  bucket     = aws_s3_bucket.data.id
  policy     = data.aws_iam_policy_document.data_bucket_public_read.json
  depends_on = [aws_s3_bucket_public_access_block.data]
}

output "public_base_url" {
  description = "PUBLIC_BASE_URL for the hosted dashboard / API."
  value       = "https://${aws_s3_bucket.data.bucket}.s3.${var.region}.amazonaws.com"
}
