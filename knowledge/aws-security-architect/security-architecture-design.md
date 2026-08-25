# Security Architecture & Design

## What is defense-in-depth, and how would you apply it to an AWS workload?
Aliases: what is defense in depth | layered security on AWS | how do you layer controls
Tags: defense-in-depth, iam, kms, waf, security-groups, layered-controls
Defense-in-depth layers independent controls so no single failure is fatal — identity, network, data, detection, and response reinforcing each other.

• Identity, network, data, detection layers
• SG + NACL + WAF
• KMS encryption, least-privilege IAM
• Assume breach, no single point

## Explain the AWS Shared Responsibility Model.
Aliases: shared responsibility model | who secures what on AWS | security of vs in the cloud
Tags: shared-responsibility, ec2, lambda, s3, patching, iam
AWS secures the cloud — hardware, hypervisor, managed services — while I secure what's in it: data, IAM, configuration, and patching.

• "of" cloud vs "in" cloud
• EC2: I patch OS
• Lambda/S3: AWS patches runtime
• Customer always owns IAM, data

## How would you design a Zero Trust architecture on AWS?
Aliases: zero trust on AWS | design zero trust | never trust always verify
Tags: zero-trust, verified-access, vpc-lattice, iam-identity-center, sigv4, cloudflare
Zero Trust means never trust the network — verify every request explicitly with identity, least privilege, and continuous validation regardless of location.

• AWS Verified Access replaces VPN
• VPC Lattice service-to-service auth
• IAM Identity Center, SigV4 signing
• Cloudflare Zero Trust — my rollout

## Walk me through how you'd threat model an AWS application using STRIDE.
Aliases: threat modeling with STRIDE | how do you threat model | STRIDE methodology
Tags: threat-modeling, stride, data-flow-diagram, trust-boundaries, risk
I decompose the system into data flows and trust boundaries, then walk each element through STRIDE to enumerate threats and controls.

• Spoofing, Tampering, Repudiation
• Info disclosure, DoS, Elevation
• Map to auth, integrity, confidentiality
• DFD and trust boundaries first

## What do you mean by security boundaries and trust zones on AWS?
Aliases: security boundaries and trust zones | what is a trust zone | segmentation strategy
Tags: trust-zones, segmentation, vpc, subnets, accounts, isolation
A trust zone is a set of resources sharing a trust level; boundaries are where I enforce controls between differing levels.

• Accounts, VPCs, subnets as zones
• Public, private, data tiers
• Choke points: SG, endpoints
• Isolate prod from dev

## What does secure-by-design mean, and how do you enforce it?
Aliases: secure by design | shift security left | build security in
Tags: secure-by-design, guardrails, scp, aws-config, secure-defaults
Secure-by-design bakes security requirements into architecture from day one, so controls are built in rather than bolted on after delivery.

• Threat model before build
• Secure defaults, encryption on
• Guardrails as code: SCP, Config
• My CYBIC secure-by-design requirements

## How do you reduce blast radius in a multi-account AWS environment?
Aliases: blast radius reduction | limit the blast radius | contain a compromise
Tags: blast-radius, isolation, sts, kms, permission-boundaries, multi-account
Blast-radius reduction limits how far a single compromise spreads by isolating workloads, scoping permissions, and segmenting accounts, networks, and keys.

• Account-per-workload isolation
• Least-privilege, short-lived STS
• Per-tenant KMS keys — my ATTEST
• Permission boundaries cap escalation

## Explain the AWS Well-Architected Security Pillar and its design principles.
Aliases: well-architected security pillar | security pillar design principles | well architected framework security
Tags: well-architected, security-pillar, identity, traceability, automation, data-protection
The Security Pillar has seven design principles built on strong identity, traceability, security at all layers, automation, data protection, and incident readiness.

• Strong identity foundation
• Enable traceability, apply all layers
• Automate; protect data transit/rest
• Keep people from data; prepare

## How would you design a secure multi-account landing zone?
Aliases: secure landing zone | multi-account reference architecture | Control Tower design
Tags: control-tower, organizations, ous, scp, delegated-admin, logging
I'd use Control Tower to stand up a multi-account landing zone with OUs, SCP guardrails, and centralized logging and security tooling accounts.

• Control Tower, Organizations, OUs
• SCPs restrict, never grant
• Dedicated log archive account
• Delegated admin: GuardDuty, Security Hub

## Design a secure internet-facing three-tier web application on AWS.
Aliases: design a three-tier web app | secure web app architecture | reference architecture walkthrough
Tags: reference-architecture, cloudfront, waf, alb, rds, kms
I'd attach a WAF to an internet-facing ALB in public subnets, keep the app tier private, and isolate the data tier from the internet.

• CloudFront, WAF, Shield at edge
• ALB public, app private
• RDS isolated, no internet route
• KMS at rest, TLS transit

## How do you balance security trade-offs against cost, speed, and usability?
Aliases: security trade-offs | balance security and business | risk-based decisions
Tags: risk, trade-offs, compensating-controls, guardrails, communication
I frame security as risk reduction against cost and friction, quantify the risk, and let data owners make an informed decision.

• Risk vs cost vs agility
• Compensating controls, document risk
• Guardrails, not blocking gates
• Speak business risk, not jargon

## When would you use preventive versus detective versus responsive controls?
Aliases: preventive vs detective controls | types of security controls | control categories
Tags: preventive, detective, responsive, guardduty, config, cloudtrail
Preventive controls stop bad actions, detective controls surface them, and responsive controls contain and remediate — I layer all three.

• Preventive: IAM, SCP, SG
• Detective: GuardDuty, Config, CloudTrail
• Responsive: automated remediation, isolation
• No control is perfect

## How do you embed security into the SDLC from an architecture standpoint?
Aliases: DevSecOps architecture | security in the SDLC | shift-left pipeline security
Tags: devsecops, oidc, sbom, slsa, sast-dast, iac-scanning
I shift security left by embedding threat modeling, IaC scanning, SAST/DAST, and signed provenance into the pipeline as automated gates.

• OIDC keyless deploys — my CYBIC
• SBOM, SLSA provenance
• IaC scanning, secrets scanning
• Fail pipeline on criticals

## How would you approach a security architecture review of an existing AWS environment?
Aliases: how do you review an architecture | security architecture review | assess an AWS environment
Tags: architecture-review, well-architected, trusted-advisor, crown-jewels, gap-analysis
I start with the data flows and crown jewels, map the current controls against a threat model, then prioritize gaps by risk.

• Inventory accounts, data, trust zones
• Well-Architected review, Trusted Advisor
• Check IAM, encryption, logging
• Prioritize by blast radius
