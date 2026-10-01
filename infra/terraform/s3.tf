# Data lake bucket: bronze/ silver/ gold/ public/ mlflow/ reports/ athena-results/ (plan §6).
resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id
  # ACLs stay blocked. Bucket policies are allowed so public_access.tf can make ONLY public/*
  # readable for the hosted dashboard (owner decision 3a, Phase 5).
  block_public_acls       = true
  block_public_policy     = false
  ignore_public_acls      = true
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # The bronze 90-day rule (plan §3.6) is added once bronze JSON is converted to Parquet
  # (Phase 2); expiring raw data before then would lose the only copy.
}
