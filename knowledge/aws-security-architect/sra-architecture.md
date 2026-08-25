# AWS Security Reference Architecture

## What is the AWS Security Reference Architecture?
Aliases: What is the SRA? | Explain AWS SRA | What's the security reference architecture?
Tags: sra, overview, multi-account, guidance
A holistic AWS guide showing how to deploy security services across a multi-account AWS Organization.

• Prescriptive multi-account security blueprint
• Built on AWS Organizations
• Free guidance, not a service
• Maps services to accounts

## What are the SRA's core design principles?
Aliases: SRA design principles | What principles guide the SRA? | Key SRA tenets
Tags: principles, defense-in-depth, least-privilege, delegation
Align security to org structure, apply defense in depth, centralize telemetry, and delegate admin away from management.

• Defense in depth
• Least privilege everywhere
• Centralized logging and monitoring
• Delegated, not root, admin

## What multi-account and OU structure does the SRA recommend?
Aliases: SRA account structure | What OUs does the SRA use? | SRA org layout
Tags: ou-structure, accounts, organization, isolation
Separate OUs for Security, Infrastructure, and Workloads sit under the org root, isolating duties by blast radius.

• Security OU: Tooling, Log Archive
• Infrastructure OU: Network, Shared Services
• Workloads OU: application accounts
• Isolation limits blast radius

## What is the management account's role and why keep it minimal?
Aliases: Management account role | Why is the management account minimal? | Org management account purpose
Tags: management-account, control-tower, organizations, blast-radius
Runs Organizations, Control Tower, Identity Center, and the org trail, but holds no workloads to shrink blast radius.

• Org-wide CloudTrail and Access Analyzer
• Delegates security services out
• No apps, minimal access
• Most privileged, so guarded

## What is the Security Tooling account for?
Aliases: Security Tooling account | What does the audit account do? | Delegated admin account
Tags: security-tooling, delegated-admin, guardduty, security-hub
The delegated administrator that centrally operates GuardDuty, Security Hub, Macie, Inspector, Detective, and Config aggregation.

• Delegated admin, not management
• Aggregates findings org-wide
• EventBridge and Lambda response
• Hosts KMS, Private CA

## What is the Log Archive account and immutable logging?
Aliases: Log Archive account | Central logging account | What is immutable logging?
Tags: log-archive, immutable, cloudtrail, object-lock
Immutable central store for the org CloudTrail bucket, Config, and flow logs, with tightly restricted access.

• Central CloudTrail, Config, VPC logs
• Object Lock, no deletion
• Security Lake subscribers
• Read access tightly restricted

## What is the Network account and central inspection VPC?
Aliases: Network account role | Inspection VPC | What handles centralized networking?
Tags: network-account, inspection-vpc, transit-gateway, network-firewall
Owns shared networking — inbound, outbound, and inspection VPCs, Network Firewall, Transit Gateway, plus edge WAF and Shield Advanced.

• Central inspection VPC
• Transit Gateway routes traffic
• Network Firewall, DNS Firewall
• Shares subnets via RAM

## What is the Shared Services account?
Aliases: Shared Services account | What does shared services host? | Common infrastructure account
Tags: shared-services, identity-center, directory, systems-manager
Hosts common infrastructure — delegated Identity Center, Managed Microsoft AD, and Systems Manager — reused across workload accounts.

• Delegated IAM Identity Center
• Managed Microsoft AD directory
• Systems Manager automation
• Shared across all workloads

## How do per-account security services roll up via delegated administration?
Aliases: How do findings aggregate? | Delegated administration rollup | How does the SRA centralize services?
Tags: delegated-admin, rollup, auto-enable, scp
Every account runs GuardDuty, Config, Security Hub, and Access Analyzer, reporting up to the Security Tooling delegated admin.

• Delegated admin, not management
• Auto-enabled on new accounts
• Findings aggregate centrally
• SCPs restrict, never grant

## What is the SRA's phased approach to building security?
Aliases: SRA phased approach | How do you roll out the SRA? | SRA implementation phases
Tags: phased, roadmap, incremental, adoption
Build incrementally — start with org foundations and logging, then centralize detection, then network and workload controls.

• Foundations: Organizations, accounts, logging
• Then detection and monitoring
• Then network and edge
• Iterate, don't big-bang
