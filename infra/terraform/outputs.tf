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

output "api_ecr_repository" {
  value = var.api_enabled ? aws_ecr_repository.api[0].repository_url : null
}

output "api_url" {
  value = local.api_function_enabled ? aws_lambda_function_url.api[0].function_url : null
}
