# Experience & Behavioral — Extended

## Tell me about yourself
Aliases: Walk me through your background | Give me your intro | Who are you professionally
Tags: intro, career-arc, aws, security, sole-owner
I'm a security engineer whose path runs from supporting security on a team to owning a whole program, increasingly AWS-native.

• SOC roots, then sole owner
• Sole security owner at Cybic
• M.S. Penn State, Blue Team competitor
• Best work is AWS-native

## Tell me about your AI SIEM prototype
Aliases: What's the local SIEM project | Explain your AI SIEM | Your homegrown detection tool
Tags: siem, local-first, ai-triage, guardduty, detection
I built a lightweight, local-first AI SIEM for privacy-bound, resource-constrained environments, which sharpened my judgment on when managed detection wins.

• Ingest, normalize, correlate logs
• AI narratives explain why
• Clarified GuardDuty, Security Hub value
• Managed detection wins when available

## Tell me about your penetration testing program
Aliases: Explain your pentest program | How do you run pentests | Your security testing cadence
Tags: pentest, remediation, retest, automation, appsec
I run recurring, scoped assessments of production web apps and APIs, and I close findings on independent retest, not developer attestation.

• Black-box and authenticated cycles
• Themes: creds, sessions, auth
• Independently retest every fix
• Recon and report automation

## Tell me about your Zero Trust homelab
Aliases: Explain your homelab | Your Zero Trust setup | How's your lab built
Tags: zero-trust, cloudflare, homelab, verified-access, privatelink
My self-hosted services sit behind Cloudflare Zero Trust with no exposed inbound ports and identity-verified access per service.

• Tunnels replace inbound holes
• WireGuard only for raw reachability
• Maps to AWS Verified Access
• PrivateLink over open ports

## Why RAG instead of fine-tuning for ATTEST?
Aliases: Why not fine-tune ATTEST | RAG versus fine-tuning | Why retrieval over training
Tags: rag, fine-tuning, data-governance, auditability, multi-tenant
For a multi-tenant compliance product, RAG keeps tenant data out of the weights, stays fresh, and lets every answer cite its source.

• Data stays isolated, deletable
• Reads current docs at query
• Answers cite their sources
• Weights wrong place for customer data

## Why OIDC federation over rotating access keys?
Aliases: Why keyless CI/CD | OIDC versus key rotation | Why federate instead of rotate
Tags: oidc, federation, credentials, ci-cd, keyless
Rotation only shrinks the exposure window, but OIDC federation eliminates the long-lived credential class entirely, minting short-lived scoped credentials per run.

• Rotation leaves the secret existing
• No key to steal
• Short-lived, scoped, auditable creds
• Safest secret is none

## How do you test tenant isolation?
Aliases: How's isolation verified | Prove tenant separation | Testing cross-tenant access
Tags: tenant-isolation, eval, rls, kms, defense-in-depth
My eval suite actively attempts cross-tenant retrieval and requires it to fail, backed by row-level security and per-tenant KMS keys.

• Isolation is a test case
• Cross-tenant attempts must fail
• Row-level security beneath it
• Per-tenant KMS keys

## What's the hardest part of being a one-person security team?
Aliases: Hardest part of solo security | Challenge of a one-person team | What's tough about owning it solo
Tags: solo, prioritization, automation, risk, trade-offs
The hardest part is ruthless prioritization, deciding what not to do, and knowing when good enough is genuinely the right call.

• Rank everything by risk
• Automate repeatable work
• Keep judgment human
• Call diminishing returns

## Why this role and why consulting?
Aliases: Why consulting | Why this role | Why do you want this job
Tags: consulting, motivation, aws, ownership, breadth
Consulting combines the breadth I had serving many stakeholders with the ownership depth I built solo, pointed at AWS security specifically.

• Team breadth, then solo ownership
• Consulting applies depth widely
• Want to go deep on AWS
• Strongest work already AWS

## What questions do you have for us?
Aliases: Any questions for us | What do you want to ask | Your questions for the team
Tags: questions, aws-estate, success, outcomes, alignment
I'd ask what the AWS estate looks like today and what success looks like for this consultant in the first 90 days.

• Multi-account org already standing?
• First 90-day success measure?
• Managed control versus custom build?
• Where security function sits
