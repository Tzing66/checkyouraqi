# Lets GitHub Actions in this repo's main branch assume an AWS role without stored keys
# (owner decision: weekly training runs on GitHub's free runners, not on the server).

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only workflows running on main of this repo (not forks, PRs or other branches).
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.github_oidc_subject_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_train" {
  name                 = "${var.project}-github-train"
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "github_train" {
  role       = aws_iam_role.github_train.name
  policy_arn = aws_iam_policy.pipeline.arn
}

output "github_train_role_arn" {
  value = aws_iam_role.github_train.arn
}
