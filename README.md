# Recovery-Engine-AWS: Multi-Region Disaster Recovery

![Terraform](https://img.shields.io/badge/Terraform-1.6+-844FBA?style=for-the-badge&logo=terraform&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-2088FF?style=for-the-badge&logo=github-actions&logoColor=white)

## 📌 Project Overview
An enterprise-grade, fully automated Multi-Region Disaster Recovery (DR) architecture provisioned entirely via Terraform. This engine guarantees high availability for mission-critical applications by orchestrating a seamless failover from a primary AWS region (`ap-south-1`) to a secondary region (`ap-southeast-1`) in the event of a catastrophic regional outage.

## ❓ Why? (The Problem it Solves)
Many applications rely on Single-Region architectures or simple Multi-AZ setups, which are highly vulnerable to complete AWS Region outages. Achieving true cross-region high availability usually requires complex "Active-Active" architectures that effectively double your monthly cloud bill. 

This project solves that by implementing a **Warm Standby** architecture. It maintains a scaled-down footprint in the secondary region that costs only ~15% of the primary production environment. When a regional outage is detected, the engine automatically scales the secondary region up and takes over live traffic in under 15 minutes (**RTO**) with nearly zero data loss (**RPO < 5 minutes**) using RDS Cross-Region Replication.

## 🛠️ Tech Stack

| Technology | Used For |
| :--- | :--- |
| **Terraform (HCL)** | Infrastructure as Code (IaC) for multi-region provisioning |
| **Amazon ECS (AWS Fargate)** | Serverless container compute for the primary application |
| **AWS Lambda (Python 3.10)** | Executing the automated disaster recovery failover logic |
| **Amazon RDS (MySQL)** | Primary data store with continuous Cross-Region Read Replicas |
| **Amazon ElastiCache (Redis)** | High-speed data caching |
| **Amazon Route 53** | Global DNS routing and automated health check failover |
| **AWS KMS & Secrets Manager** | Multi-region data encryption and secure credential storage |
| **Amazon CloudWatch & SNS** | System observability, alarm triggering, and admin notifications |
| **GitHub Actions** | CI/CD pipeline (\	flint\, formatting, and Python \pytest\ mock testing) |

## 🏗️ Architecture & How It Works
1. **Normal Operation:** All traffic routes via Route 53 to the Primary ALB in `ap-south-1`. The ECS Fargate tasks connect to the Primary RDS instance. RDS continuously replicates data asynchronously to a Read Replica in `ap-southeast-1`.
2. **Failure Detection:** Route 53 Health Checks monitor the Primary ALB. If it fails consecutively for 60 seconds, a CloudWatch Alarm triggers an SNS topic.
3. **Automated Failover Orchestration:** The SNS topic invokes a Python-based AWS Lambda function in the DR region. 
4. **Recovery Steps:** 
   - The Lambda promotes the RDS Read Replica to a standalone writer.
   - It updates AWS Systems Manager (SSM) Parameter Store with the new database endpoints.
   - It dynamically scales the DR ECS Fargate cluster from 1 task (standby) to the full production desired count.
   - Route 53 automatically updates DNS routing to point to the DR ALB.

## 📂 Repository Structure
```text
Recovery-Engine-AWS/
├── prerequisites/         # S3 backend and DynamoDB lock tables for TF State
├── environments/
│   ├── primary/           # Primary region stack (ap-south-1)
│   └── dr/                # DR region warm-standby stack (ap-southeast-1)
├── modules/
│   ├── networking/        # VPCs, Subnets, NAT, Peering
│   ├── compute/           # ECS Fargate, ALB, Auto Scaling
│   ├── database/          # RDS, ElastiCache
│   ├── security/          # IAM roles, KMS, Secrets Manager
│   ├── storage/           # S3 buckets with Cross-Region Replication
│   ├── monitoring/        # CloudWatch Alarms, SNS, Dashboards
│   └── dns/               # Route 53 Hosted Zones and Failover Records
├── scripts/
│   ├── failover_lambda.py   # The DR Failover Orchestrator (Python)
│   └── simulate_failover.py # Game-day testing CLI tool
├── tests/
│   └── test_failover_lambda.py # Pytest suite using Boto3 Mocking
└── .github/workflows/     # CI pipeline (Terraform validate, tflint, pytest)
```

## 🚀 How to Deploy (Step-by-Step)

### Step 1: Prerequisites
- AWS CLI installed and configured (`aws configure`) with Administrator permissions.
- Terraform >= 1.6 installed locally.

### Step 2: Bootstrap Terraform State
Terraform needs an S3 bucket to securely store its state and prevent concurrent modification conflicts.
```bash
cd prerequisites
terraform init
terraform apply -auto-approve
```
*Take note of the output bucket names and DynamoDB table names.*

### Step 3: Configure Primary Environment
1. Navigate to the primary environment:
   ```bash
   cd ../environments/primary
   ```
2. Open `main.tf` and replace `"REPLACE_WITH_YOUR_STATE_BUCKET"` and `"REPLACE_WITH_YOUR_LOCK_TABLE"` in the `backend "s3"` block with the names generated in Step 2.
3. Copy the example variables file:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
4. Edit `terraform.tfvars` and insert a highly secure `db_master_password`.
5. Deploy the primary infrastructure:
   ```bash
   terraform init
   terraform apply
   ```

### Step 4: Configure DR Environment
1. Navigate to the DR environment:
   ```bash
   cd ../dr
   ```
2. Open `main.tf` and update the backend block with your bucket names (just like you did in Step 3).
3. Deploy the DR stack. *Note: The DR environment will automatically pull the RDS ARN from the primary state to create the cross-region replica.*
   ```bash
   terraform init
   terraform apply
   ```

### Step 5: Game-Day Testing (Simulate a Failure)
You can safely test the automated failover orchestration without manually shutting down AWS services by running the included Python simulation script:
```bash
python scripts/simulate_failover.py --region ap-south-1 --trigger-alarm
```

## 🧹 Teardown Steps
To avoid incurring unnecessary AWS charges when you are done testing, destroy the infrastructure in **reverse order**:
1. **Destroy DR:** 
   ```bash
   cd environments/dr
   terraform destroy -auto-approve
   ```
2. **Destroy Primary:** 
   ```bash
   cd ../primary
   terraform destroy -auto-approve
   ```
3. **Destroy Prerequisites:** 
   ```bash
   cd ../../prerequisites
   terraform destroy -auto-approve
   ```

## 🔒 Security & Best Practices Followed
- **Least Privilege IAM:** All ECS tasks, Lambda functions, and AWS services use strictly scoped IAM roles generated automatically by Terraform.
- **Encryption at Rest:** Multi-region AWS KMS keys are utilized to encrypt S3 buckets, RDS databases, and CloudWatch logs.
- **Secret Management:** Database credentials and Redis auth tokens are randomly generated by Terraform and stored securely in AWS Secrets Manager, ensuring they are never exposed in plaintext logs.
- **Network Isolation:** All compute (ECS) and data (RDS, ElastiCache) resources are placed in private subnets with zero direct inbound internet access.
- **CI/CD Automated Testing:** The repository leverages GitHub Actions to execute `tflint`, enforce HCL formatting, run structural Terraform validations, and execute `pytest` unit tests (using mocked Boto3 clients) on every single commit.
