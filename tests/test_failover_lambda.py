import os
import json
import pytest
from unittest.mock import MagicMock, patch

# Set environment variables required by the lambda at import time
os.environ["DR_CLUSTER_ID"] = "dr-db-replica"
os.environ["ECS_CLUSTER"] = "dr-cluster"
os.environ["ECS_SERVICE"] = "dr-service"
os.environ["PROD_TASK_COUNT"] = "10"
os.environ["APP_NAME"] = "recovery-engine"
os.environ["DR_REGION"] = "us-east-1"
os.environ["NOTIFICATION_TOPIC"] = "arn:aws:sns:us-east-1:123456789012:topic"

# Mock boto3 before importing the lambda to prevent real AWS calls
with patch('boto3.client'):
    from scripts import failover_lambda

@pytest.fixture
def mock_aws():
    """Reset the mock clients before each test."""
    failover_lambda.rds = MagicMock()
    failover_lambda.ecs = MagicMock()
    failover_lambda.ssm = MagicMock()
    failover_lambda.sns = MagicMock()
    failover_lambda.aas = MagicMock()

def test_handler_success(mock_aws):
    """Test a successful full failover execution."""
    os.environ["DRY_RUN"] = "false"
    
    # 1st describe: check if writer (has replication source)
    # 2nd describe: get the new endpoint
    failover_lambda.rds.describe_db_instances.side_effect = [
        {"DBInstances": [{"ReadReplicaSourceDBInstanceIdentifier": "primary-db"}]},
        {"DBInstances": [{"Endpoint": {"Address": "new-db.amazonaws.com"}}]}
    ]
    
    failover_lambda.ecs.describe_services.return_value = {
        "services": [{"runningCount": 10}]
    }

    event = {"Records": [{"Sns": {"Message": "Test Alarm"}}]}
    
    result = failover_lambda.handler(event, None)
    
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    
    assert body["status"] == "success"
    assert body["steps"]["rds_promotion"]["endpoint"] == "new-db.amazonaws.com"
    assert body["steps"]["ecs_scale"]["running_tasks"] == 10

    # Verify RDS interactions
    failover_lambda.rds.promote_read_replica.assert_called_once_with(
        DBInstanceIdentifier="dr-db-replica"
    )
    failover_lambda.rds.get_waiter.assert_called_once_with('db_instance_available')
    
    # Verify SSM interactions (4 parameters)
    assert failover_lambda.ssm.put_parameter.call_count == 4
    
    # Verify ECS interactions
    failover_lambda.ecs.update_service.assert_called_once_with(
        cluster="dr-cluster",
        service="dr-service",
        desiredCount=10
    )
    failover_lambda.ecs.get_waiter.assert_called_once_with('services_stable')


def test_handler_already_writer(mock_aws):
    """Test idempotency when DB is already promoted."""
    os.environ["DRY_RUN"] = "false"
    
    # describe returns no replication source (already writer)
    failover_lambda.rds.describe_db_instances.return_value = {
        "DBInstances": [
            {"Endpoint": {"Address": "existing-db.amazonaws.com"}}
        ]
    }
    failover_lambda.ecs.describe_services.return_value = {
        "services": [{"runningCount": 10}]
    }

    result = failover_lambda.handler({}, None)
    body = json.loads(result["body"])
    
    assert body["status"] == "success"
    
    # promote_read_replica should NOT be called
    failover_lambda.rds.promote_read_replica.assert_not_called()


def test_handler_dry_run(mock_aws):
    """Test that dry run does not modify resources."""
    os.environ["DRY_RUN"] = "true"
    
    failover_lambda.rds.describe_db_instances.return_value = {
        "DBInstances": [{"ReadReplicaSourceDBInstanceIdentifier": "primary-db"}]
    }

    result = failover_lambda.handler({}, None)
    body = json.loads(result["body"])
    
    assert body["status"] == "success"
    
    # Modifying calls should NOT be made
    failover_lambda.rds.promote_read_replica.assert_not_called()
    failover_lambda.ssm.put_parameter.assert_not_called()
    failover_lambda.ecs.update_service.assert_not_called()
