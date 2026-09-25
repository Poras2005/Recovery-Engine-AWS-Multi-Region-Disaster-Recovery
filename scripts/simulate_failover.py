#!/usr/bin/env python3
"""
simulate_failover.py
Game-day DR failover simulation script.
Injects failures into the primary region and verifies automated failover.
"""

import argparse
import boto3
import socket
import urllib.request
import time
import os
import sys
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────
APP_NAME = os.environ.get("APP_NAME", "myapp")
PRIMARY_REGION = os.environ.get("PRIMARY_REGION", "ap-south-1")
DR_REGION = os.environ.get("DR_REGION", "ap-southeast-1")
DOMAIN = os.environ.get("DOMAIN", "api.example.com")
HEALTH_CHECK_PATH = os.environ.get("HEALTH_CHECK_PATH", "/health")
PRIMARY_ALB_LISTENER_ARN = os.environ.get("PRIMARY_ALB_LISTENER_ARN", "")

STATE_FILE = f"{APP_NAME}_dr_test_rule_arn.txt"

class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    BOLD = '\033[1m'
    NC = '\033[0m'

def log(msg): print(f"{Colors.BLUE}[{datetime.now().strftime('%H:%M:%S')}]{Colors.NC} {msg}")
def success(msg): print(f"{Colors.GREEN}[✓]{Colors.NC} {msg}")
def warn(msg): print(f"{Colors.YELLOW}[!]{Colors.NC} {msg}")
def error(msg): print(f"{Colors.RED}[✗]{Colors.NC} {msg}")
def section(msg): print(f"\n{Colors.BOLD}{Colors.BLUE}══ {msg} ══{Colors.NC}\n")

try:
    session = boto3.Session()
    r53 = session.client('route53')
    ecs_pri = session.client('ecs', region_name=PRIMARY_REGION)
    ecs_dr = session.client('ecs', region_name=DR_REGION)
    cw_dr = session.client('cloudwatch', region_name=DR_REGION)
    elbv2 = session.client('elbv2', region_name=PRIMARY_REGION)
    sts = session.client('sts', region_name=PRIMARY_REGION)
except Exception as e:
    error(f"Failed to initialize boto3 clients. Is AWS CLI configured? {e}")
    sys.exit(1)


def check_prereqs():
    section("Checking Prerequisites")
    try:
        sts.get_caller_identity()
        success("AWS credentials valid")
    except Exception as e:
        error(f"AWS credentials not configured: {e}")
        sys.exit(1)


def capture_baseline():
    section("Capturing Baseline Metrics")
    
    log("Primary region DNS resolution:")
    try:
        ip = socket.gethostbyname(DOMAIN)
        success(f"DNS resolved to {ip}")
    except socket.gaierror:
        warn("DNS lookup failed")

    log("Primary health check:")
    try:
        req = urllib.request.Request(f"https://{DOMAIN}{HEALTH_CHECK_PATH}", method="GET")
        with urllib.request.urlopen(req, timeout=5) as res:
            if res.status == 200:
                success("Primary health check OK")
    except Exception as e:
        warn(f"Primary health check failed (expected if already failed over): {e}")

    log("Current Route 53 health check status:")
    try:
        checks = r53.list_health_checks().get('HealthChecks', [])
        hc_id = None
        for hc in checks:
            if DOMAIN in hc.get('HealthCheckConfig', {}).get('FullyQualifiedDomainName', ''):
                hc_id = hc['Id']
                break
        if hc_id:
            status = r53.get_health_check_status(HealthCheckId=hc_id)
            obs = status.get('HealthCheckObservations', [])
            for o in obs:
                print(f"  Region: {o.get('Region')}, Status: {o.get('StatusReport', {}).get('Status')}")
    except Exception as e:
        warn(f"Could not fetch health check status: {e}")

    log("ECS service status (primary):")
    try:
        resp = ecs_pri.describe_services(cluster=f"{APP_NAME}-primary", services=[f"{APP_NAME}-primary-service"])
        if resp['services']:
            svc = resp['services'][0]
            print(f"  Running: {svc['runningCount']}, Desired: {svc['desiredCount']}, Status: {svc['status']}")
    except Exception as e:
        warn(f"Could not fetch ECS status: {e}")

    log("RDS replication lag (DR instance):")
    try:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5)
        resp = cw_dr.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName="ReplicaLag",
            Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': f"{APP_NAME}-dr-db"}],
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=["Maximum"]
        )
        if resp['Datapoints']:
            latest = sorted(resp['Datapoints'], key=lambda x: x['Timestamp'])[-1]
            print(f"  Replication lag: {latest['Maximum']}s")
    except Exception as e:
        warn(f"Could not fetch replication lag: {e}")


def inject_failure(dry_run):
    section("Injecting Failure — Primary ALB Returns 503")
    warn("This will cause Route 53 health checks to fail and trigger automated failover.")
    warn("Press Ctrl+C within 10 seconds to abort.")
    try:
        time.sleep(10)
    except KeyboardInterrupt:
        error("Aborted.")
        sys.exit(1)

    listener_arn = PRIMARY_ALB_LISTENER_ARN
    if not listener_arn:
        try:
            # Try to auto-discover
            albs = elbv2.describe_load_balancers(Names=[f"{APP_NAME}-primary-alb"])
            alb_arn = albs['LoadBalancers'][0]['LoadBalancerArn']
            listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)
            for l in listeners['Listeners']:
                if l['Port'] == 443:
                    listener_arn = l['ListenerArn']
                    break
        except Exception:
            pass

    if not listener_arn:
        error("Could not discover ALB Listener ARN. Set PRIMARY_ALB_LISTENER_ARN env var.")
        sys.exit(1)

    try:
        rules = elbv2.describe_rules(ListenerArn=listener_arn)
        default_rule = next(r for r in rules['Rules'] if r['IsDefault'])
        rule_arn = default_rule['RuleArn']
    except Exception as e:
        error(f"Failed to get ALB rule: {e}")
        sys.exit(1)

    log("Modifying default listener rule to return 503...")
    if dry_run:
        warn(f"[DRY RUN] Would modify rule {rule_arn} to return 503")
    else:
        try:
            elbv2.modify_rule(
                RuleArn=rule_arn,
                Actions=[{
                    'Type': 'fixed-response',
                    'FixedResponseConfig': {
                        'MessageBody': 'FAILOVER_TEST',
                        'StatusCode': '503',
                        'ContentType': 'text/plain'
                    }
                }]
            )
            success("Primary ALB now returning 503 — failover should trigger automatically")
            with open(STATE_FILE, 'w') as f:
                f.write(rule_arn)
        except Exception as e:
            error(f"Failed to modify rule: {e}")
            sys.exit(1)


def monitor_failover():
    section("Monitoring Failover Progress")
    log("Polling DNS and DR ECS service every 15 seconds for 20 minutes...")
    
    max_wait = 1200
    start_time = time.time()
    
    while (time.time() - start_time) < max_wait:
        elapsed = int(time.time() - start_time)
        
        # Check DNS
        try:
            resolved_ip = socket.gethostbyname(DOMAIN)
        except:
            resolved_ip = "FAILED"
            
        # Check ECS
        try:
            resp = ecs_dr.describe_services(cluster=f"{APP_NAME}-dr", services=[f"{APP_NAME}-dr-service"])
            ecs_running = resp['services'][0]['runningCount'] if resp['services'] else 0
        except:
            ecs_running = 0
            
        # Check Health
        try:
            req = urllib.request.Request(f"https://{DOMAIN}{HEALTH_CHECK_PATH}", method="GET")
            with urllib.request.urlopen(req, timeout=5) as res:
                health_resp = "HEALTHY" if res.status == 200 else "UNHEALTHY"
        except:
            health_resp = "UNHEALTHY"
            
        print(f"[{elapsed:4}s] DNS: {resolved_ip:<16} | DR ECS running: {ecs_running:<3} | Health: {health_resp}")
        
        if health_resp == "HEALTHY" and ecs_running >= 2:
            print("\n")
            success("Failover complete! Application healthy in DR region.")
            success(f"Total failover time: {elapsed}s")
            return
            
        time.sleep(15)
        
    error("Failover did not complete within 20 minutes — manual investigation required")
    sys.exit(1)


def restore_primary(dry_run):
    section("Restoring Primary Region")
    warn("This restores the ALB listener rule to forward traffic normally.")
    
    if not os.path.exists(STATE_FILE):
        error(f"Rule ARN file not found at {STATE_FILE} — cannot auto-restore")
        print("Manually update the ALB default listener rule to forward to your target group.")
        sys.exit(1)
        
    with open(STATE_FILE, 'r') as f:
        rule_arn = f.read().strip()
        
    try:
        tgs = elbv2.describe_target_groups(Names=[f"{APP_NAME}-primary-tg"])
        tg_arn = tgs['TargetGroups'][0]['TargetGroupArn']
    except Exception as e:
        error(f"Could not find primary Target Group: {e}")
        sys.exit(1)
        
    log("Restoring default listener rule to forward to target group...")
    if dry_run:
        warn(f"[DRY RUN] Would modify rule {rule_arn} to forward to {tg_arn}")
    else:
        try:
            elbv2.modify_rule(
                RuleArn=rule_arn,
                Actions=[{
                    'Type': 'forward',
                    'TargetGroupArn': tg_arn
                }]
            )
            success("Primary ALB restored to normal forwarding")
            warn("NOTE: You must also manually recreate the DR RDS Read Replica from the primary")
            warn("and update Route 53 records once you are satisfied with the post-failover state.")
            os.remove(STATE_FILE)
        except Exception as e:
            error(f"Failed to restore rule: {e}")
            sys.exit(1)


def write_report(mode):
    section("Game Day Report")
    report_file = f"{APP_NAME}_dr_game_day_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w') as f:
        f.write(f"DR Game Day Report — {APP_NAME}\n")
        f.write(f"Date: {datetime.now()}\n")
        f.write(f"Mode: {mode}\n")
        f.write(f"Primary Region: {PRIMARY_REGION}\n")
        f.write(f"DR Region: {DR_REGION}\n\n")
        f.write("Results captured in console. Attach this file to your DR runbook update.\n")
    log(f"Report written to {report_file}")


def main():
    parser = argparse.ArgumentParser(description="AWS Multi-Region DR Failover Simulation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Describe what would happen, no changes")
    group.add_argument("--execute", action="store_true", help="Trigger real failover test")
    group.add_argument("--restore", action="store_true", help="Restore primary after test")
    
    args = parser.parse_args()
    
    print(f"\n{Colors.BOLD}{Colors.BLUE}AWS Multi-Region DR Failover Simulation{Colors.NC}")
    print(f"{Colors.BLUE}App: {APP_NAME} | Primary: {PRIMARY_REGION} | DR: {DR_REGION}{Colors.NC}\n")

    check_prereqs()
    capture_baseline()
    
    if args.dry_run:
        section("DRY RUN — No changes will be made")
        inject_failure(dry_run=True)
        success("Dry run complete. Review the output and run with --execute when ready.")
    elif args.execute:
        inject_failure(dry_run=False)
        monitor_failover()
        write_report("execute")
    elif args.restore:
        restore_primary(dry_run=False)

if __name__ == "__main__":
    main()
