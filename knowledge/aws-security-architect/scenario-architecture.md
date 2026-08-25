# Scenario Drills — Security Architecture & Design

## How would you threat model a new public-facing API before you launch it?
Aliases: How do you threat model a new API? | Threat modeling before launch? | STRIDE a public-facing API?
Tags: threat modeling, stride, api, trust boundaries, abuse cases
Biggest risk is shipping blind — map assets, entry points, and trust boundaries, then run STRIDE plus abuse cases fast.

• Inventory assets, data flows, endpoints.
• Draw trust boundaries first.
• STRIDE each boundary; add abuse cases.
• Trade-off: timebox depth, not coverage.

## A startup runs everything in one AWS account and is onboarding enterprise customers — what do you change first?
Aliases: Single AWS account onboarding enterprise customers? | What do you change first for enterprise? | One account, multi-account migration?
Tags: multi-account, organizations, scp, blast radius, isolation
First split into multi-account with AWS Organizations — one account can't isolate blast radius or satisfy enterprise assurance.

• Organizations with SCP guardrails.
• Separate prod, dev, security accounts.
• Centralize CloudTrail, GuardDuty via delegation.
• Trade-off: added account-management overhead.

## An engineer says "encryption everywhere" makes the system secure — how do you respond?
Aliases: Is encryption everywhere enough? | Does encryption make it secure? | Encryption as security silver bullet?
Tags: encryption, defense in depth, iam, kms, least privilege
Encryption is one layer, not security — it stops eavesdroppers, not an attacker holding valid credentials.

• Defense in depth; many layers.
• Encryption ignores authorized-but-malicious access.
• Add IAM, monitoring, least privilege.
• Trade-off: layers cost complexity.

## You inherit a flat VPC where every service can reach every other — how do you segment it without a big-bang rewrite?
Aliases: Segment a flat VPC? | Network segmentation without a rewrite? | Everything reaches everything — fix it?
Tags: vpc, security groups, nacl, subnets, segmentation
Segment incrementally — tighten security groups first, since they're stateful and reversible, then carve subnets later.

• SGs reference SGs, not CIDRs.
• Start permissive, log, then restrict.
• NACLs stateless — coarse subnet layer.
• Trade-off: incremental means temporary gaps.

## How would you design a secure architecture for a healthcare SaaS handling PHI?
Aliases: Secure architecture for PHI? | HIPAA healthcare SaaS design? | Design for handling patient data?
Tags: hipaa, phi, kms, cloudtrail, guardduty, isolation
Design for HIPAA end-to-end — encrypt PHI, isolate tenants, scope access least-privilege, and audit every layer.

• Sign BAA; use eligible services.
• KMS encryption, per-tenant keys.
• CloudTrail audit; GuardDuty threat detection.
• Trade-off: isolation raises cost, ops.

## Product wants to store users' third-party API keys — how do you design the storage?
Aliases: How to store third-party API keys? | Storing users' secrets securely? | Design secret storage on AWS?
Tags: secrets manager, kms, envelope encryption, iam, rotation
Store third-party keys in Secrets Manager with envelope encryption and per-user access scoping — never in the database.

• Secrets Manager, KMS envelope encryption.
• Scope IAM per-user, per-tenant.
• Rotate and audit access.
• Trade-off: per-secret cost adds up.

## The business sees security review as a bottleneck — how do you embed security without becoming the blocker?
Aliases: Security seen as a bottleneck? | Embed security without blocking? | Shift left, guardrails over gates?
Tags: shift left, guardrails, ci/cd, oidc, sbom, paved roads
Shift left with guardrails over gates — paved roads make the secure path the easy default.

• Guardrails in CI/CD, not gates.
• CI: OIDC, SBOM, secrets scanning.
• Paved roads, secure defaults.
• Trade-off: upfront platform investment.

## When do you choose account-level versus VPC-level versus IAM-level isolation to separate workloads?
Aliases: Account vs VPC vs IAM isolation? | When to isolate at which level? | Choosing the workload isolation boundary?
Tags: account, vpc, iam, isolation, blast radius
Choose by blast radius — accounts for hard isolation, VPCs for network separation, IAM for identity-level within an account.

• Account: strongest, billing and quota boundary.
• VPC: network isolation, same account.
• IAM: finest, weakest blast containment.
• Trade-off: stronger isolation, more overhead.

## A single god Lambda role is shared by twenty functions — what's wrong and how do you fix it incrementally?
Aliases: Shared god Lambda role — fix it? | One IAM role for many functions? | Least privilege for Lambda roles?
Tags: lambda, iam, least privilege, access analyzer, roles
Shared god role breaks least privilege — one compromise inherits all twenty functions' permissions; split into per-function roles.

• One role per function.
• Migrate incrementally, lowest-risk first.
• Use Access Analyzer to scope.
• Trade-off: more roles to manage.

## How do you decide what's secure enough to ship when you're the only security person?
Aliases: What's secure enough to ship? | Risk-based prioritization when solo? | Only security person — what to fix?
Tags: risk, prioritization, likelihood, impact, threat modeling
Prioritize by risk — likelihood times impact — fix what's exploitable and high-impact, then accept and document the rest.

• Rank likelihood times impact.
• Block only exploitable, high-impact issues.
• Document accepted risks explicitly.
• Trade-off: residual risk always remains.
