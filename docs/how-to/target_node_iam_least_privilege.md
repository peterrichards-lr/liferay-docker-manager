# AWS IAM Least-Privilege Policy — Target Compute Node Power Control

This document defines the minimal AWS IAM policy required for automated target compute node power management (`scripts/manage_target_nodes.py`, GitHub Action `.github/workflows/node-power-manager.yml`).

---

## 1. Minimal IAM Policy Schema

Attach this custom policy to your CI/CD service account or IAM User (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEC2DescribeInstancesRead",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowEC2TargetNodePowerControl",
      "Effect": "Allow",
      "Action": [
        "ec2:StartInstances",
        "ec2:StopInstances"
      ],
      "Resource": [
        "arn:aws:ec2:eu-north-1:*:instance/i-049889a61ec29e7ce",
        "arn:aws:ec2:eu-north-1:*:instance/i-01194b1b4476dd3d7"
      ]
    }
  ]
}
```

---

## 2. Security Guardrails

1. **Strict Resource Scoping**:
   - `ec2:StartInstances` and `ec2:StopInstances` actions are scoped **exclusively** to specific EC2 instance IDs (`i-049889a61ec29e7ce` for `aws-1` and `i-01194b1b4476dd3d7` for `aws-2`).
   - The user/role cannot modify, terminate, or power off any other EC2 instances in your AWS account.

2. **Read-Only Inspection**:
   - `ec2:DescribeInstances` is granted to query live EC2 running state (`EC2:RUNNING`, `EC2:STOPPED`) and dynamically retrieve the assigned public IP.

3. **No Administrative Rights**:
   - No permissions are granted for `ec2:TerminateInstances`, `ec2:RunInstances`, IAM role management, or security group modification.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-20* | *Last Reviewed: 2026-08-20*
