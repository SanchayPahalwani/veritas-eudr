provider "aws" {
  region = var.aws_region
}

# ── Random suffix so the S3 bucket name is globally unique ───────────────────
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# ── ECS Cluster ───────────────────────────────────────────────────────────────
resource "aws_ecs_cluster" "main" {
  name = "veritas-eudr"
}

# ── Security Groups ───────────────────────────────────────────────────────────

# Service SG: ingress on 8000 from ALB CIDRs; egress unrestricted.
resource "aws_security_group" "service" {
  name        = "veritas-eudr-service"
  description = "ECS Fargate tasks: allow inbound 8000 from ALB, unrestricted egress."
  vpc_id      = var.vpc_id

  ingress {
    description = "API port from ALB"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = var.alb_cidr_blocks
  }

  egress {
    description = "Unrestricted egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# DB SG: ingress on 5432 from the service SG only.
resource "aws_security_group" "db" {
  name        = "veritas-eudr-db"
  description = "RDS Postgres: allow inbound 5432 from service SG only."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from ECS service"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.service.id]
  }

  egress {
    description = "Unrestricted egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── RDS Postgres ──────────────────────────────────────────────────────────────
# NOTE: PostGIS is NOT a pre-enabled extension on the managed RDS engine.
# The Alembic baseline migration (0001_baseline.py) runs
#   CREATE EXTENSION IF NOT EXISTS postgis;
# on first `alembic upgrade head`, which is the correct and only place to
# enable the extension. RDS for PostgreSQL bundles PostGIS in its library path
# but requires that explicit CREATE EXTENSION statement at runtime.
resource "aws_db_subnet_group" "main" {
  name       = "veritas-eudr"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_instance" "main" {
  identifier             = "veritas-eudr"
  engine                 = "postgres"
  engine_version         = "16.3"
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_storage
  storage_type           = "gp3"
  db_name                = "veritas"
  username               = "veritas"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  skip_final_snapshot    = true
  deletion_protection    = false

  # Parameter group: rds.force_ssl and search_path kept at defaults.
  # PostGIS is installed via CREATE EXTENSION in the Alembic baseline migration,
  # not via a custom parameter group or RDS option group.
}

# ── S3 Bucket (clipped raster tiles / run artifacts) ─────────────────────────
resource "aws_s3_bucket" "artifacts" {
  bucket = "veritas-eudr-artifacts-${random_id.bucket_suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── IAM Role for ECS Task Execution ──────────────────────────────────────────
data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "veritas-eudr-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ── ECS Task Definition ───────────────────────────────────────────────────────
resource "aws_ecs_task_definition" "api" {
  family                   = "veritas-eudr-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.task_execution.arn

  ephemeral_storage {
    # Rasterio clips can be large; 50 GiB provides headroom for the
    # fixtures_dir and any temporary raster work within the task.
    size_in_gib = 50
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.app_image
      essential = true

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "VERITAS_DATABASE_URL"
          value = "postgresql+psycopg://veritas:${var.db_password}@${aws_db_instance.main.address}:5432/veritas"
        },
        {
          name  = "VERITAS_WHISP_LIVE"
          value = "false"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/veritas-eudr"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
          "awslogs-create-group"  = "true"
        }
      }
    }
  ])
}

# ── ECS Service ───────────────────────────────────────────────────────────────
resource "aws_ecs_service" "api" {
  name            = "veritas-eudr-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }
}
