output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "athena_workgroup" {
  value = aws_athena_workgroup.main.name
}

output "glue_databases" {
  value = [for db in aws_glue_catalog_database.layer : db.name]
}

output "dev_user" {
  value = aws_iam_user.dev.name
}

output "ec2_instance_profile" {
  value = aws_iam_instance_profile.ec2.name
}
