#!/bin/bash
# Safe deploy — plan first, confirm, then apply + push image.
# Usage: ./scripts/deploy.sh [prod]
set -euo pipefail

ENV="${1:-prod}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="$ROOT/terraform/environments/$ENV"
AWS_REGION="${AWS_REGION:-eu-west-1}"
# Resolved at runtime so no account ID is hardcoded in the repo.
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/jamf-snipeit-suite-prod"

if [ ! -d "$TF_DIR" ]; then
  echo "ERROR: $TF_DIR does not exist"; exit 1
fi

cd "$ROOT"

# 1. Python syntax check
echo "=== [1/6] Syntax check ==="
python3 -c "import ast, pathlib; [ast.parse(f.read_text()) for f in pathlib.Path('src').rglob('*.py')]"

# 2. Tests if present
if [ -d "tests" ]; then
  echo "=== [2/6] Tests ==="
  python3 -m pytest tests/ -q || { echo "Tests failed"; exit 1; }
else
  echo "=== [2/6] Tests: skipped (no tests/ dir) ==="
fi

# 3. Terraform plan
echo "=== [3/6] Terraform plan ==="
cd "$TF_DIR"
eval "$(aws configure export-credentials --format env 2>/dev/null)" || true
terraform init -input=false -upgrade=false >/dev/null
terraform plan -out=/tmp/tfplan-$ENV

# 4. Confirm
echo
read -p "Apply plan to $ENV? [y/N] " ok
[ "$ok" = "y" ] || { echo "Aborted"; exit 1; }

# 5. Apply Terraform
echo "=== [4/6] Terraform apply ==="
terraform apply /tmp/tfplan-$ENV

# 6. Build + push image
cd "$ROOT"
echo "=== [5/6] Docker build ==="
docker build --platform linux/amd64 -t "$ECR:latest" .

echo "=== [6/6] Docker push ==="
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${ECR%/*}"
docker push "$ECR:latest"

echo
echo "Deploy complete."
