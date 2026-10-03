#!/usr/bin/env bash
# Build the API's Lambda image (arm64), push it to ECR tagged with the current commit, and print
# the Terraform command that points the function at it. Needs Docker and the admin profile.
#   scripts/deploy_api.sh            # from the repo root, on a clean working tree
set -euo pipefail

PROFILE="${AWS_PROFILE_ADMIN:-checkyouraqi}"
REGION="ap-south-1"
REPO_NAME="checkyouraqi-api"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is not clean: commit first so the image tag matches the code" >&2
  exit 1
fi
TAG="$(git rev-parse --short=12 HEAD)"
REPO_URL="$(aws ecr describe-repositories --profile "$PROFILE" --region "$REGION" \
  --repository-names "$REPO_NAME" --query 'repositories[0].repositoryUri' --output text)"

aws ecr get-login-password --profile "$PROFILE" --region "$REGION" \
  | docker login --username AWS --password-stdin "${REPO_URL%%/*}" >/dev/null
# --provenance=false: Lambda rejects the multi-manifest index buildx makes by default.
docker buildx build --platform linux/arm64 --provenance=false -f api/Dockerfile \
  -t "$REPO_URL:$TAG" --push .

echo
echo "pushed $REPO_NAME:$TAG. Now set api_image_tag = \"$TAG\" in"
echo "infra/terraform/deployed.auto.tfvars, then in infra/terraform:"
echo "  terraform plan -out=api.tfplan && terraform apply api.tfplan"
