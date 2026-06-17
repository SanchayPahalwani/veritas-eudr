# deploy/ — Terraform reference definition

## What this stack provisions

| Resource | Purpose |
|---|---|
| `aws_ecs_cluster` | ECS cluster named `veritas-eudr` |
| `aws_ecs_task_definition` | Fargate task for the API container (port 8000, 1 vCPU / 2 GiB default, 50 GiB ephemeral storage) |
| `aws_ecs_service` | Fargate service, desired count 1, in private subnets |
| `aws_db_instance` | RDS Postgres 16.3 (`db.t4g.small`, 20 GiB gp3) |
| `aws_s3_bucket` | Artifact/raster-tile storage with public access blocked |
| `aws_security_group` (×2) | Service SG (ingress 8000 from ALB CIDRs); DB SG (ingress 5432 from service SG only) |
| `aws_iam_role` | ECS task execution role with `AmazonECSTaskExecutionRolePolicy` |

The `VERITAS_DATABASE_URL` environment variable is composed at plan time from the RDS endpoint so the container connects to the correct instance without hard-coding any address.

## Validated, not applied

No AWS account is associated with this definition. The HCL was written as a reference that documents the intended deployment topology and passes static analysis. Running `terraform apply` would require real AWS credentials, a pre-existing VPC/subnets, an ECR image, and a secrets strategy for `db_password`; none of those prerequisites exist in this context.

The `deploy/.terraform/`, `*.tfstate*`, and `.terraform.lock.hcl` paths are excluded by the repository `.gitignore`.

## PostGIS

PostGIS is **not** pre-enabled by the RDS engine. The Alembic baseline migration (`migrations/versions/0001_baseline.py`) runs `CREATE EXTENSION IF NOT EXISTS postgis;` as the first statement in `upgrade()`. That migration must be executed against the RDS instance before any geometry column can be created.

## Reproducing validate / fmt-check

```sh
# Install Terraform (macOS arm64 example)
mkdir -p /tmp/tf
curl -fsSL -o /tmp/tf/tf.zip \
    https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_darwin_arm64.zip
unzip -o /tmp/tf/tf.zip -d /tmp/tf

# From repo root
cd deploy/
/tmp/tf/terraform init -backend=false
/tmp/tf/terraform validate          # must print: Success! The configuration is valid.
/tmp/tf/terraform fmt -check -recursive  # must exit 0 (no output)
```

Provider versions resolved during the original validation run: `hashicorp/aws v5.100.0`, `hashicorp/random v3.9.0` (see `VALIDATE_OUTPUT.txt`).
