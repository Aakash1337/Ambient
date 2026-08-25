# Incident Response

## Walk me through how you'd structure the incident response lifecycle in AWS.
Aliases: AWS IR lifecycle | phases of cloud incident response | how do you run an AWS incident
Tags: nist 800-61, guardduty, cloudtrail, ssm, security incident response
I follow the NIST 800-61 phases the AWS Security Incident Response Guide adapts: prepare, detect and analyze, contain, eradicate, recover, then post-incident.

• Prepare: logging, roles, runbooks
• Detect: GuardDuty, Security Hub, CloudTrail
• Contain, eradicate, recover programmatically
• Post-incident: COE, update detections

## How is incident response in the cloud different from on-prem IR?
Aliases: cloud vs on-prem IR | why is AWS IR different | shared responsibility in IR
Tags: shared responsibility, cloudtrail, api, automation, ephemeral
Cloud IR is API-driven and programmable, so I contain and collect evidence in code instead of touching physical hardware, and CloudTrail is ground truth.

• Shared responsibility model boundaries
• Ephemeral, elastic, re-deployable resources
• Programmatic containment at scale
• No physical access; API logs

## You get a GuardDuty finding that IAM credentials are being used from an unusual location — what do you do?
Aliases: credential compromise response | compromised IAM user | unauthorized API access
Tags: guardduty, iam, cloudtrail, sts, access keys
First scope it in CloudTrail, then contain the identity — deactivate keys or revoke sessions — before eradicating anything the attacker created.

• CloudTrail: scope actions, timeline
• Deactivate access keys; revoke sessions
• Hunt: new IAM users, keys
• Check GuardDuty related findings

## An access key was pushed to a public GitHub repo — walk me through your response.
Aliases: IAM key leaked on GitHub | exposed access key | secret committed to git
Tags: access keys, quarantine, cloudtrail, guardduty, secrets
AWS usually auto-attaches its quarantine policy and opens a case, but I still deactivate and delete the key immediately, then rotate and investigate.

• AWSCompromisedKeyQuarantineV3 managed policy
• Delete key; rotate secret
• CloudTrail: crypto-mining, new resources
• Prevent: OIDC roles, secrets scanning

## How would you isolate a compromised EC2 instance without destroying evidence?
Aliases: contain a compromised EC2 | quarantine EC2 instance | EC2 isolation steps
Tags: ec2, security groups, ebs snapshot, forensics, iam role
I capture volatile evidence first, then network-isolate with a quarantine security group, snapshot the EBS volumes, and detach the instance role.

• Memory before stop; RAM volatile
• Quarantine SG; deregister from ELB/ASG
• EBS snapshot; enable termination protection
• Revoke instance profile credentials

## Why isn't removing a security group's rules enough to cut off an attacker, and what do you add?
Aliases: SG vs NACL for isolation | security group statefulness | why isolation SG isn't instant
Tags: security groups, nacl, stateful, connection tracking, isolation
Security groups are stateful, so established connections keep flowing from tracked state; I add a deny-all NACL, which is stateless and cuts immediately.

• SG stateful; tracks existing flows
• NACL stateless; drops immediately
• Subnet-level deny for isolation
• Common interview gotcha

## How do you capture forensic evidence — memory and disk — from a running EC2 instance?
Aliases: EC2 memory acquisition | disk forensics AWS | capture RAM before stopping
Tags: ec2, ebs snapshot, memory, avml, forensics
Memory is lost on stop, so I capture RAM live with a tool like AVML or LiME, then snapshot the EBS volumes for disk.

• AVML/LiME via SSM Run Command
• Stopping loses RAM; order matters
• EBS snapshot; hash for integrity
• Copy to forensic account, read-only

## Design an automated remediation pipeline for security findings.
Aliases: EventBridge Lambda remediation | auto-remediate GuardDuty | SOAR on AWS
Tags: eventbridge, lambda, ssm automation, guardduty, step functions
GuardDuty and Security Hub findings hit an EventBridge rule that triggers a Lambda or SSM Automation runbook to contain, with Step Functions orchestrating multi-step responses.

• EventBridge rule on finding severity
• Lambda/SSM Automation for containment
• Step Functions for orchestration
• Human approval for destructive actions

## How do you revoke compromised temporary STS credentials versus long-lived access keys?
Aliases: revoke STS sessions | disable access keys | contain role credentials
Tags: sts, iam, access keys, session revocation, tokenissuetime
Long-lived keys I deactivate then delete; STS tokens can't be individually revoked, so I attach a deny policy conditioned on aws:TokenIssueTime.

• Access keys: deactivate, then delete
• STS: no per-token revocation
• Deny with DateLessThan aws:TokenIssueTime
• Console revoke sessions: inline policy

## Why would you set up a dedicated forensic account, and how is it configured?
Aliases: forensics account AWS | isolated analysis account | cross-account forensics
Tags: organizations, forensic account, kms, cross-account, security ou
A dedicated forensic account in a security OU isolates analysis from production, so evidence and tooling live where the attacker can't reach or tamper.

• Separate account, security OU
• Cross-account snapshot sharing, KMS
• No internet; locked-down IAM
• Pre-built forensic workstation AMI

## What's the difference between a playbook and a runbook, and how do you operationalize them in AWS?
Aliases: playbook vs runbook | IR runbooks in AWS | operationalize response procedures
Tags: playbooks, runbooks, ssm automation, jupyter, guardduty
A playbook is the strategic response for an incident type; a runbook is the concrete step-by-step, which I codify as SSM Automation documents.

• Playbook: strategy per incident type
• Runbook: exact executable steps
• SSM Automation, Lambda, notebooks
• Version-controlled, tested in game days

## What happens in your post-incident process after containment?
Aliases: post-incident analysis | lessons learned | correction of error
Tags: post-incident, blameless postmortem, coe, mttr, detection
I run a blameless correction-of-error review to find root cause, then feed lessons back into detections and runbooks — I've owned this for live-event incidents.

• Blameless COE; root cause
• Metrics: MTTD, MTTR
• Update detections, runbooks, IAM
• Stakeholder comms, evidence retention

## How do you prepare an AWS environment for incident response before anything happens?
Aliases: IR readiness AWS | prepare for incidents | IR preparation phase
Tags: preparation, cloudtrail, guardduty, break-glass, security incident response
Preparation is most of the work: organization-wide CloudTrail and GuardDuty, break-glass roles, tested runbooks, and pre-staged forensic tooling before any incident.

• Org CloudTrail, GuardDuty, Security Hub
• Break-glass IR roles, permissions
• AWS Security Incident Response service
• Game days; pre-staged forensic account
