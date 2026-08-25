# Scenario Drills — Organizations & Governance

## How would you guarantee that nobody can turn off CloudTrail in any account across the org?
Aliases: Prevent disabling CloudTrail org-wide | Lock down CloudTrail everywhere | Stop anyone deleting trails
Tags: scp, cloudtrail, organizations, guardrail, logging
Attach an SCP at the org root denying cloudtrail:StopLogging, DeleteTrail, and UpdateTrail, and run a tamper-resistant organization trail from the management account.

• Deny StopLogging, DeleteTrail, UpdateTrail
• Org trail hides from members
• Caveat: mgmt account SCP-exempt
• Pair with Config drift detection

## How would you restrict every account to just two regions for data residency?
Aliases: Limit accounts to two regions | Enforce data residency by region | Block all other AWS regions
Tags: scp, aws:requestedregion, data-residency, organizations, regions
Attach an SCP denying all actions when aws:RequestedRegion falls outside your two approved regions, using NotAction to exempt global services like IAM.

• Deny on aws:RequestedRegion condition
• NotAction exempts IAM, CloudFront, Route53
• Global endpoints resolve to us-east-1
• Trade-off: whitelist global services carefully

## Why would you run GuardDuty from a dedicated security account instead of the management account?
Aliases: Why not GuardDuty in management account | Delegated admin for GuardDuty | Security account vs management account
Tags: guardduty, delegated-admin, management-account, least-privilege, blast-radius
Keeping security tooling out of the management account shrinks blast radius; delegate GuardDuty admin to a dedicated security account instead.

• Management account is highest-value target
• SCPs don't restrict management account
• Delegate admin, aggregate all findings
• Sources: CloudTrail, VPC flow, DNS

## How would you design an OU structure for a company with prod, staging, dev, and a security team?
Aliases: Design an OU structure | Organize accounts into OUs | OU layout for prod staging dev
Tags: organizations, ou, control-tower, multi-account, guardrails
Mirror AWS's multi-account guidance: a Security OU for log-archive and audit, a Workloads OU with Prod/Staging/Dev, plus Infrastructure and Sandbox OUs.

• Security OU: log-archive, audit accounts
• Workloads OU: prod, staging, dev
• Sandbox OU: loose experimentation guardrails
• Attach SCPs per OU

## An SCP you wrote broke a legitimate workload — how do you debug and roll back safely?
Aliases: SCP broke a workload | Debug an SCP access denial | Roll back a bad SCP
Tags: scp, cloudtrail, access-denied, rollback, testing
First scope the blast radius, then read CloudTrail AccessDenied events to find the denied action, and detach or narrow the SCP.

• CloudTrail errorMessage names explicit deny
• Detach SCP or narrow scope
• Test future SCPs in non-prod
• Stage rollout OU by OU

## A new team spun up shadow AWS accounts outside the org — how do you bring them under governance?
Aliases: Bring shadow accounts into the org | Enroll rogue accounts | Govern accounts created outside org
Tags: organizations, control-tower, account-enrollment, guardrails, invite
Invite each shadow account into the organization, enroll it through Control Tower Account Factory, and apply your baseline guardrails and centralized logging.

• Send org invitation, they accept
• Enroll existing account via Account Factory
• Apply baseline SCPs, logging
• Trade-off: needs AWSControlTowerExecution role

## What can an SCP not do?
Aliases: Limits of SCPs | What SCPs can't do | SCP restrictions and gotchas
Tags: scp, organizations, management-account, service-linked-roles, permissions-boundary
An SCP never grants permissions; it only caps the maximum, doesn't restrict the management account, and doesn't affect service-linked roles.

• Filters permissions, never grants them
• No effect on management account
• Doesn't affect service-linked roles
• Needs identity policies to actually allow

## How would you enforce mandatory tagging across all accounts for cost and security ownership?
Aliases: Enforce mandatory tagging org-wide | Require tags on resource creation | Tag governance across accounts
Tags: scp, tag-policies, config, tagging, organizations
Deny resource creation when required tags are missing via an SCP using aws:RequestTag, standardize with Organizations tag policies, and detect drift with Config.

• SCP: deny create without tags
• Conditions on aws:RequestTag, aws:TagKeys
• Config required-tags rule catches drift
• Trade-off: enforcement is per-service

## What's the value of a dedicated log archive account?
Aliases: Why a log archive account | Value of central logging account | Dedicated logging account benefits
Tags: log-archive, s3-object-lock, central-logging, immutability, security-ou
It centralizes immutable logs away from workload accounts, so even a compromised account operator can't alter or delete the evidence.

• Central sink: CloudTrail, Config, VPC logs
• S3 Object Lock for immutability
• Tightly restricted, read-mostly access
• Sits in the Security OU
