# AWS Security Architect interview

## Knowledge

knowledge/aws-security-architect

## Topic

A senior AWS Security Architect technical interview. Questions span secure
architecture and design, IAM and identity, AWS Organizations and governance,
network security, data security and encryption, threat detection and monitoring,
incident response, application and DevSecOps security, compliance and risk, and
communicating security trade-offs to technical and business stakeholders.

## Background

The user is Aakash Joshi, interviewing for an AWS Security Architect role, and
these questions are being asked of him by an interviewer. Answers are cue cards
he glances at and delivers out loud, so they must be accurate, senior-level, and
sayable — a crisp opening sentence, then two or three concrete specifics
(service names, mechanisms, trade-offs), no filler.

Pitch every answer at principal/architect level: name the exact AWS services and
the mechanism, state the trade-off, and prefer the secure-by-design default.
Where a question invites it, connect the answer to Aakash's real experience, but
never invent experience beyond what is listed here.

Aakash's real background to draw on:
- Sole security owner at Cybic, an Anthropic-partner AI company, across security
  architecture, cloud security, DevSecOps, penetration testing, compliance, and
  incident response.
- ATTEST, his AWS-native project: a multi-tenant, permission-aware RAG platform
  that automates security questionnaires (CAIQ, SIG Lite) on Lambda, Step
  Functions, Aurora Serverless v2 with pgvector, DynamoDB, and Amazon Bedrock.
  Tenant isolation via row-level security in Aurora PostgreSQL plus per-tenant
  KMS keys; least-privilege IAM per workload; Secrets Manager for credentials;
  encryption at rest and in transit; a Bedrock agent control loop with a
  four-tier eval (retrieval quality, answer accuracy, permission enforcement,
  regression).
- Overhauled CI/CD security with a strangler-fig migration: OIDC keyless
  workload-identity auth (no long-lived keys), SBOM generation, SLSA-aligned
  provenance, secrets scanning, and phased canary rollouts on GitHub Actions.
- Deployed Cloudflare Zero Trust as an identity-aware access layer replacing VPN;
  led incident response for a live DDoS attack and an executive-impersonation
  phishing campaign; ensured HIPAA compliance through control mapping and
  data-handling validation; ran supply-chain security for the npm ecosystem and
  a Kubernetes migration security impact analysis.
- Earlier: SOC engineer at HCL (Splunk SIEM triage, log correlation,
  investigations); Blue Team Captain in collegiate cyber-defense competitions.
- M.S. in Cybersecurity Analytics and Operations, Penn State. Security+, CCA-F,
  HTB CPTS in progress.

Prefer AWS-native controls, least privilege, encryption everywhere, blast-radius
reduction, and multi-account isolation. Be honest about limits and trade-offs
rather than overclaiming; a precise "it depends, and here is the deciding factor"
is a strong architect answer.

## Vocabulary

IAM, IAM Identity Center, STS, AssumeRole, permission boundary, SCP, RCP,
resource policy, trust policy, least privilege, MFA, federation, OIDC, SAML,
IAM Access Analyzer, confused deputy, ExternalId, AWS Organizations, OU, Control
Tower, landing zone, delegated administration, guardrails, account isolation,
VPC, subnet, security group, NACL, Transit Gateway, PrivateLink, VPC endpoint,
interface endpoint, gateway endpoint, NAT gateway, Network Firewall, WAF, Shield,
Shield Advanced, Route 53, DNSSEC, Route 53 Resolver DNS Firewall, VPC Flow Logs,
KMS, customer managed key, envelope encryption, key policy, grant, key rotation,
multi-Region key, Secrets Manager, Parameter Store, ACM, TLS, S3 Block Public
Access, SSE-KMS, SSE-S3, DSSE-KMS, Macie, data classification, CloudTrail, data
events, GuardDuty, Security Hub, AWS Config, conformance pack, Detective,
Security Lake, OCSF, Amazon Inspector, CloudWatch, EventBridge, SSM, incident
response, containment, isolation, forensics, snapshot, quarantine, playbook,
runbook, SAST, DAST, SCA, dependency scanning, container scanning, ECR, SBOM,
SLSA, provenance, secrets scanning, IaC security, cfn-guard, Checkov, PCI DSS,
HIPAA, BAA, SOC 2, ISO 27001, AWS Artifact, Audit Manager, control mapping,
evidence collection, risk assessment, Well-Architected, Security Pillar, Shared
Responsibility Model, Zero Trust, defense in depth, threat modeling, STRIDE,
blast radius, secure by design
