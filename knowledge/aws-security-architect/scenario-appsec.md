# Scenario Drills — Application & DevSecOps Security

## How do you design security gates for a CI/CD pipeline without slowing developers to a crawl?
Aliases: How do you add security gates without slowing developers? | Where do scans go in the pipeline? | How do you keep security from blocking merges?
Tags: ci/cd, sast, dast, secrets-scanning, break-glass, devsecops
Gate on fast, high-signal checks — SAST, dependency, and secret scans block the PR; slow DAST runs async, with an audited break-glass.

• Secret and dependency scans: fail fast
• SAST inline on the PR
• DAST and pen tests async
• Trade-off: async delays runtime findings

## Walk me through your OIDC keyless CI/CD design, end to end.
Aliases: Walk me through your OIDC keyless CI/CD design. | How does keyless deployment to AWS work? | How do you do CI/CD without stored credentials?
Tags: oidc, iam, sts, sbom, slsa, canary
OIDC federates the runner to a scoped IAM role — no stored keys — then build, sign SBOM plus SLSA provenance, and canary out.

• OIDC token to AssumeRoleWithWebIdentity
• Trust policy pins sub claim
• Generate SBOM, signed provenance
• Trade-off: setup over rotating keys

## A dependency you use just had a critical CVE — walk me through your response.
Aliases: A dependency has a critical CVE — what do you do? | How do you respond to a vulnerable library? | Walk me through your CVE response.
Tags: cve, sbom, supply-chain, patching, exploitability
First scope blast radius from the SBOM, confirm the vulnerable path is actually reachable, then patch or mitigate and review the supply chain.

• SBOM query: where is it?
• Assess exploitability and reachability
• Patch, pin, or compensating control
• Trade-off: fast patch risks regression

## Why generate an SBOM, and what do you actually do with it?
Aliases: Why generate an SBOM? | What do you actually do with an SBOM? | What's the point of a software bill of materials?
Tags: sbom, inventory, cve, provenance, compliance
An SBOM is a machine-readable inventory of every component, so when a CVE drops you answer "are we affected?" in minutes, not days.

• Instant blast-radius CVE queries
• Feeds provenance and attestation
• Compliance and vendor evidence
• Trade-off: stale if not regenerated

## How do you secure secrets in a containerized deployment?
Aliases: How do you secure secrets in containers? | Where do container secrets come from at runtime? | How do you keep secrets out of images?
Tags: secrets-manager, containers, iam, ecs, eks
Never bake secrets into the image or leave them in env at rest — inject at runtime from Secrets Manager via a scoped IAM role.

• Fetch at runtime, scoped role
• Never in image layers
• Rotate through Secrets Manager
• Trade-off: runtime fetch dependency

## What's SLSA provenance, and what attack does it defend against?
Aliases: What is SLSA provenance? | What attack does provenance defend against? | Why sign build attestations?
Tags: slsa, provenance, supply-chain, attestation, sigstore
SLSA provenance is a signed attestation of what built an artifact, from which source and builder — it defends against build-system tampering.

• Binds artifact to source, builder
• Verify before deploy or admission
• Blocks injected malicious builds
• Trade-off: needs a trusted builder

## A container escapes to the host — what made that possible, and how do you reduce the risk?
Aliases: A container escaped to the host — how? | How do you prevent container breakout? | What makes container escape possible?
Tags: container-escape, namespaces, privileged, seccomp, runtime-security
Escape means the kernel and namespace boundary was crossed — usually a privileged container or kernel bug; reduce risk with least privilege and runtime detection.

• No --privileged, drop capabilities
• Non-root, read-only rootfs, seccomp
• Patch kernel, run runtime agent
• Trade-off: hardening versus compatibility

## How do you catch IaC misconfigurations before they deploy?
Aliases: How do you catch IaC misconfigurations before deploy? | How do you scan Terraform for security issues? | Policy as code for infrastructure?
Tags: iac, checkov, tfsec, cfn-guard, policy-as-code
Shift left with policy-as-code — run checkov, tfsec, or cfn-guard in CI so misconfigured Terraform or CloudFormation fails the build.

• Scan plans in the PR
• Org policies as code
• Backstop with Config, SCP guardrails
• Trade-off: false positives, tuning

## Developers want to bypass the security scan just this once for a hotfix — how do you handle it?
Aliases: Developers want to skip the scan for a hotfix — what do you do? | How do you handle a security bypass request? | What's your break-glass process?
Tags: break-glass, risk-acceptance, audit, hotfix, governance
Say yes with a controlled break-glass — a logged, time-boxed override with named risk acceptance and a mandatory retroactive review.

• Break-glass needs explicit approval
• Every override audited and alerted
• Retroactive scan plus fix ticket
• Trade-off: speed versus residual risk
