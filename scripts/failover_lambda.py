"""
failover_lambda.py
Automated DR failover orchestrator.
Triggered by SNS → CloudWatch Alarm when primary region health checks fail.

Sequence:
  1. Validate this is not a false-positive (idempotency check)
  2. Promote RDS Read Replica to standalone writer
  3. Update SSM parameters to point app at DR DB endpoint
  4. Scale ECS service to production desired count
  5. Publish completion event to SNS
  6. Return summary for CloudWatch Logs
"""

import boto3
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment Variables (set by Terraform) ──────────────────────
# Note: DR_CLUSTER_ID is re-used from the old Aurora setup, but now maps to the RDS DB Instance ID
DR_DB_INSTANCE_ID  = os.environ["DR_CLUSTER_ID"]
ECS_CLUSTER        = os.environ["ECS_CLUSTER"]
ECS_SERVICE        = os.environ["ECS_SERVICE"]
PROD_TASK_COUNT    = int(os.environ["PROD_TASK_COUNT"])
APP_NAME           = os.environ["APP_NAME"]
DR_REGION          = os.environ["DR_REGION"]
NOTIFICATION_TOPIC = os.environ["NOTIFICATION_TOPIC"]

# ── AWS Clients ───────────────────────────────────────────────────
rds = boto3.client("rds",              region_name=DR_REGION)
ecs = boto3.client("ecs",              region_name=DR_REGION)
ssm = boto3.client("ssm",              region_name=DR_REGION)
sns = boto3.client("sns",              region_name=DR_REGION)
aas = boto3.client("application-autoscaling", region_name=DR_REGION)


# ── Helpers ───────────────────────────────────────────────────────
def log_step(step: int, message: str) -> None:
    logger.info(f"[FAILOVER STEP {step}] {message}")


def publish_notification(subject: str, message: dict) -> None:
    """Publish status to SNS for PagerDuty / Slack / email."""
    try:
        sns.publish(
            TopicArn=NOTIFICATION_TOPIC,
            Subject=subject,
            Message=json.dumps(message, indent=2, default=str),
        )
    except Exception as e:
        logger.error(f"Failed to publish SNS notification: {e}")


def is_already_writer() -> bool:
    """Check if the DR database is already a standalone writer (idempotency)."""
    resp = rds.describe_db_instances(DBInstanceIdentifier=DR_DB_INSTANCE_ID)
    instance = resp["DBInstances"][0]
    # If there's no replication source, it is standalone
    return instance.get("ReadReplicaSourceDBInstanceIdentifier") is None


def get_dr_db_endpoint() -> str:
    """Fetch the writer endpoint of the DR RDS instance."""
    resp = rds.describe_db_instances(DBInstanceIdentifier=DR_DB_INSTANCE_ID)
    return resp["DBInstances"][0]["Endpoint"]["Address"]


# ── Step Functions ────────────────────────────────────────────────
def step_promote_rds(dry_run: bool) -> str:
    """
    Promote the DR RDS Read Replica to standalone writer.
    Returns the new writer endpoint.
    """
    log_step(1, f"Initiating RDS Read Replica promotion → {DR_DB_INSTANCE_ID}")

    if is_already_writer():
        log_step(1, "DR database already standalone writer — skipping RDS promotion")
        return get_dr_db_endpoint()

    if dry_run:
        log_step(1, "[DRY RUN] Would call rds.promote_read_replica()")
        return "dry-run-endpoint.rds.amazonaws.com"

    rds.promote_read_replica(
        DBInstanceIdentifier=DR_DB_INSTANCE_ID
    )

    log_step(1, "Waiting for RDS promotion to complete...")
    
    # Waiter for the DB instance to become available again
    waiter = rds.get_waiter('db_instance_available')
    try:
        waiter.wait(
            DBInstanceIdentifier=DR_DB_INSTANCE_ID,
            WaiterConfig={"Delay": 20, "MaxAttempts": 45}  # Wait up to 15 mins
        )
    except Exception as e:
        raise TimeoutError(f"RDS promotion did not complete or failed: {e}")

    endpoint = get_dr_db_endpoint()
    log_step(1, f"RDS promotion complete. New writer endpoint: {endpoint}")
    return endpoint


def step_update_ssm_parameters(db_endpoint: str, dry_run: bool) -> None:
    """Update SSM parameters so running ECS tasks pick up the new DB host on restart."""
    log_step(2, f"Updating SSM parameters with new DB endpoint: {db_endpoint}")

    params = {
        f"/{APP_NAME}/dr/db/endpoint":         db_endpoint,
        f"/{APP_NAME}/dr/db/reader_endpoint":  db_endpoint, # Reusing for reader
        f"/{APP_NAME}/dr/failover/timestamp":  datetime.now(timezone.utc).isoformat(),
        f"/{APP_NAME}/dr/failover/status":     "active",
    }

    for name, value in params.items():
        if dry_run:
            log_step(2, f"[DRY RUN] Would set SSM {name} = {value}")
        else:
            ssm.put_parameter(
                Name=name,
                Value=value,
                Type="String",
                Overwrite=True,
            )
            log_step(2, f"SSM updated: {name}")


def step_scale_ecs(dry_run: bool) -> int:
    """Scale ECS service to production task count."""
    log_step(3, f"Scaling ECS service {ECS_SERVICE} to {PROD_TASK_COUNT} tasks")

    if dry_run:
        log_step(3, f"[DRY RUN] Would set desiredCount={PROD_TASK_COUNT}")
        return PROD_TASK_COUNT

    # Update the auto-scaling minimum so it won't scale back down
    aas.register_scalable_target(
        ServiceNamespace="ecs",
        ResourceId=f"service/{ECS_CLUSTER}/{ECS_SERVICE}",
        ScalableDimension="ecs:service:DesiredCount",
        MinCapacity=max(2, PROD_TASK_COUNT // 2),
        MaxCapacity=PROD_TASK_COUNT * 3,
    )

    # Set desired count directly
    ecs.update_service(
        cluster=ECS_CLUSTER,
        service=ECS_SERVICE,
        desiredCount=PROD_TASK_COUNT,
    )

    # Wait for tasks to reach steady state
    log_step(3, "Waiting for ECS service to reach steady state...")
    waiter = ecs.get_waiter("services_stable")
    waiter.wait(
        cluster=ECS_CLUSTER,
        services=[ECS_SERVICE],
        WaiterConfig={"Delay": 15, "MaxAttempts": 40},  # 10 minutes
    )

    resp    = ecs.describe_services(cluster=ECS_CLUSTER, services=[ECS_SERVICE])
    running = resp["services"][0]["runningCount"]
    log_step(3, f"ECS steady state reached. Running tasks: {running}")
    return running


# ── Main Handler ──────────────────────────────────────────────────
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.
    Triggered by SNS. Parses the SNS message and executes failover steps.
    """
    started_at = datetime.now(timezone.utc)
    dry_run    = os.environ.get("DRY_RUN", "false").lower() == "true"

    logger.info(f"Failover orchestrator invoked. DRY_RUN={dry_run}")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    publish_notification(
        subject=f"[{APP_NAME}] DR Failover INITIATED",
        message={
            "app":       APP_NAME,
            "status":    "initiated",
            "dry_run":   dry_run,
            "timestamp": started_at.isoformat(),
            "region":    DR_REGION,
        },
    )

    result: dict[str, Any] = {
        "app":           APP_NAME,
        "dry_run":       dry_run,
        "started_at":    started_at.isoformat(),
        "steps":         {},
    }

    try:
        # ── Step 1: Promote RDS ───────────────────────────────────
        db_endpoint = step_promote_rds(dry_run)
        result["steps"]["rds_promotion"] = {
            "status":   "success",
            "endpoint": db_endpoint,
        }

        # ── Step 2: Update SSM ────────────────────────────────────
        step_update_ssm_parameters(db_endpoint, dry_run)
        result["steps"]["ssm_update"] = {"status": "success"}

        # ── Step 3: Scale ECS ─────────────────────────────────────
        running_count = step_scale_ecs(dry_run)
        result["steps"]["ecs_scale"] = {
            "status":        "success",
            "running_tasks": running_count,
        }

        # ── Summary ───────────────────────────────────────────────
        completed_at               = datetime.now(timezone.utc)
        elapsed                    = (completed_at - started_at).total_seconds()
        result["completed_at"]     = completed_at.isoformat()
        result["elapsed_seconds"]  = elapsed
        result["status"]           = "success"

        log_step(0, f"Failover completed successfully in {elapsed:.0f}s")

        publish_notification(
            subject=f"[{APP_NAME}] DR Failover COMPLETED ✓ ({elapsed:.0f}s)",
            message=result,
        )

    except Exception as e:
        result["status"] = "failed"
        result["error"]  = str(e)
        logger.error(f"Failover FAILED: {e}", exc_info=True)

        publish_notification(
            subject=f"[{APP_NAME}] DR Failover FAILED — MANUAL INTERVENTION REQUIRED",
            message=result,
        )
        raise

    return {"statusCode": 200, "body": json.dumps(result, default=str)}
