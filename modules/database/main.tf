# modules/database/main.tf
# RDS MySQL (Primary + Cross-Region Read Replica) + ElastiCache Redis

# ── Subnet Groups ────────────────────────────────────────────────
resource "aws_db_subnet_group" "rds" {
  name        = "${var.app_name}-${var.environment}-rds-subnet-group"
  subnet_ids  = var.private_data_subnet_ids
  description = "RDS subnet group for ${var.environment}"
  tags        = merge(var.tags, { Name = "${var.app_name}-${var.environment}-rds-subnet-group" })
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.app_name}-${var.environment}-redis-subnet-group"
  subnet_ids = var.private_data_subnet_ids
  tags       = merge(var.tags, { Name = "${var.app_name}-${var.environment}-redis-subnet-group" })
}

# ── RDS Instance ──────────────────────────────────────────────────
resource "aws_db_instance" "main" {
  identifier = "${var.app_name}-${var.environment}-db"
  
  # Engine config only applied to primary
  engine               = var.is_primary ? "mysql" : null
  engine_version       = var.is_primary ? "8.0" : null
  instance_class       = "db.t3.micro"
  
  # For replica, replicate_source_db must be set (reusing global_cluster_id var for simplicity)
  replicate_source_db  = var.is_primary ? null : var.global_cluster_id 

  # Credentials only for primary
  username             = var.is_primary ? var.db_master_username : null
  password             = var.is_primary ? var.db_master_password : null
  db_name              = var.is_primary ? var.database_name : null

  # db_subnet_group_name can't be set on cross-region read replicas in some cases, but generally it's fine
  db_subnet_group_name   = var.is_primary ? aws_db_subnet_group.rds.name : null
  vpc_security_group_ids = [var.database_sg_id]
  kms_key_id             = var.is_primary ? var.kms_key_arn : null
  storage_encrypted      = true
  allocated_storage      = 20
  max_allocated_storage  = 100

  backup_retention_period      = var.backup_retention_days
  backup_window                = "02:00-03:00"
  maintenance_window           = "sun:04:00-sun:05:00"

  deletion_protection          = var.deletion_protection
  skip_final_snapshot          = var.skip_final_snapshot
  final_snapshot_identifier    = "${var.app_name}-${var.environment}-final-snapshot"
  
  multi_az = var.is_primary ? true : false

  tags = merge(var.tags, { Name = "${var.app_name}-${var.environment}-db" })
}

# ── ElastiCache Redis ─────────────────────────────────────────────
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.app_name}-${var.environment}-redis"
  description          = "Redis cluster for ${var.app_name} ${var.environment}"

  node_type            = var.redis_node_type
  num_cache_clusters   = var.is_primary ? var.redis_num_replicas : 1
  port                 = 6379

  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [var.cache_sg_id]

  at_rest_encryption_enabled  = true
  transit_encryption_enabled  = true
  auth_token                  = var.redis_auth_token
  kms_key_id                  = var.kms_key_arn

  automatic_failover_enabled = var.is_primary && var.redis_num_replicas > 1 ? true : false
  multi_az_enabled           = var.is_primary && var.redis_num_replicas > 1 ? true : false

  snapshot_retention_limit = var.backup_retention_days
  snapshot_window          = "03:00-04:00"
  maintenance_window       = "sun:05:00-sun:06:00"

  parameter_group_name = aws_elasticache_parameter_group.redis.name

  tags = merge(var.tags, { Name = "${var.app_name}-${var.environment}-redis" })
}

resource "aws_elasticache_parameter_group" "redis" {
  family = "redis7"
  name   = "${var.app_name}-${var.environment}-redis-params"

  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }
  parameter {
    name  = "notify-keyspace-events"
    value = ""
  }
  tags = var.tags
}

# ── SSM Parameters for connection info ───────────────────────────
resource "aws_ssm_parameter" "db_endpoint" {
  name  = "/${var.app_name}/${var.environment}/db/endpoint"
  type  = "SecureString"
  value = aws_db_instance.main.endpoint
  key_id = var.kms_key_arn
  tags  = var.tags
}

resource "aws_ssm_parameter" "db_reader_endpoint" {
  name  = "/${var.app_name}/${var.environment}/db/reader_endpoint"
  type  = "SecureString"
  value = aws_db_instance.main.endpoint
  key_id = var.kms_key_arn
  tags  = var.tags
}

resource "aws_ssm_parameter" "redis_endpoint" {
  name  = "/${var.app_name}/${var.environment}/cache/endpoint"
  type  = "SecureString"
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
  key_id = var.kms_key_arn
  tags  = var.tags
}
