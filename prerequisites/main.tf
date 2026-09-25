provider "aws" {
  region = "ap-south-1"
  alias  = "primary"
}

provider "aws" {
  region = "ap-southeast-1"
  alias  = "dr"
}

variable "project_prefix" {
  description = "A unique prefix for your state buckets (e.g., your company name)"
  type        = string
  default     = "myapp-tfstate"
}

# ── Primary Region (ap-south-1) State ─────────────────────────────
resource "aws_s3_bucket" "primary_state" {
  provider = aws.primary
  bucket   = "${var.project_prefix}-primary-ap-south-1"
}

resource "aws_s3_bucket_versioning" "primary_versioning" {
  provider = aws.primary
  bucket   = aws_s3_bucket.primary_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_dynamodb_table" "primary_lock" {
  provider     = aws.primary
  name         = "${var.project_prefix}-primary-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
}

# ── DR Region (ap-southeast-1) State ──────────────────────────────
resource "aws_s3_bucket" "dr_state" {
  provider = aws.dr
  bucket   = "${var.project_prefix}-dr-ap-southeast-1"
}

resource "aws_s3_bucket_versioning" "dr_versioning" {
  provider = aws.dr
  bucket   = aws_s3_bucket.dr_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_dynamodb_table" "dr_lock" {
  provider     = aws.dr
  name         = "${var.project_prefix}-dr-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
}

output "primary_state_bucket" {
  value = aws_s3_bucket.primary_state.bucket
}

output "primary_lock_table" {
  value = aws_dynamodb_table.primary_lock.name
}

output "dr_state_bucket" {
  value = aws_s3_bucket.dr_state.bucket
}

output "dr_lock_table" {
  value = aws_dynamodb_table.dr_lock.name
}
