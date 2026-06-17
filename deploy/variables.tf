variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-west-1"
}

variable "app_image" {
  description = "Fully-qualified container image URI for the veritas-eudr API (e.g. 123456789.dkr.ecr.eu-west-1.amazonaws.com/veritas-eudr:sha-abc1234)."
  type        = string
}

variable "db_password" {
  description = "Password for the RDS Postgres master user. Must be supplied at runtime; no default."
  type        = string
  sensitive   = true
}

variable "vpc_id" {
  description = "ID of the VPC in which all resources are placed."
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for the ECS tasks and RDS instance."
  type        = list(string)
}

variable "alb_cidr_blocks" {
  description = "CIDR blocks permitted to reach port 8000 on the ECS service (e.g. the ALB's subnets or a VPC CIDR)."
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "container_cpu" {
  description = "CPU units for each ECS Fargate task (1 vCPU = 1024)."
  type        = number
  default     = 1024
}

variable "container_memory" {
  description = "Memory (MiB) for each ECS Fargate task."
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of ECS task replicas to run."
  type        = number
  default     = 1
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage" {
  description = "Initial allocated storage for RDS (GiB)."
  type        = number
  default     = 20
}
