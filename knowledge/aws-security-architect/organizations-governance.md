# AWS Organizations & Governance

## Explain AWS Organizations and why an enterprise runs many accounts instead of one.
Aliases: what is AWS Organizations | why multi-account on AWS | how does Organizations work
Tags: organizations, multi-account, ou, scp, consolidated-billing, governance
AWS Organizations centrally manages multiple accounts as one entity, giving you OUs, SCPs, consolidated billing, and org-wide policy from a management account.

• Management account, member accounts
• OUs group accounts by function
• SCPs, RCPs, tag policies
• Blast-radius isolation per account

## How would you design your OU structure?
Aliases: how do you organize OUs | OU design strategy | how would you lay out organizational units
Tags: ou, organizations, inheritance, security-ou, structure
I'd structure OUs by function and lifecycle — Security, Infrastructure, Workloads, Sandbox — so guardrails inherit down the tree, not by mirroring org charts.

• Security OU: log-archive, audit
• Group by blast radius
• Policies inherit, deny wins
• Avoid deep nesting

## What is the difference between an SCP and an IAM policy?
Aliases: SCP vs IAM policy | do SCPs grant permissions | how are SCPs different from identity policies
Tags: scp, iam, guardrail, permissions-boundary, deny
An SCP is a guardrail that sets the maximum permissions for a member account; it never grants access — IAM policies do that.

• SCP filters, never grants
• Effective = SCP ∩ IAM
• Doesn't affect management account
• Excludes service-linked roles

## What are RCPs and how do they differ from SCPs?
Aliases: what is a resource control policy | RCP vs SCP | how do RCPs work
Tags: rcp, scp, resource-control, cross-account, s3, kms
RCPs, launched in 2024, are resource-side guardrails — they cap who can access resources org-wide, including external principals, and like SCPs never grant.

• SCP = identities, RCP = resources
• Supports S3, STS, SQS, KMS
• Blocks external/cross-account access
• Both are authorization policies

## What is Control Tower and when would you use it over plain Organizations?
Aliases: what is AWS Control Tower | Control Tower vs Organizations | when to use Control Tower
Tags: control-tower, landing-zone, account-factory, identity-center, config
Control Tower automates a secure multi-account landing zone on top of Organizations, IAM Identity Center, Config, and CloudTrail, with prebuilt controls and Account Factory.

• Orchestrates Organizations, not replaces
• Account Factory provisions accounts
• Controls: preventive, detective, proactive
• Use for greenfield governance

## How would you design a landing zone?
Aliases: what is a landing zone | how do you build a landing zone | landing zone baseline
Tags: landing-zone, baseline, log-archive, audit, cloudtrail, aft
A landing zone is a pre-configured, well-architected multi-account baseline — identity, logging, and guardrails — that new accounts drop into securely from day one.

• Dedicated log-archive, audit accounts
• Centralized CloudTrail, Config
• Baseline SCPs, Identity Center
• Terraform via AFT

## What is delegated administration and why does it matter?
Aliases: what is delegated admin | why delegate administration | how does delegated administration work
Tags: delegated-admin, management-account, guardduty, security-hub, least-privilege
Delegated administration lets a member account manage a service org-wide, so you keep the management account minimal and reduce its blast radius.

• Delegate GuardDuty, Security Hub, Config
• Management account = break-glass only
• Audit account often the delegate
• Least privilege at org tier

## When would you put a workload in its own account?
Aliases: why account-per-workload | account isolation strategy | when to separate accounts
Tags: account-isolation, blast-radius, boundary, quotas, environments
I'd isolate each workload or environment in its own account to get a hard security, blast-radius, and quota boundary that IAM alone can't provide.

• Account = strongest isolation boundary
• Separate prod, dev, security
• Limits credential-compromise blast radius
• Beyond my per-tenant KMS/RLS

## Explain preventive versus detective guardrails.
Aliases: preventive vs detective controls | types of guardrails | how do guardrails work
Tags: guardrails, controls, preventive, detective, config, scp
Preventive controls stop non-compliant actions before they happen, usually via SCPs; detective controls flag drift after the fact, usually via Config rules.

• Preventive = SCP deny
• Detective = Config rules
• Proactive = CloudFormation Hooks
• Layer all three, defense-in-depth

## Is the consolidated billing boundary a security boundary?
Aliases: what is consolidated billing | is billing a security boundary | consolidated billing benefits
Tags: consolidated-billing, payer-account, savings-plans, boundary, cost
Consolidated billing gives one payer account with shared volume discounts and Savings Plans, but it isn't a security boundary — the account is.

• Single payer, one invoice
• RI/Savings Plans shared default
• Billing ≠ security isolation
• Can disable discount sharing

## How do you enforce tagging, backup, and config standards across the org?
Aliases: what are management policies | tag and backup policies | how to enforce config org-wide
Tags: tag-policy, backup-policy, declarative-policy, aws-config, standardization
Organizations management policies standardize non-authorization settings org-wide — tag policies enforce tagging, backup policies push AWS Backup plans, declarative policies lock service configs.

• Tag policies standardize keys/values
• Backup policies enforce AWS Backup
• Declarative policies pin configs
• AWS Config detects drift

## How would you protect the Organizations management account?
Aliases: securing the management account | how to lock down the root account | management account best practices
Tags: management-account, root, mfa, break-glass, delegated-admin, scp
I'd lock down the management account as break-glass only — no workloads, hardware-MFA root, delegated services out, since SCPs never restrict it.

• SCPs don't limit management account
• Hardware MFA on root
• No workloads in payer
• Delegate admin to members
