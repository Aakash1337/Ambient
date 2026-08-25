# Experience & Behavioral

## Walk me through ATTEST, the multi-tenant RAG platform you built.
Aliases: Tell me about ATTEST | Describe your questionnaire automation platform | What is ATTEST's architecture
Tags: rag, bedrock, aurora, kms, serverless, multi-tenant
ATTEST is a multi-tenant, permission-aware RAG platform I built to auto-answer security questionnaires like CAIQ and SIG Lite on an AWS serverless stack.

• Lambda, Step Functions, Aurora Serverless v2
• pgvector retrieval, Bedrock agent loop
• Per-tenant KMS, RLS isolation
• Four-tier eval harness

## How did you enforce tenant isolation in ATTEST?
Aliases: How do you isolate tenants | Multi-tenant data separation | Prevent cross-tenant data leakage
Tags: multi-tenant, rls, kms, iam, aurora
I layered isolation: row-level security in Aurora Postgres scopes every query, and each tenant gets its own KMS encryption key.

• RLS policies keyed on tenant_id
• Per-tenant KMS keys, crypto isolation
• Least-privilege IAM per workload
• Defense-in-depth, not single control

## Tell me about the evaluation harness for your Bedrock agent.
Aliases: How did you test the agent | Four-tier eval explained | How you measured RAG quality
Tags: bedrock, eval, rag, permission-enforcement, regression
I hand-wrote a four-tier eval covering retrieval quality, answer accuracy, permission enforcement, and regression, because an agent answering questionnaires must never leak or hallucinate.

• Retrieval quality: right chunks
• Answer accuracy vs ground truth
• Permission enforcement: no cross-tenant
• Regression: catch prompt drift

## Walk me through your CI/CD OIDC keyless overhaul.
Aliases: How did you kill long-lived keys | Keyless CI/CD auth | GitHub Actions OIDC to AWS
Tags: oidc, cicd, iam, workload-identity, slsa
I ran a strangler-fig migration to OIDC workload identity so pipelines assume short-lived roles instead of storing long-lived cloud keys.

• GitHub OIDC provider, AssumeRoleWithWebIdentity
• Scoped trust policy: repo/branch claims
• SBOM, SLSA provenance, secrets scanning
• Phased canary rollout

## Tell me about a security incident you led end-to-end.
Aliases: Walk me through an incident | DDoS and phishing response | Describe your IR experience
Tags: incident-response, ddos, phishing, containment, comms
During live events I led two incidents: a DDoS mitigation and containment of an executive-impersonation phishing campaign, both under real-time pressure.

• DDoS: edge mitigation, rate limiting
• Phishing: containment, takedown, user comms
• Stakeholder updates during event
• Post-incident analysis, hardening

## Why did you replace the VPN with Cloudflare Zero Trust?
Aliases: Why Zero Trust over VPN | Identity-aware access rollout | How you modernized remote access
Tags: zero-trust, cloudflare, identity-aware, access, vpn
I replaced the flat-trust VPN with Cloudflare Zero Trust so access is identity-aware and per-application, not a broad network tunnel.

• VPN = flat network access
• Identity-aware policies per app
• MFA, device posture at edge
• Least privilege, smaller blast radius

## How did you approach HIPAA compliance?
Aliases: Tell me about your HIPAA work | Compliance control mapping | How you validated data handling
Tags: hipaa, compliance, control-mapping, encryption, phi
I mapped HIPAA controls to our actual data handling, then validated access, encryption, and data flows against them rather than trusting attestation.

• Control mapping to real controls
• Access + encryption validation
• Data-handling / PHI flow review
• Evidence, not developer attestation

## What's it like being the sole security owner at CYBIC?
Aliases: How do you handle owning security alone | Sole security engineer | Breadth of your role
Tags: ownership, breadth, prioritization, devsecops, architecture
As sole owner I span architecture, cloud security, DevSecOps, pentesting, compliance, and IR, so I prioritize by risk and automate everything repeatable.

• Breadth: architecture to IR
• Risk-based prioritization, limited hands
• Automate repeatable checks
• Verification-driven, not attestation

## Tell me about a hard security trade-off you had to make.
Aliases: A time security met business needs | Security vs velocity | Pragmatic security decision
Tags: trade-off, strangler-fig, velocity, risk, cicd
Migrating CI/CD to keyless OIDC, I chose a phased strangler-fig rollout over a big-bang cutover to avoid breaking every pipeline at once.

• Big-bang vs phased migration
• Kept builds green during change
• Canary rollout, measurable steps
• Security gain without velocity loss

## What's your biggest strength as a security engineer?
Aliases: What do you do best | Your key strength | What sets you apart
Tags: strength, verification, breadth, offense-defense
My strength is pairing offensive and defensive perspective with verification discipline: I confirm fixes by retesting, not by trusting developer attestation.

• Pentester plus blue-team background
• Verification-driven remediation (retest + SAST)
• Full stack: architecture to IR
• Automate to scale one person

## What's a real weakness or growth area for you?
Aliases: Where do you need to grow | A genuine weakness | Your development area
Tags: weakness, growth, organizations, scale, learning
My depth is single-account and small-team; my growth area is enterprise multi-account governance at scale, which is exactly why this AWS role appeals.

• Deep in single-account design
• Growing: Organizations, SCPs, Control Tower
• Actively studying (HTB CPTS in progress)
• Learn by building, not lurking

## Why do you want to be an AWS Security Architect?
Aliases: Why this role | Why AWS security | What draws you here
Tags: motivation, aws, architecture, secure-by-design
I've already built a secure-by-design AWS system as one person; I want to do that architecture work at enterprise scale across many accounts.

• Built ATTEST fully AWS-native
• Love threat modeling, boundaries
• Scale one app to org
• Design proactively, not just react

## Give me a STAR example of leading under pressure.
Aliases: Time you handled a crisis | STAR incident story | Delivered under pressure
Tags: star, incident-response, phishing, leadership, comms
Situation: during a live event, an attacker ran an executive-impersonation phishing campaign; I owned containment, communication, and the post-incident review.

• Task: contain, protect, communicate
• Action: takedown, user warnings, controls
• Result: campaign contained, stakeholders informed
• Follow-up: hardening, lessons learned

## Tell me about a time you pushed back on a developer or team.
Aliases: Time you disagreed | Enforcing security standards | Handling remediation disputes
Tags: remediation, verification, influence, communication, sast
Rather than accept a developer's word that a vulnerability was fixed, I insisted on retesting plus static analysis before closing findings.

• Attestation isn't verification
• Retest + SAST confirms fix
• Framed as shared quality goal
• Fewer reopened findings
