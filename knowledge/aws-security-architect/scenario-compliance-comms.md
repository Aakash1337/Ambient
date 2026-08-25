# Scenario Drills — Compliance, Risk & Communication

## How would you get a product that handles HIPAA data compliant — where do you start?
Aliases: Where do you start making a product HIPAA compliant? | How do you approach HIPAA for a new product? | First steps for HIPAA compliance?
Tags: hipaa, phi, baa, kms, cloudtrail, encryption
Start by scoping PHI flows — like I did at Cybic — because you can't protect what you haven't mapped.

• Scope, map PHI flows
• Encryption, access, audit controls
• Sign BAA via AWS Artifact
• Use HIPAA-eligible services only

## What is control mapping, and how can one control satisfy multiple frameworks?
Aliases: How does one control cover multiple frameworks? | What's control mapping? | Can encryption satisfy HIPAA and PCI at once?
Tags: control-mapping, hipaa, pci-dss, soc2, encryption, evidence
One control maps to many frameworks because they share intent; encryption-at-rest covers HIPAA, PCI DSS, and SOC 2 requirements simultaneously.

• Map controls to framework requirements
• One test, multiple evidence artifacts
• Reduces audit fatigue, duplication
• Trade-off: mapping upkeep as frameworks change

## An auditor wants evidence that access reviews actually happen. How do you produce it?
Aliases: How do you prove access reviews occur? | Show me evidence of periodic access reviews. | How do you evidence recertification to an auditor?
Tags: access-review, iam-access-analyzer, config, evidence, audit
Produce dated review artifacts: IAM Access Analyzer findings, AWS Config history, and signed tickets showing a recurring recertification cadence.

• Access Analyzer, unused-access findings
• AWS Config records over time
• Ticketed quarterly review cadence
• Automate evidence; watch manual gaps

## How do you run a risk assessment when you can't quantify probability precisely?
Aliases: How do you assess risk without hard probabilities? | Qualitative vs quantitative risk assessment? | How to rate risk when data is scarce?
Tags: risk-assessment, threat-modeling, qualitative, likelihood, impact
Use qualitative likelihood-times-impact scoring anchored by threat modeling, and document every assumption so the rating is defensible and repeatable.

• Likelihood times impact matrix
• Threat model to bound scenarios
• Document assumptions, data gaps
• Trade-off: subjective, revisit as data grows

## The business wants to accept a security risk you flagged. How do you handle that?
Aliases: What if the business accepts a risk you raised? | How do you handle formal risk acceptance? | Business overrides your finding — now what?
Tags: risk-acceptance, escalation, risk-owner, governance, documentation
Fine to accept — but the right business owner signs it: I quantify the exposure, document it, and escalate for formal acceptance.

• Quantify exposure in business terms
• Escalate to accountable risk owner
• Document formal, signed acceptance
• Set expiry and revisit date

## How does automating questionnaire responses like CAIQ or SIG reduce compliance risk?
Aliases: Why automate CAIQ/SIG responses? | How does questionnaire automation cut compliance risk? | Benefit of automating security questionnaires?
Tags: caiq, sig, questionnaire, evidence-traceability, attest, rag
Automation kills drift: every answer stays consistent, traceable to source evidence, and returned fast — which is exactly what ATTEST does.

• Consistent answers across reviewers
• Evidence traceability, permission-aware retrieval
• Speed, fewer stale responses
• Trade-off: needs human review, guardrails

## A customer asks which you have — SOC 2, ISO 27001, or PCI DSS — and why it matters. How do you explain it?
Aliases: SOC 2 vs ISO 27001 vs PCI DSS? | Which compliance report should we have? | What's the difference between SOC 2 and ISO 27001?
Tags: soc2, iso-27001, pci-dss, compliance, certification
SOC 2 is an attestation report, ISO 27001 certifies your ISMS, and PCI DSS is a mandate for handling card data.

• SOC 2: trust report, auditor opinion
• ISO 27001: certified management system
• PCI DSS: required for cardholder data
• Choose by customer, data type

## How would you explain to a non-technical CEO why you need budget for a security tool?
Aliases: How do you justify a security tool to the CEO? | Pitch security budget to a non-technical exec? | How to sell security spend to leadership?
Tags: business-risk, budget, communication, roi, executive
Frame it as dollars of risk avoided, not features: this tool lowers our expected breach cost and closes a specific exposure.

• Translate risk to dollars, impact
• Tie to revenue, deals, downtime
• Show likelihood reduction, ROI
• Avoid jargon; offer alternatives

## Two senior engineers disagree on an architecture's security approach and pull you in. How do you resolve it?
Aliases: How do you settle a security architecture dispute? | Two engineers disagree — how do you decide? | Resolving conflicting security opinions?
Tags: conflict, trade-off, decision, threat-model, documentation
Surface the real trade-off under the disagreement, decide with data and the threat model — not seniority — then document the rationale.

• Reframe as concrete trade-off
• Anchor on threat model, data
• Decide; own the call
• Document decision and rationale

## You have to tell a team their design has a serious flaw a week before launch. How do you deliver that?
Aliases: How do you flag a serious flaw before launch? | Delivering bad security news pre-launch? | How to raise a blocker close to release?
Tags: communication, risk, collaboration, delivery, remediation
Lead with the concrete risk and a path forward, framed collaboratively — I'm here to help you ship safely, not block.

• State impact, not blame
• Offer remediation options, effort
• Rank by severity, deadline
• Trade-off: delay vs residual risk

## Draw and walk me through the architecture of a system you secured.
Aliases: Whiteboard an architecture you secured. | Walk me through a system you designed securely. | Diagram a secure system you built.
Tags: architecture, attest, kms, aurora, bedrock, least-privilege
I'd whiteboard ATTEST: multi-tenant RAG on Lambda and Step Functions, isolated by Aurora row-level security plus per-tenant KMS keys.

• Bedrock agent, Aurora pgvector retrieval
• Per-tenant KMS, RLS isolation
• Least-privilege IAM per workload
• Trade-off: isolation cost vs blast radius

## How do you explain "we should do X, but it slows us down" so the business can decide?
Aliases: How do you present a security-vs-speed trade-off? | Framing "the right thing slows us down"? | How to let the business decide on a slowdown?
Tags: trade-off, communication, recommendation, monitoring, decision
Name the trade-off honestly, give a clear recommendation, and state what I'd monitor if we defer — then let the owner decide.

• Quantify cost and benefit
• Give explicit recommendation
• Define compensating controls, metrics
• Owner decides; you document

## A stakeholder wants a simple yes-or-no: is it secure? How do you respond?
Aliases: Is it secure — yes or no? | How do you answer "is this secure"? | Someone demands a binary security answer — what do you say?
Tags: risk-spectrum, threat-model, reframe, communication, assurance
There's no binary — security is a risk spectrum; I reframe to "secure enough against which threat model and which assets?"

• Reframe to threat model
• State residual risks plainly
• Map controls to those threats
• Give confidence, not false certainty

## You're the only security person and ten teams want your time this week. How do you communicate priorities?
Aliases: How do you prioritize as the only security person? | Ten teams, one you — how do you triage? | Communicating priorities when overloaded?
Tags: prioritization, risk-based-triage, communication, transparency, capacity
As the sole security owner, I triage by risk with transparent criteria, publish the queue, and say no with reasoning.

• Rank by risk, impact
• Publish criteria and queue
• Say no with reasoning
• Trade-off: escalate for more capacity
