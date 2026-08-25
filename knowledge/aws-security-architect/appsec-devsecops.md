# Application & DevSecOps Security

## What does "shift-left" security mean, and how would you embed it in the SDLC?
Aliases: what is shift-left security | how do you shift security left | embedding security in the SDLC
Tags: shift-left, sdlc, devsecops, sast, pre-commit
Shift-left means moving security controls earlier — into design, commit, and build — so defects are caught before they ever reach production.

• threat modeling at design
• pre-commit hooks, IDE SAST
• fail fast in CI
• cheaper to fix early

## What's the difference between SAST, DAST, and SCA, and when do you use each?
Aliases: SAST vs DAST vs SCA | static vs dynamic analysis | when to use each scan type
Tags: sast, dast, sca, codeguru, zap
SAST analyzes source code statically, DAST tests the running app from outside, and SCA inventories third-party dependencies for known CVEs.

• SAST: CodeGuru Security, semgrep
• DAST: OWASP ZAP, Burp
• SCA: Dependabot, npm audit
• SAST false positives; DAST runtime-only

## How would you secure a CI/CD pipeline end to end?
Aliases: securing CI/CD | pipeline security controls | harden the build pipeline
Tags: ci/cd, github-actions, oidc, least-privilege, provenance
I treat the pipeline as a production system — I overhauled ours with least-privilege runners, keyless OIDC, pinned dependencies, and signed, provenanced artifacts.

• OIDC, no long-lived keys
• pin actions to SHA
• SBOM + SLSA provenance
• canary rollout, secrets scanning

## Design a keyless deployment from GitHub Actions to AWS with no long-lived keys.
Aliases: OIDC to IAM | GitHub Actions keyless deploy | workload identity federation to AWS
Tags: oidc, iam, sts, github-actions, federation
I register GitHub's OIDC provider in IAM, then let the workflow assume a role via AssumeRoleWithWebIdentity — short-lived STS creds, zero stored secrets.

• IdP: token.actions.githubusercontent.com
• trust policy scopes sub claim
• condition on repo, branch/env
• STS creds, ~1 hour

## What is an SBOM, and how would you generate and use one?
Aliases: what is an SBOM | software bill of materials | generating SBOMs in CI
Tags: sbom, cyclonedx, spdx, inspector, supply-chain
An SBOM is a machine-readable inventory of every component and dependency in a build, so you can answer "am I affected?" fast.

• formats: CycloneDX, SPDX
• generate: Syft, Inspector export
• match against new CVEs
• Log4Shell-style rapid triage

## Explain SLSA and build provenance — how would you achieve build integrity?
Aliases: what is SLSA | build provenance | supply-chain levels for software artifacts
Tags: slsa, provenance, attestation, in-toto, build-integrity
SLSA is a framework of levels for build integrity; provenance is signed, tamper-evident metadata proving what built an artifact, from what source.

• build track, levels L1–L3
• provenance: in-toto attestation
• hermetic, isolated builders
• verify before deploy

## How do you handle container image scanning in AWS?
Aliases: ECR image scanning | container vulnerability scanning | Inspector for containers
Tags: ecr, inspector, container, scan-on-push, trivy
I enable ECR enhanced scanning powered by Amazon Inspector for continuous CVE detection — on push and when new vulnerabilities are disclosed.

• enhanced scanning = Inspector
• basic scanning = on-push only
• minimal/distroless base images
• Trivy as CI gate

## How would you defend against software supply-chain attacks?
Aliases: supply-chain security | npm dependency attacks | protecting the dependency chain
Tags: supply-chain, sca, npm, lockfiles, provenance
I reduce trust in third-party code — pin and verify dependencies, generate SBOMs, sign artifacts, enforce provenance — as I did leading npm-ecosystem mitigation.

• lockfiles, pin exact versions
• npm --ignore-scripts, private registry
• typosquat, dependency-confusion defense
• SLSA provenance verification

## How do you scan for secrets and keep credentials out of code?
Aliases: secrets scanning | preventing hardcoded secrets | git secret detection
Tags: secrets-scanning, gitleaks, push-protection, secrets-manager, rotation
I combine pre-commit and CI secret scanning with push protection to block leaks, plus centralized storage so there's nothing to hardcode.

• tools: gitleaks, trufflehog
• GitHub push protection
• store in Secrets Manager
• rotate + revoke on leak

## How would you secure Infrastructure-as-Code before it deploys?
Aliases: IaC scanning | Terraform/CloudFormation security | policy-as-code for infra
Tags: iac, checkov, tfsec, cfn-guard, terraform
I scan IaC in CI with policy-as-code so misconfigurations fail the build before any resource exists — public buckets, open SGs, unencrypted volumes.

• cfn-guard for CloudFormation
• checkov, tfsec/Trivy for Terraform
• guardrails caught pre-apply
• plan-time, not runtime

## Explain signed artifacts — how would you verify image signatures at deploy?
Aliases: signing container images | cosign / Notation | verifying artifact signatures
Tags: signing, cosign, aws-signer, notation, admission-control
I sign every artifact at build so deploys only accept trusted images — verifying signatures at admission control before anything runs.

• cosign / Sigstore signing
• AWS Signer + Notation (OCI)
• Kyverno/OPA admission gate
• keyless signing, OIDC identity

## When would you block a build versus warn — how do you gate without killing velocity?
Aliases: security gates vs developer velocity | fail build or warn | tuning pipeline policy
Tags: policy, gating, severity, velocity, false-positives
I gate on severity and exploitability: block on critical/high with known exploits, warn on the rest, and tune out noise to keep trust.

• fail: critical, secrets, license
• warn: low/medium, informational
• baseline + suppress false positives
• break-glass with approval

## Design a DevSecOps pipeline for a serverless AWS application.
Aliases: serverless DevSecOps | securing Lambda/Step Functions delivery | secure serverless pipeline
Tags: serverless, lambda, iam, oidc, least-privilege
I secure serverless end to end with least-privilege IAM per Lambda, keyless OIDC deploys, IaC scanning, and dependency gates.

• per-function least-privilege IAM
• SAST/SCA on TypeScript
• OIDC deploy, no static keys
• cfn-guard on IaC templates
