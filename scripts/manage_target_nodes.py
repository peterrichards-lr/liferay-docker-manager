#!/usr/bin/env python3
"""Standalone Target Node Power & Cost Control Utility.

Manages power states (wake/sleep/enforce/status) for remote compute target nodes
(e.g., aws-1, aws-2) for cost control.

Last Updated: 2026-08-20 | Last Reviewed: 2026-08-20
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / ".node-power-config.json"
STATE_FILE = Path(__file__).parent.parent / ".node-power-state.json"
LDMRC_FILE = Path.home() / ".ldmrc"
CONFIG_URL = "https://raw.githubusercontent.com/peterrichards-lr/liferay-docker-manager/master/.node-power-config.json"


def load_target_nodes() -> dict:
    """Loads target node definitions from .node-power-config.json or ~/.ldmrc fallback."""
    nodes = {
        "aws-1": {
            "name": "aws-1",
            "schedule": "auto",
            "ec2_instance_id": "",
            "region": "",
            "host": "",
            "user": "ubuntu",
        },
        "aws-2": {
            "name": "aws-2",
            "schedule": "auto",
            "ec2_instance_id": "",
            "region": "",
            "host": "",
            "user": "ubuntu",
        },
    }

    # Auto-sync central config if missing locally
    if not CONFIG_FILE.exists():
        try:
            import urllib.request

            urllib.request.urlretrieve(CONFIG_URL, CONFIG_FILE)
        except Exception:
            pass

    # Override from ~/.ldmrc if present
    if LDMRC_FILE.exists():
        try:
            data = json.loads(LDMRC_FILE.read_text())
            for name, node_info in data.get("targets", {}).items():
                if name not in nodes:
                    nodes[name] = {
                        "name": name,
                        "schedule": "auto",
                        "ec2_instance_id": node_info.get("ec2_instance_id", ""),
                        "region": node_info.get("region", ""),
                        "host": node_info.get("host", ""),
                        "user": node_info.get("user", "ubuntu"),
                    }
                else:
                    nodes[name]["host"] = node_info.get("host", nodes[name]["host"])
                    nodes[name]["user"] = node_info.get("user", nodes[name]["user"])
                    if node_info.get("ec2_instance_id"):
                        nodes[name]["ec2_instance_id"] = node_info["ec2_instance_id"]
        except Exception:
            pass

    # Override from local config file if present
    if CONFIG_FILE.exists():
        try:
            custom_data = json.loads(CONFIG_FILE.read_text())
            for name, cfg in custom_data.get("nodes", {}).items():
                if name in nodes:
                    nodes[name].update(cfg)
                else:
                    nodes[name] = cfg
        except Exception:
            pass

    return nodes


def load_state() -> dict:
    """Loads transient wake state from .node-power-state.json."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    """Saves transient wake state to .node-power-state.json."""
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def parse_duration(ttl_str: str) -> timedelta:
    """Parses duration string like '2h', '30m', '1d', '4h' into timedelta."""
    match = re.match(r"^(\d+)([smhd])$", ttl_str.strip().lower())
    if not match:
        return timedelta(hours=2)
    val, unit = int(match.group(1)), match.group(2)
    if unit == "s":
        return timedelta(seconds=val)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    if unit == "d":
        return timedelta(days=val)
    return timedelta(hours=2)


def is_in_shutdown_window(dt: datetime, schedule: str) -> bool:
    """Determines whether the given datetime falls inside the scheduled shutdown window."""
    if schedule == "off":
        return False

    weekday = dt.weekday()  # Mon=0, Tue=1, ..., Fri=4, Sat=5, Sun=6
    hour = dt.hour

    is_overnight = hour >= 19 or hour < 7
    is_weekend = (
        (weekday == 4 and hour >= 19)
        or (weekday in (5, 6))
        or (weekday == 0 and hour < 7)
    )

    if schedule == "overnight":
        return is_overnight
    if schedule == "weekend":
        return is_weekend
    if schedule in ("auto", "default"):
        return is_overnight or is_weekend

    return False


def get_ec2_public_ip(ec2_id: str, region: str = "") -> str:
    """Queries AWS EC2 for the current public IP address of an instance."""
    cmd = [
        "aws",
        "ec2",
        "describe-instances",
        "--instance-ids",
        ec2_id,
        "--query",
        "Reservations[0].Instances[0].PublicIpAddress",
        "--output",
        "text",
    ]
    if region:
        cmd.extend(["--region", region])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and res.stdout.strip() and res.stdout.strip() != "None":
        return res.stdout.strip()
    return ""


def update_node_host_ip(node_name: str, new_ip: str) -> None:
    """Updates .node-power-config.json and ~/.ldmrc host entry when public IP changes."""
    if not new_ip:
        return

    if CONFIG_FILE.exists():
        try:
            custom_data = json.loads(CONFIG_FILE.read_text())
            nodes = custom_data.get("nodes", {})
            if node_name in nodes:
                nodes[node_name]["host"] = new_ip
                CONFIG_FILE.write_text(json.dumps(custom_data, indent=2) + "\n")
        except Exception:
            pass

    if LDMRC_FILE.exists():
        try:
            ldmrc_data = json.loads(LDMRC_FILE.read_text())
            targets = ldmrc_data.get("targets", {})
            if node_name in targets:
                targets[node_name]["host"] = new_ip
                LDMRC_FILE.write_text(json.dumps(ldmrc_data, indent=4) + "\n")
        except Exception:
            pass


def power_on_node(node_name: str, config: dict) -> bool:
    """Boots or resumes the specified target node using AWS CLI or SSH."""
    ec2_id = config.get("ec2_instance_id")
    if ec2_id:
        cmd = ["aws", "ec2", "start-instances", "--instance-ids", ec2_id]
        if config.get("region"):
            cmd.extend(["--region", config["region"]])
        print(f"▶ Booting AWS EC2 instance '{ec2_id}' for target node '{node_name}'...")
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            print(f"✅ Target node '{node_name}' successfully powered on.")
            new_ip = get_ec2_public_ip(ec2_id, config.get("region", ""))
            if new_ip:
                print(f"🌐 Resolved updated public IP for '{node_name}': {new_ip}")
                update_node_host_ip(node_name, new_ip)
            return True
        print(f"⚠️ AWS CLI error for '{node_name}': {res.stderr.strip()}")
        return False
    print(
        f"❌ Node '{node_name}' has no EC2 instance ID configured. Set ec2_instance_id in .node-power-config.json."
    )
    return False


def power_off_node(node_name: str, config: dict) -> bool:
    """Shuts down or stops the specified target node using AWS CLI or SSH."""
    ec2_id = config.get("ec2_instance_id")
    if ec2_id:
        cmd = ["aws", "ec2", "stop-instances", "--instance-ids", ec2_id]
        if config.get("region"):
            cmd.extend(["--region", config["region"]])
        print(
            f"▶ Stopping AWS EC2 instance '{ec2_id}' for target node '{node_name}'..."
        )
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            print(f"✅ Target node '{node_name}' successfully powered off.")
            return True
        print(f"⚠️ AWS CLI error for '{node_name}': {res.stderr.strip()}")
        return False

    host = config.get("host")
    user = config.get("user", "ubuntu")
    if host and host != "localhost":
        cmd = ["ssh", f"{user}@{host}", "sudo shutdown -h now"]
        print(f"▶ Sending SSH shutdown command to '{user}@{host}'...")
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            print(f"✅ Target node '{node_name}' SSH shutdown command sent.")
            return True

    print(
        f"⚠️ Unable to shut down node '{node_name}': No EC2 instance ID or SSH host configured."
    )
    return False


def cmd_wake(args: argparse.Namespace) -> None:
    """Handler for 'wake <node> [--ttl 2h]'."""
    nodes = load_target_nodes()
    node_name = args.node
    if node_name not in nodes:
        print(
            f"❌ Target node '{node_name}' not found. Available nodes: {', '.join(nodes.keys())}"
        )
        sys.exit(1)

    config = nodes[node_name]
    duration = parse_duration(args.ttl)
    now = datetime.now(timezone.utc)
    wake_until_dt = now + duration
    wake_until_str = wake_until_dt.isoformat()

    ok = power_on_node(node_name, config)
    if not ok:
        print(f"❌ Failed to power on target node '{node_name}'. Exiting with error.")
        sys.exit(1)

    state = load_state()
    state[node_name] = {
        "status": "woken",
        "wake_until": wake_until_str,
        "woken_at": now.isoformat(),
    }
    save_state(state)

    print(
        f"⏰ Target node '{node_name}' woken until {wake_until_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} (TTL: {args.ttl})."
    )


def cmd_sleep(args: argparse.Namespace) -> None:
    """Handler for 'sleep <node>'."""
    nodes = load_target_nodes()
    node_name = args.node
    if node_name not in nodes:
        print(
            f"❌ Target node '{node_name}' not found. Available nodes: {', '.join(nodes.keys())}"
        )
        sys.exit(1)

    config = nodes[node_name]

    ok = power_off_node(node_name, config)
    if not ok:
        print(f"❌ Failed to power off target node '{node_name}'. Exiting with error.")
        sys.exit(1)

    state = load_state()
    state[node_name] = {
        "status": "shutdown",
        "wake_until": "",
        "shutdown_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)


def cmd_enforce(args: argparse.Namespace) -> None:
    """Handler for 'enforce' (evaluates schedules and active wake TTLs)."""
    nodes = load_target_nodes()
    state = load_state()
    now = datetime.now(timezone.utc)
    now_local = datetime.now()

    print(
        f"🔍 Evaluating node power enforcement at {now_local.strftime('%Y-%m-%d %H:%M:%S')}..."
    )

    for name, config in nodes.items():
        schedule = config.get("schedule", "auto")
        node_state = state.get(name, {})
        wake_until_str = node_state.get("wake_until", "")

        is_woken = False
        if wake_until_str:
            try:
                wake_until_dt = datetime.fromisoformat(wake_until_str)
                if wake_until_dt > now:
                    is_woken = True
            except Exception:
                pass

        if is_woken:
            print(f"  • Node '{name}': WOKEN (TTL active until {wake_until_str})")
            continue

        in_window = is_in_shutdown_window(now_local, schedule)
        if in_window:
            print(
                f"  • Node '{name}': Shutdown window active (schedule: {schedule}). Enforcing shutdown."
            )
            power_off_node(name, config)
            state[name] = {
                "status": "shutdown",
                "wake_until": "",
                "shutdown_at": now.isoformat(),
            }
        else:
            print(
                f"  • Node '{name}': Business hours active (schedule: {schedule}). Ensuring node is powered ON."
            )
            ok = power_on_node(name, config)
            state[name] = {
                "status": "active" if ok else "error",
                "wake_until": "",
                "powered_on_at": now.isoformat(),
            }

    save_state(state)


def cmd_status(args: argparse.Namespace) -> None:
    """Handler for 'status'."""
    nodes = load_target_nodes()
    state = load_state()
    now = datetime.now(timezone.utc)
    now_local = datetime.now()

    print(
        "\n=========================================================================="
    )
    print("                TARGET COMPUTE NODE POWER CONTROL STATUS                  ")
    print("==========================================================================")
    print(
        f"Local Time: {now_local.strftime('%Y-%m-%d %H:%M:%S')} | Schedule Window: {'ACTIVE' if is_in_shutdown_window(now_local, 'auto') else 'INACTIVE'}\n"
    )
    print(
        f"{'NODE':<10} {'SCHEDULE':<10} {'EC2 ID':<18} {'STATUS':<15} {'DETAILS':<25}"
    )
    print("-" * 78)

    for name, config in nodes.items():
        schedule = config.get("schedule", "auto")
        ec2_id = config.get("ec2_instance_id") or ""
        region = config.get("region")
        region_flags = ["--region", region] if region else []
        node_state = state.get(name, {})
        wake_until_str = node_state.get("wake_until", "")

        status_label = "OFFLINE"
        details = "Local node"

        if ec2_id:
            check_cmd = [
                "aws",
                "ec2",
                "describe-instances",
                "--instance-ids",
                ec2_id,
                "--query",
                "Reservations[0].Instances[0].[State.Name,PublicIpAddress]",
                "--output",
                "text",
                *region_flags,
            ]
            check_res = subprocess.run(
                check_cmd, capture_output=True, text=True, check=False
            )
            if check_res.returncode == 0:
                parts = check_res.stdout.strip().split()
                aws_state = parts[0].upper() if parts else "UNKNOWN"
                public_ip = parts[1] if len(parts) > 1 else ""
                status_label = f"EC2:{aws_state}"
                if public_ip:
                    details = f"IP: {public_ip}"
                else:
                    details = "No Public IP"
            else:
                status_label = "AWS ERROR"
                details = check_res.stderr.strip()[:24]
        elif name == "local":
            status_label = "ACTIVE"
            details = "Local operation"

        if wake_until_str and "EC2:" in status_label:
            try:
                wake_until_dt = datetime.fromisoformat(wake_until_str)
                if wake_until_dt > now:
                    rem = wake_until_dt - now
                    mins = int(rem.total_seconds() // 60)
                    details += f" (TTL: {mins}m)"
            except Exception:
                pass

        disp_ec2_id = ec2_id if ec2_id else "N/A"
        print(
            f"{name:<10} {schedule:<10} {disp_ec2_id:<18} {status_label:<15} {details:<25}"
        )

    print(
        "==========================================================================\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone Target Node Power & Cost Control Utility"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status", help="Display target node power status and active wake TTLs"
    )

    wake_p = subparsers.add_parser(
        "wake", help="Temporarily wake a target node during shutdown hours"
    )
    wake_p.add_argument("node", help="Name of the target node (e.g. aws-1, aws-2)")
    wake_p.add_argument(
        "--ttl", default="2h", help="Wake duration (e.g. 2h, 30m, 4h). Default: 2h"
    )

    sleep_p = subparsers.add_parser("sleep", help="Immediately shut down a target node")
    sleep_p.add_argument("node", help="Name of the target node (e.g. aws-1, aws-2)")

    subparsers.add_parser(
        "enforce", help="Enforce scheduled overnight/weekend shutdowns and expire TTLs"
    )

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "wake":
        cmd_wake(args)
    elif args.command == "sleep":
        cmd_sleep(args)
    elif args.command == "enforce":
        cmd_enforce(args)


if __name__ == "__main__":
    main()
