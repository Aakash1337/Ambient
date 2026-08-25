# Scenario Drills — Threat Detection & Monitoring

## When you inherit an org with zero detection, what do you enable first and in what order?
Aliases: No detection at all, where do I start? | Bootstrapping AWS threat detection from scratch | What do you turn on first in a greenfield org?
Tags: cloudtrail, guardduty, security hub, config, organizations
Org-wide CloudTrail first as the durable audit foundation, then layer GuardDuty, Security Hub, and Config.

• Org CloudTrail to log-archive account
• GuardDuty org-wide via delegated admin
• Security Hub to aggregate, then Config
• Trade-off: cost scales, enable centrally

## GuardDuty just flagged cryptomining on a prod instance — walk me through your response.
Aliases: Cryptomining finding on production, what now? | GuardDuty crypto-mining alert response | Compromised EC2 mining coins, how do you respond?
Tags: guardduty, incident response, ec2, forensics, cryptomining
Validate it's real, preserve evidence, isolate the instance, then hunt for how the credentials or host were compromised.

• Snapshot EBS first, preserve evidence
• Isolate SG; NACL deny (stateless)
• Rotate role creds, enforce IMDSv2
• Rebuild from clean AMI; post-mortem

## Security Hub is showing 4,000 findings — how do you cut the alert fatigue?
Aliases: 4,000 findings, reduce the noise | Security Hub alert fatigue, what do you do? | Too many findings, how do you prioritize?
Tags: security hub, alert fatigue, automation rules, prioritization, eventbridge
Prioritize by severity and standard, suppress documented accepted risks, and automate the noise away — the SOC triage I've run.

• Consolidated controls, dedupe cross-account
• Automation rules suppress accepted risks
• Route real findings to ticketing
• Trade-off: suppression can hide issues

## Design centralized logging for a 50-account organization.
Aliases: Centralized logging across 50 accounts | Org-wide log architecture and retention | How do you build a log-archive account?
Tags: cloudtrail, log archive, s3 object lock, kms, organizations
Org-trail delivering every account into an immutable, KMS-encrypted S3 bucket in a locked-down log-archive account.

• Org CloudTrail auto-covers new accounts
• S3 Object Lock WORM, deny delete
• Aggregate GuardDuty, Config, Security Hub centrally
• Lifecycle to Glacier; trade-off cost

## What data sources does GuardDuty analyze, and what does that let it catch?
Aliases: What does GuardDuty look at? | GuardDuty data sources and detections | What can GuardDuty actually detect?
Tags: guardduty, cloudtrail, vpc flow logs, dns logs, threat detection
GuardDuty reads CloudTrail events, VPC Flow Logs, and DNS query logs to catch recon, credential misuse, and exfiltration.

• Ingests these itself — no setup
• Optional: EKS, RDS, Lambda, malware scan
• Catches mining, C2, IAM abuse
• Trade-off: detection, not prevention

## You suspect account compromise — how do you reconstruct the attacker's actions?
Aliases: Rebuild the attacker timeline | Reconstruct what a compromised account did | Which tools trace attacker activity?
Tags: cloudtrail, detective, vpc flow logs, athena, incident response
Build an API timeline from CloudTrail, pivot through Detective's behavior graph, and corroborate movement with VPC Flow Logs.

• CloudTrail Lake or Athena queries
• Detective links entities and behavior
• Trade-off: data events must pre-exist
• Management events: 90-day history

## When would you reach for Security Lake?
Aliases: Why use Amazon Security Lake? | When does Security Lake make sense? | Security Lake versus a SIEM?
Tags: security lake, ocsf, athena, data lake, normalization
When you need multi-source security data normalized to OCSF in your own S3 for big-data analytics and cross-tool querying.

• Centralizes AWS, on-prem, SaaS sources
• Parquet in S3, query via Athena
• Feeds any SIEM/analytics subscriber
• Trade-off: lake, not detection engine

## How do you detect low-and-slow exfiltration that stays under rate thresholds?
Aliases: Catching slow data exfiltration | Exfil under the rate limits, how? | Detecting stealthy exfiltration
Tags: guardduty, dns exfiltration, macie, baselining, vpc flow logs
Baseline normal behavior and lean on GuardDuty's exfil and DNS findings, since fixed thresholds miss low-and-slow theft.

• GuardDuty DNS data-exfiltration findings
• Macie locates sensitive S3 data
• Detective for behavioral anomalies
• Trade-off: baselining needs warm-up

## Config caught an S3 bucket going public three days ago — how do you make sure it never persists again?
Aliases: S3 bucket went public, prevent recurrence | Stop public buckets for good | Auto-remediate public S3
Tags: s3 block public access, config, scp, auto-remediation, organizations
Layer prevention over detection — account-level Block Public Access plus an SCP so it can't happen, not just get caught.

• Account-level S3 Block Public Access
• Config rule with SSM auto-remediation
• SCP denies disabling BPA
• SCPs restrict, never grant

## What's the gap between GuardDuty and a full SIEM, and when do you actually need the SIEM?
Aliases: GuardDuty versus a SIEM | Do I need a SIEM on top of GuardDuty? | When is GuardDuty not enough?
Tags: guardduty, siem, correlation, retention, security lake
GuardDuty is AWS-only managed detection; you need a SIEM for cross-source correlation, custom rules, and long-term retention.

• GuardDuty findings retained ~90 days
• SIEM ingests endpoint, on-prem, SaaS
• Custom correlation rules, case management
• Trade-off: SIEM cost and tuning
