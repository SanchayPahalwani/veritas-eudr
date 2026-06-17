output "ecs_service_name" {
  description = "Name of the ECS service running the veritas-eudr API."
  value       = aws_ecs_service.api.name
}

output "db_endpoint" {
  description = "RDS Postgres endpoint (host:port) for the veritas-eudr database."
  value       = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

output "artifacts_bucket_name" {
  description = "S3 bucket name for clipped raster tiles and run artifacts."
  value       = aws_s3_bucket.artifacts.bucket
}
