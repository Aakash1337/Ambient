# Compliance & Risk

## How would you architect a PCI DSS compliant cardholder data environment on AWS?
Aliases: How do you build a PCI environment on AWS | PCI DSS architecture | reduce PCI scope in the cloud
Tags: pci dss, cde, artifact, kms, waf, scope
I'd isolate the cardholder data environment in a dedicated account to shrink audit scope, then map AWS controls to PCI DSS v4.0.1.

• Dedicated account, segmented VPC
• AWS: Level 1 service provider
• Artifact for AOC download
• KMS encryption, WAF, tokenization

## What is the AWS BAA and how do you build a HIPAA-compliant workload?
Aliases: How do you handle HIPAA on AWS | what is a business associate agreement | putting PHI in AWS
Tags: hipaa, baa, phi, artifact, encryption, healthcare
The BAA is the contract letting you put PHI on AWS; I accept it self-service in AWS Artifact, then use only HIPAA-eligible services.

• BAA self-service in Artifact
• HIPAA-eligible services only
• No HIPAA certification exists
• CYBIC: HIPAA control mapping

## Explain the difference between AWS's SOC 2 report and your own SOC 2.
Aliases: what does AWS SOC 2 cover | do I still need my own SOC 2 | SOC 1 vs SOC 2 vs SOC 3
Tags: soc 2, trust services criteria, artifact, type ii, inheritance
AWS's SOC 2 report covers the cloud infrastructure; I still need my own SOC 2 for everything I build in the account.

• SOC 1/2/3 via Artifact
• Type II: period, not snapshot
• Five Trust Services Criteria
• Inherit infra, own app

## How does AWS's ISO 27001 certification help you, and what's still your responsibility?
Aliases: is AWS ISO 27001 certified | ISO 27001 shared controls | ISMS on AWS
Tags: iso 27001, isms, artifact, annex a, statement of applicability
AWS holds ISO 27001:2022 certification for its infrastructure, which I inherit as a control, but I still run my own ISMS.

• Certificate via AWS Artifact
• ISO 27001:2022, Annex A
• Own ISMS, Statement of Applicability
• Inherited vs customer controls

## What is AWS Artifact and what would you pull from it during an audit?
Aliases: what does AWS Artifact do | where do you get compliance reports | how to get the BAA
Tags: artifact, compliance reports, baa, nda, audit
AWS Artifact is the self-service portal where I download compliance reports and accept legal agreements like the BAA, at no cost.

• Reports: SOC, PCI, ISO
• Agreements: BAA, NDA
• Share with auditors, NDA
• Point-in-time, not continuous

## How would you use AWS Audit Manager to prepare for a compliance audit?
Aliases: what does Audit Manager do | automate audit prep on AWS | continuous evidence collection
Tags: audit manager, frameworks, evidence, config, security hub
Audit Manager continuously collects evidence and maps it to prebuilt frameworks like PCI DSS, HIPAA, and SOC 2, then generates assessment reports.

• Prebuilt and custom frameworks
• Evidence: Config, CloudTrail, Security Hub
• Continuous vs point-in-time
• Assessment reports for auditors

## How would you use Config conformance packs to map and enforce controls across accounts?
Aliases: what is a conformance pack | control mapping with AWS Config | enforce compliance org-wide
Tags: config, conformance packs, organizations, remediation, control mapping
A conformance pack bundles Config rules and remediation into one template you deploy org-wide to map and enforce controls.

• Sample packs: PCI, HIPAA, NIST
• Deploy via Organizations, all accounts
• Detective plus auto-remediation
• Per-control compliance dashboard

## How would you automate compliance evidence collection across a multi-account org?
Aliases: automate evidence gathering | continuous compliance evidence | replace manual audit prep
Tags: evidence, audit manager, config, cloudtrail, automation
I'd centralize evidence with Audit Manager pulling from Config, CloudTrail, and Security Hub, so audits become continuous instead of a fire drill.

• Audit Manager as aggregator
• Config rules show control state
• CloudTrail: full audit trail
• HCL: manual evidence background

## Walk me through how you'd run a risk assessment for a new AWS workload.
Aliases: how do you do a risk assessment | risk assessment methodology | how do you score risk
Tags: risk assessment, threat modeling, nist, risk register, treatment
I start by identifying assets and data classification, threat-model the workload, then score risks by likelihood and impact into a risk register.

• Threat modeling with STRIDE, CYBIC
• Likelihood times impact scoring
• Treat: accept, mitigate, transfer, avoid
• NIST 800-30 framing

## Explain the shared responsibility model in the context of compliance.
Aliases: who is responsible for compliance on AWS | shared responsibility for audits | what does AWS cover
Tags: shared responsibility, inherited controls, iam, configuration, governance
AWS owns compliance of the cloud; I own compliance in the cloud, inheriting their controls but owning config, IAM, and data.

• AWS: hardware, hypervisor, facilities
• Customer: config, IAM, encryption
• Inherited vs shared controls
• Compliance is not automatic

## How would you approach automating security questionnaires like CAIQ and SIG Lite?
Aliases: automate vendor security questionnaires | CAIQ and SIG Lite automation | RAG for compliance questionnaires
Tags: caiq, sig lite, csa star, rag, bedrock, questionnaires
That's exactly what I built with ATTEST — permission-aware RAG over our control evidence that drafts answers for CAIQ and SIG Lite.

• CAIQ: CSA STAR questionnaire
• SIG Lite: Shared Assessments
• Bedrock RAG, Aurora pgvector
• Human review before submit

## How would you enforce data residency and sovereignty requirements on AWS?
Aliases: how do you keep data in-region | data sovereignty on AWS | restrict which regions data lives in
Tags: data residency, regions, scps, kms, sovereignty
Residency starts with choosing your Region, then enforcing it with SCPs, since AWS stores data in-Region but some services are global.

• SCPs restrict allowed Regions
• Watch global services (IAM, edge)
• KMS keys are regional
• AWS European Sovereign Cloud
