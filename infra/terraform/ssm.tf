# Placeholders only: real values are written with
#   aws ssm put-parameter --name /checkyouraqi/<name> --type SecureString --overwrite --value ...
# so secrets never pass through Terraform state. Standard tier + aws/ssm key = free.
resource "aws_ssm_parameter" "secret" {
  for_each = var.ssm_parameters
  name     = "/${var.project}/${each.key}"
  type     = "SecureString"
  tier     = "Standard"
  value    = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}
