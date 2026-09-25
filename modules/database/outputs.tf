# modules/database/outputs.tf
output "aurora_cluster_id" { value = aws_db_instance.main.id }
output "aurora_cluster_endpoint" { value = aws_db_instance.main.endpoint }
output "aurora_reader_endpoint" { value = aws_db_instance.main.endpoint }
output "aurora_cluster_arn" { value = aws_db_instance.main.arn }
output "global_cluster_id" { value = aws_db_instance.main.arn } # Repurposing to pass primary ARN
output "redis_primary_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "redis_reader_endpoint" { value = aws_elasticache_replication_group.redis.reader_endpoint_address }
output "redis_replication_group_id" { value = aws_elasticache_replication_group.redis.id }
