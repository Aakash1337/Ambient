# Scenario Drills — IAM & Identity

## A developer just pushed our AWS access keys to a public GitHub repo—walk me through your first thirty minutes.
Aliases: Leaked keys on GitHub, first 30 minutes | AWS keys pushed to a public repo | Access keys exposed, walk me through the response
Tags: incident-response, cloudtrail, iam, credential-leak, rotation
Deactivate the key immediately, scope blast radius in CloudTrail, hunt for attacker persistence, then rotate and run a blameless post-incident.

• Deactivate first, preserve for forensics
• Scope blast radius in CloudTrail
• Hunt persistence: new users, roles
• Rotate, then blameless post-incident

## CI/CD needs to deploy to AWS and the engineers want to store an access key in the pipeline—what do you propose instead, and why?
Aliases: Access key in CI/CD versus the alternative | How should CI deploy to AWS without keys | Storing an AWS key in the pipeline
Tags: oidc, ci-cd, federation, sts, workload-identity
Don't store static keys; use OIDC federation so CI assumes a role for short-lived, auto-expiring credentials scoped per pipeline.

• No key to leak or rotate
• CI assumes role via IdP token
• Ran this at Cybic keyless
• Trade-off: initial trust setup

## Walk me through a trust policy for GitHub Actions OIDC—which claims do you validate, and why?
Aliases: GitHub Actions OIDC trust policy | What claims to validate for GitHub OIDC | Trust policy for keyless CI
Tags: oidc, trust-policy, github-actions, sts, claims
Validate the issuer is GitHub's OIDC provider, the audience is sts.amazonaws.com, and the subject claim pins a specific repo and branch.

• iss: GitHub's OIDC provider
• aud: sts.amazonaws.com, blocks reuse
• sub: pin repo and branch
• Wildcard sub is a major risk

## A partner company needs read access to one S3 bucket in our account—how do you grant it safely?
Aliases: Partner read access to one S3 bucket | Grant cross-account S3 access safely | External company needs bucket read
Tags: cross-account, s3, externalid, iam-role, confused-deputy
Grant a cross-account IAM role they assume, scoped read-only to that one bucket, with an ExternalId to prevent the confused-deputy problem.

• Role plus ExternalId over keys
• Read-only, scoped to prefix
• Bucket policy is simpler, no assume
• Trade-off: role adds a hop

## My IAM policy has an explicit allow, but the action is still denied—why?
Aliases: Explicit allow but the action denied | Why is my IAM allow not working | Policy evaluation order
Tags: iam, policy-evaluation, scp, permission-boundary, explicit-deny
Something higher in the chain overrides it—an explicit deny, an SCP, a permission boundary, or a session policy, all evaluated before your identity allow.

• Explicit deny always wins
• SCP may not allow it
• Boundary or session policy caps it
• Check the resource policy too

## A team keeps requesting broad IAM permissions "to be safe"—how do you push back constructively?
Aliases: Team wants broad IAM to be safe | Pushing back on over-broad permissions | Enforcing least privilege politely
Tags: least-privilege, access-analyzer, iam, cloudtrail
Start narrow and widen from evidence: grant the minimum, then use Access Analyzer and last-accessed data to add only what's actually used.

• Broad now is audit debt
• Access Analyzer generates scoped policy
• CloudTrail proves real usage
• Frame it as faster approval

## When would you use a permission boundary versus an SCP—both limit, so what's the difference?
Aliases: Permission boundary versus SCP | When to use a boundary or an SCP | Difference between boundary and SCP
Tags: permission-boundary, scp, organizations, iam, guardrails
Both cap permissions at different layers: an SCP is an org or account guardrail, while a boundary is a per-identity ceiling delegated to developers.

• SCP: account or OU guardrail
• Boundary: per-principal ceiling
• Boundary lets devs self-serve
• SCP skips the management account

## Contractors need temporary access to production—design it for me.
Aliases: Temporary prod access for contractors | Design contractor access to production | Short-lived access for external staff
Tags: identity-center, mfa, temporary-access, sso, least-privilege
Use IAM Identity Center with time-bound permission sets and MFA, so contractors get short-lived, auto-expiring access instead of standing credentials.

• Permission sets, not IAM users
• MFA enforced at login
• Sessions expire automatically
• Trade-off: SSO setup upfront

## How do you detect over-privileged roles across hundreds of accounts?
Aliases: Find over-privileged roles across accounts | Detect unused IAM permissions at scale | Auditing roles org-wide
Tags: access-analyzer, last-accessed, iam, organizations, unused-access
Use IAM Access Analyzer's unused-access findings plus last-accessed data org-wide to surface roles with permissions or credentials they never actually use.

• Unused-access analyzer flags stale roles
• Last-accessed data prunes actions
• Delegate the analyzer to org account
• Trade-off: tune out noise

## A service account's credentials may be compromised, but you're not sure—how do you investigate without breaking production?
Aliases: Service account maybe compromised | Investigate suspected credential compromise safely | Check for breach without breaking prod
Tags: cloudtrail, guardduty, incident-response, service-account, revocation
Investigate before you revoke: review CloudTrail for anomalous calls, check GuardDuty findings, then stage revocation so production doesn't break.

• CloudTrail: odd IPs, regions, actions
• GuardDuty flags credential exfil
• Stage: deactivate, watch, then rotate
• Trade-off: speed versus uptime

## Implement least privilege for a Lambda that reads S3, writes DynamoDB, and calls KMS.
Aliases: Least privilege Lambda S3 DynamoDB KMS | Scope a Lambda execution role | Minimal IAM for a Lambda function
Tags: lambda, least-privilege, iam, kms, dynamodb, s3
Give the execution role only s3:GetObject on that bucket, dynamodb:PutItem on that table, and the specific KMS actions on that one key.

• Scoped ARNs, no wildcards
• kms:Decrypt/GenerateDataKey, single key
• Did this per-workload on ATTEST
• Trade-off: more policy upkeep

## A reviewer flags Resource: "*" in a KMS key policy as a finding—are they right?
Aliases: Resource star in a KMS key policy | Is a wildcard resource in a key policy a finding | KMS key policy Resource star
Tags: kms, key-policy, iam, wildcard, least-privilege
They're wrong for a key policy: Resource "*" scopes to this key and is standard; the risk is kms:* on "*" in an identity policy.

• Key policy "*" means this key
• Key policy is authoritative
• Real risk: kms:* identity Resource "*"
• That grants any key

## How does STS AssumeRole work under the hood, and what's the security benefit over static keys?
Aliases: How does STS AssumeRole work | AssumeRole under the hood | Why roles beat static keys
Tags: sts, assumerole, temporary-credentials, iam, cloudtrail
You call STS, it verifies the trust policy, then mints temporary auto-expiring credentials, so there's no long-lived secret to leak and every use is auditable.

• Returns key, secret, session token
• Credentials expire, 15min to 12hr
• Trust policy gates assumption
• Every call auditable in CloudTrail

## A user has console MFA, but their long-lived access keys don't require it—is that a problem?
Aliases: Console MFA but keys skip MFA | Is MFA on console but not API a problem | Enforce MFA on API calls
Tags: mfa, iam, access-keys, sts, multifactorauthpresent
Yes—MFA on console but not API leaves a bypass; enforce it on API calls with an aws:MultiFactorAuthPresent condition, or drop the long-lived keys.

• Long-lived keys bypass MFA
• Condition: aws:MultiFactorAuthPresent true
• Better: replace keys with roles
• Needs STS GetSessionToken with MFA
