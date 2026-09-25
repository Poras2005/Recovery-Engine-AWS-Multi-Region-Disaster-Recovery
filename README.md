# AWS Multi-Region Warm Standby DR — Terraform Infrastructure

Production-grade Terraform for a **Warm Standby** Disaster Recovery architecture across two AWS regions.

## Architecture

| Region | Role | Cost Model |
|---|---|---|
| `ap-south-1` | Primary — serves 100% of live traffic | Full production sizing |
| `ap-southeast-1` | DR — warm standby, scales on failover | ~15% of primary cost |

**RPO:** < 5 minutes (RDS Read Replica)  
**RTO:** < 15 minutes (automated failover Lambda)

## Repository Structure

```
terraform-aws-dr/
├── environments/
│   ├── primary/          # ap-south-1 full-stack environment
│   └── dr/               # ap-southeast-1 warm standby environment
├── modules/
│   ├── networking/       # VPC, subnets, IGW, NAT, VPC peering
│   ├── compute/          # ECS Fargate cluster + services
│   ├── database/         # RDS MySQL with Cross-Region Replica, ElastiCache
│   ├── storage/          # S3 with CRR, AWS Backup vault
│   ├── dns/              # Route 53 hosted zone, health checks, failover records
│   ├── monitoring/       # CloudWatch alarms, dashboards, SNS
│   ├── security/         # IAM roles, KMS keys, Security Groups, Secrets Manager
│   └── backup/           # AWS Backup plans and vaults
├── scripts/
│   ├── failover_lambda.py     # Automated failover orchestrator
│   └── simulate_failover.py   # Game-day testing script (Python)
└── policies/
    └── failover_lambda_policy.json
```

## Quick Start

### Prerequisites
- AWS CLI configured with profiles for both accounts (or single account)
- Terraform >= 1.6
- Two AWS regions available

### 1. Bootstrap remote state (run once)
```bash
cd environments/primary
terraform init
terraform apply -target=aws_s3_bucket.terraform_state -target=aws_dynamodb_table.terraform_locks
```

### 2. Deploy primary region
```bash
cd environments/primary
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform plan
terraform apply
```

### 3. Deploy DR region
```bash
cd environments/dr
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set primary_global_cluster_id from primary output
terraform init
terraform plan
terraform apply
```

### 4. Verify replication
```bash
# Check RDS replication lag
aws cloudwatch get-metric-statistics \
  --region ap-southeast-1 \
  --namespace AWS/RDS \
  --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=myapp-dr-db \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Average
```

### 5. Simulate failover (game day)
```bash
pip install boto3
python scripts/simulate_failover.py --dry-run
python scripts/simulate_failover.py --execute   # triggers real failover
```

## Key Variables

| Variable | Description | Example |
|---|---|---|
| `app_name` | Application name prefix | `myapp` |
| `primary_region` | Primary AWS region | `us-east-1` |
| `dr_region` | DR AWS region | `eu-west-1` |
| `domain_name` | Route 53 hosted zone | `example.com` |
| `db_master_password` | Aurora master password (use Secrets Manager) | — |
| `container_image` | ECR image URI | `123456789.dkr.ecr.us-east-1.amazonaws.com/myapp:latest` |

## Cost Estimate

| Component | Primary/mo | DR/mo |
|---|---|---|
| RDS MySQL (db.t3.micro) | ~$15 | ~$15 |
| ECS Fargate (2 tasks) | ~$30 | ~$15 (1 task) |
| ElastiCache (cache.t3.micro) | ~$12 | ~$12 |
| ALB + NAT + data transfer | ~$120 | ~$60 |
| Route 53 + health checks | ~$10 | ~$5 |
| S3 CRR + Backup | ~$30 | ~$15 |
| **Total** | **~$920** | **~$173** |

*Estimates only. Actual costs depend on data transfer, request volume, and region pricing.*

## Failover

Failover is **fully automated** via:
1. Route 53 health check → detects primary ALB failure (3 × 10s checks)
2. CloudWatch Alarm → fires after 60s of sustained failure
3. SNS → triggers failover Lambda
4. Lambda → promotes RDS Read Replica, scales ECS Fargate tasks, updates SSM params
5. Route 53 → automatically flips DNS to DR ALB (via failover record)

Total automated failover time: **< 15 minutes** (dominated by RDS Read Replica promotion).

## Testing

Run game days quarterly:
```bash
# Inject 503s on the primary health check endpoint
aws elbv2 modify-rule --rule-arn <health-check-rule-arn> \
  --actions Type=fixed-response,FixedResponseConfig='{StatusCode=503}'
```

Watch CloudWatch dashboards and verify DNS flips to DR region automatically.
