# The single Airflow server (plan §10, Phase 5). Created only when var.ec2_enabled = true.
# No inbound ports, no SSH key: it bootstraps itself from user-data (bootstrap.sh.tftpl) and
# runs with the least-privilege instance role. SSM is attached only as an emergency door.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "default_a" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.region}a"
  default_for_az    = true
}

resource "aws_security_group" "airflow" {
  name        = "${var.project}-airflow"
  description = "Airflow server: no inbound access; outbound for APIs, S3, Athena, SSM."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "All outbound (API calls, package installs, AWS endpoints)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_instance" "airflow" {
  count = var.ec2_enabled ? 1 : 0

  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  instance_type               = var.ec2_instance_type
  subnet_id                   = data.aws_subnet.default_a.id
  vpc_security_group_ids      = [aws_security_group.airflow.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true # needed for outbound internet without a NAT gateway

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.ec2_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # so containers can reach the instance role
  }

  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    region     = var.region
    bucket     = aws_s3_bucket.data.bucket
    repo_url   = "https://github.com/${var.github_repo}.git"
    ssm_prefix = "/${var.project}"
  })
  user_data_replace_on_change = false

  # Ignore AMI and bootstrap edits so neither stops or replaces a running server; they apply
  # to the next server (`terraform apply -replace=aws_instance.airflow[0]`).
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "${var.project}-airflow"
  }
}

output "airflow_instance_id" {
  value = try(aws_instance.airflow[0].id, null)
}
