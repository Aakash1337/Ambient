# Threat Detection & Monitoring

## What's the difference between CloudTrail management events and data events, and why does it matter?
Aliases: CloudTrail management vs data events | control plane vs data plane logging | what does CloudTrail capture
Tags: cloudtrail, management-events, data-events, logging, audit
CloudTrail management events log control-plane API calls, while data events log data-plane object-level operations like S3 GetObject or Lambda invokes.

• Management events: on by default
• Data events: paid, high-volume
• Insights events: anomalous API spikes
• Log file validation: SHA-256 digests

## How would you design CloudTrail coverage across a large multi-account AWS organization?
Aliases: organization trail | org-wide CloudTrail | multi-account audit logging
Tags: cloudtrail, organizations, organization-trail, delegated-admin, s3
I'd create an organization trail from the management or delegated-admin account so every current and future member account is logged centrally.

• Central S3 in log-archive account
• Members can't disable it
• CloudTrail Lake: SQL over events
• SCP guardrail against tampering

## What is Amazon GuardDuty, and what data sources does it analyze?
Aliases: what is GuardDuty | GuardDuty data sources | GuardDuty foundational sources
Tags: guardduty, threat-detection, vpc-flow-logs, dns-logs, cloudtrail
GuardDuty is a managed threat-detection service that continuously analyzes CloudTrail, VPC Flow Logs, and DNS logs without you enabling those logs yourself.

• Foundational sources: no agent needed
• ML plus threat-intel feeds
• Findings mapped to ATT&CK tactics
• Delegated admin, org auto-enable

## Explain GuardDuty's protection plans — how do you extend it to EKS, malware, and RDS?
Aliases: GuardDuty protection plans | GuardDuty malware protection | GuardDuty EKS RDS Lambda
Tags: guardduty, eks, malware-protection, rds-protection, runtime-monitoring
Beyond the foundational sources, GuardDuty adds optional protection plans for S3, EKS, malware, RDS logins, and Lambda network activity.

• EKS Protection: audit-log analysis
• Runtime Monitoring: eBPF agent
• Malware Protection: EBS volume scanning
• S3 Malware Protection: object scanning

## What is Security Hub, and how does it fit into cloud security posture management?
Aliases: what is Security Hub | Security Hub CSPM | posture management standards
Tags: security-hub, cspm, asff, fsbp, cis
Security Hub is AWS's cloud posture and finding-aggregation service that runs automated standards checks and normalizes findings into the ASFF format.

• Standards: FSBP, CIS, PCI, NIST
• Aggregates GuardDuty, Inspector, Macie
• Checks backed by AWS Config
• Security score per account

## How would you aggregate security findings across many accounts and regions?
Aliases: cross-region aggregation | Security Hub aggregation region | central findings view
Tags: security-hub, aggregation, multi-account, multi-region, delegated-admin
I'd designate a delegated administrator and a single aggregation Region so all member-account and cross-Region findings land in one pane.

• Delegated admin in Organizations
• Aggregation Region plus linked Regions
• EventBridge to ticketing/SOAR
• Automation rules for suppression

## What is AWS Config, and how do its rules evaluate compliance?
Aliases: what is AWS Config | Config rules | configuration compliance
Tags: aws-config, config-rules, remediation, ssm-automation, compliance
AWS Config records resource configuration changes over time and evaluates them against managed or custom rules to flag non-compliant resources.

• Config items: point-in-time snapshots
• Custom rules: Lambda or Guard
• Auto-remediation via SSM Automation
• Aggregators for multi-account view

## When would you use Config conformance packs?
Aliases: what are conformance packs | Config conformance packs | packaged compliance controls
Tags: aws-config, conformance-packs, compliance, organizations, remediation
I'd use conformance packs to deploy a curated set of Config rules and remediations as a single unit across the organization.

• Sample packs: PCI, HIPAA, NIST
• Single deployable YAML template
• Org-wide from delegated admin
• Pack-level compliance scoring

## What is Amazon Detective, and when would you reach for it during an investigation?
Aliases: what is Detective | Detective vs GuardDuty | investigation behavior graph
Tags: detective, investigation, behavior-graph, guardduty, root-cause
Detective builds a behavior graph from CloudTrail, VPC Flow Logs, and GuardDuty findings to help you investigate root cause fast.

• Investigates, doesn't generate findings
• Pivots from GuardDuty finding
• ML baselines normal behavior
• Activity timelines, entity profiles

## What is Security Lake, and why does OCSF matter?
Aliases: what is Security Lake | why OCSF | normalized security data lake
Tags: security-lake, ocsf, parquet, athena, s3
Security Lake centralizes security logs into your own S3, normalized to the OCSF open schema and Parquet so any tool can query them.

• OCSF: vendor-neutral schema
• Sources: CloudTrail, VPC, Route53, Security Hub
• Subscribers: Athena, OpenSearch, SIEM
• Fixes SIEM parser sprawl

## How would you use CloudWatch for security monitoring and alerting?
Aliases: CloudWatch for security | metric filters and alarms | detect root login
Tags: cloudwatch, metric-filters, alarms, logs-insights, eventbridge
From SOC work, I'd turn CloudTrail logs into CloudWatch metric filters and alarms for root logins or unauthorized API calls.

• Metric filters: root-login alarm
• Logs Insights: ad-hoc queries
• EventBridge: event-driven response
• Cross-account observability across org

## How would you make your security logs tamper-proof and immutable?
Aliases: immutable logging | WORM log storage | tamper-proof audit logs
Tags: s3-object-lock, worm, kms, log-integrity, cloudtrail
I'd store logs in S3 with Object Lock in compliance mode for WORM immutability, plus versioning and SSE-KMS encryption.

• Object Lock compliance: no deletion
• CloudTrail log file validation
• Bucket policy denies Delete
• Dedicated KMS key, restricted grants

## Explain the dedicated log-archive account pattern and why it matters.
Aliases: log-archive account | central logging account | Control Tower log account
Tags: log-archive, control-tower, separation-of-duties, cross-account, scp
A dedicated log-archive account owns the central logging buckets, and every other account ships logs cross-account with tightly restricted read access.

• Separation of duties by design
• Control Tower provisions it
• Compromised account can't erase logs
• SCPs block CloudTrail/Config disable

## Design an end-to-end threat detection and monitoring stack for a multi-account org.
Aliases: design detection architecture | end-to-end monitoring stack | wire the services together
Tags: guardduty, security-hub, config, security-lake, detective
I'd enable GuardDuty, Config, and Security Hub org-wide via delegated admin, centralize logs to a log-archive account, and aggregate into Security Lake.

• Detection: GuardDuty plus Config rules
• Aggregation: Security Hub single pane
• Investigation: Detective behavior graph
• Response: EventBridge to SOAR/Lambda
