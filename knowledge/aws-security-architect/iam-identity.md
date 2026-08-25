# IAM & Identity

## When would you use an IAM role instead of an IAM user, and why?
Aliases: IAM users vs roles | when to use a role | why avoid IAM users
Tags: iam, roles, users, sts, temporary-credentials
Prefer roles almost always: they deliver short-lived STS credentials with no long-term secrets to leak or rotate.

• Users = long-lived access keys
• Roles = temporary STS tokens
• Humans via Identity Center
• Workloads via instance/IRSA roles

## Walk me through how AWS evaluates whether a request is allowed.
Aliases: IAM policy evaluation logic | how a request is authorized | order of policy evaluation
Tags: iam, policy-evaluation, explicit-deny, scp, resource-policy
Default deny, an explicit Deny anywhere always wins, and every guardrail must allow while only identity or resource policies grant.

• Explicit deny beats everything
• SCP, boundary, session filter
• Cross-account needs both sides
• Same account: allow either

## Explain the different IAM policy types and how they interact.
Aliases: policy types in AWS | identity vs resource policies | SCP vs boundary vs session
Tags: iam, identity-policy, resource-policy, scp, permission-boundary
Identity and resource policies grant access; SCPs, permission boundaries, and session policies only cap it and never grant anything.

• Identity: attached to principal
• Resource: attached to resource
• Boundary + session = intersection
• SCP/RCP = org guardrails

## What is a permission boundary and when would you use one?
Aliases: permission boundaries | delegated role creation | capping IAM permissions
Tags: iam, permission-boundary, delegation, least-privilege
A permission boundary sets the maximum permissions an identity policy can grant, so effective access is the intersection of both.

• Never grants by itself
• Enables safe delegated admin
• Ignores resource policies, SCPs
• Blocks privilege escalation

## How do Service Control Policies fit into an IAM strategy?
Aliases: SCPs | service control policies | org-level guardrails | do SCPs grant access
Tags: organizations, scp, rcp, guardrails, governance
SCPs are Organizations guardrails that cap the maximum permissions in member accounts but never grant access on their own.

• Skip the management account
• Pair with RCPs (resource-side)
• Common: deny regions, root
• Attach to OU or account

## How does STS AssumeRole work under the hood?
Aliases: STS AssumeRole | how AssumeRole works | trust vs permissions policy
Tags: sts, assumerole, temporary-credentials, trust-policy
AssumeRole returns temporary credentials once the role's trust policy authorizes the caller, handing back key, secret, and session token.

• Trust policy = who assumes
• Permissions policy = what allowed
• Default 1h, max 12h
• Role chaining capped 1h

## How would you give on-prem workloads AWS access without long-lived keys?
Aliases: IAM Roles Anywhere | on-prem AWS credentials | X.509 workload identity | hybrid auth
Tags: iam-roles-anywhere, x509, pki, sts, hybrid
IAM Roles Anywhere lets external servers exchange X.509 certificates from a trusted CA for temporary STS credentials, no static keys.

• Trust anchor references CA
• Profile maps to role
• For on-prem, other clouds
• Certs from Private CA

## Design cross-account access between a central security account and workload accounts.
Aliases: cross-account access | assume role across accounts | central security account design
Tags: cross-account, sts, assumerole, resource-policy, ram
Use roles and AssumeRole, never shared keys: the workload account trusts the security account's principal and grants scoped permissions.

• Both sides must allow
• Resource policies for S3/KMS
• RAM shares subnets, resources
• Restrict with aws:PrincipalOrgID

## What is the confused deputy problem and how does ExternalId prevent it?
Aliases: confused deputy | ExternalId | third-party role assumption | SourceArn SourceAccount
Tags: confused-deputy, externalid, sourcearn, sts, trust-policy
A confused deputy is a trusted service tricked into using its permissions for an attacker; ExternalId is the shared secret preventing it.

• ExternalId for SaaS third-parties
• aws:SourceArn, aws:SourceAccount for services
• Put condition in trust policy
• Unique, unguessable per customer

## How would you manage workforce access across a multi-account org?
Aliases: IAM Identity Center | AWS SSO | permission sets | workforce federation
Tags: identity-center, sso, permission-sets, federation, scim
IAM Identity Center centralizes workforce sign-in across all accounts, mapping permission sets to IAM roles and syncing users from your IdP.

• Permission sets = provisioned roles
• SAML 2.0 to external IdP
• SCIM auto-provisions users, groups
• Replaces per-account IAM users

## When would you choose SAML versus OIDC for federation?
Aliases: SAML vs OIDC | federation protocols | web identity federation | workforce vs workload
Tags: saml, oidc, federation, cognito, jwt
SAML suits browser-based workforce SSO to the console; OIDC suits workloads and apps exchanging JWTs, like GitHub Actions or mobile.

• SAML = XML, workforce
• OIDC = JWT, workloads
• OIDC: EKS IRSA, CI/CD
• Cognito for app users

## How would you remove long-lived AWS keys from a CI/CD pipeline?
Aliases: OIDC keyless CI/CD | GitHub Actions to AWS | workload identity federation | remove static keys
Tags: oidc, github-actions, workload-identity, iam, keyless
I did exactly this: an IAM OIDC provider trusts the CI issuer, and the role trust policy pins repo and branch.

• Condition on sub, aud
• No stored access keys
• Short-lived STS per job
• Pair with SBOM, provenance

## How would you enforce MFA across an AWS environment?
Aliases: MFA enforcement | require MFA | multi-factor conditions | FIDO2 passkeys
Tags: mfa, condition-keys, fido2, root-account, security
Enforce MFA with condition keys like aws:MultiFactorAuthPresent, require it for sensitive actions and role assumption, and mandate FIDO2 on root.

• aws:MultiFactorAuthPresent in conditions
• aws:MultiFactorAuthAge for freshness
• FIDO2 passkeys or TOTP
• Deny actions without MFA

## How do you achieve least privilege in practice at scale?
Aliases: least privilege | scoping IAM policies | reduce permissions | right-sizing access
Tags: least-privilege, access-analyzer, access-advisor, conditions
Start with zero, generate policies from CloudTrail activity, prune with last-accessed data, and constrain everything with conditions instead of wildcards.

• Access Analyzer policy generation
• Access Advisor last-accessed data
• ABAC scales via tags
• Boundaries cap delegated roles

## What does IAM Access Analyzer do and how would you use it?
Aliases: IAM Access Analyzer | external access findings | unused access | policy validation
Tags: access-analyzer, provable-security, unused-access, policy-validation
Access Analyzer uses automated reasoning to flag external access, surface unused permissions, validate policies, and gate risky policies in CI/CD.

• External findings vs zone-of-trust
• Unused roles, keys, permissions
• Custom checks: check-no-new-access
• Provable security (Zelkova)

## How would you use condition keys and tags to scale permissions?
Aliases: condition keys | ABAC | tag-based access control | aws:PrincipalTag
Tags: condition-keys, abac, tags, aws-principaltag, least-privilege
Attribute-based access control matches principal tags to resource tags, so one policy scales across teams without editing it per resource.

• aws:PrincipalTag = aws:ResourceTag
• aws:PrincipalOrgID locks to org
• aws:SourceIp, aws:SecureTransport guards
• Tag on creation: aws:RequestTag
