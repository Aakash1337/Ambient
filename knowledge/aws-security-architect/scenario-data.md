# Scenario Drills — Data Security & Encryption

## Can you explain envelope encryption and why AWS built KMS around it?
Aliases: What is envelope encryption? | Why does KMS use data keys? | Why the 4KB KMS limit?
Tags: kms, envelope encryption, data key, performance, blast radius
Envelope encryption uses a local data key to encrypt the data, then a KMS key wraps that data key.

• GenerateDataKey returns plaintext plus ciphertext
• KMS 4KB limit; bulk encrypted locally
• KMS key material never leaves KMS
• Trade-off: extra KMS decrypt call

## How would you design encryption for a multi-tenant SaaS where tenants demand cryptographic isolation?
Aliases: How do you isolate tenant data cryptographically? | Encryption design for multi-tenant SaaS? | Per-tenant encryption keys?
Tags: kms, multi-tenant, tenant isolation, encryption context, saas
Give each tenant a dedicated KMS key so no tenant's data is decryptable with another tenant's key.

• Per-tenant CMK, scoped key policy
• Bind tenant ID via encryption context
• Pair with Aurora row-level security
• Trade-off: per-key cost, quota limits

## A KMS key policy locked everyone out, including admins — what happened and how do you recover?
Aliases: KMS key policy locked out admins? | How to recover a locked KMS key? | Key policy vs IAM authority?
Tags: kms, key policy, iam, recovery, root
KMS access requires the key policy to grant it, so a policy with no valid statement locks everyone out.

• Root-delegation statement enables IAM access
• If present, fix via IAM
• If removed, only AWS Support recovers
• Prevent: keep the root statement

## Sensitive data may be sitting unencrypted across various S3 buckets — how do you find and fix it?
Aliases: How to find unencrypted S3 data? | Discover sensitive data in S3? | Fix S3 encryption drift?
Tags: macie, s3, config, sse-kms, block public access
Use Macie to discover sensitive data, Config to catch encryption drift, and enforce default encryption plus Block Public Access.

• Macie: ML-based PII discovery
• Config rule flags non-KMS buckets
• Enforce SSE-KMS via bucket policy
• Trade-off: Macie cost scales with volume

## A tenant offboards and wants cryptographic proof their data is unrecoverable — how does your design deliver that?
Aliases: How to prove tenant data is unrecoverable? | Crypto-shredding on offboarding? | Delete a tenant's KMS key?
Tags: kms, crypto-shredding, tenant offboarding, key deletion, multi-tenant
Delete that tenant's dedicated KMS key and their ciphertext becomes permanently undecryptable — crypto-shredding without touching every backup.

• Per-tenant key makes this clean
• Schedule key deletion, 7-30 days
• CloudTrail logs deletion as proof
• Trade-off: irreversible, no shared keys

## How do you handle KMS key rotation without breaking access to old data?
Aliases: Does key rotation break old data? | How does KMS auto-rotation work? | Rotate keys without losing decryption?
Tags: kms, key rotation, auto-rotation, encryption, envelope encryption
KMS auto-rotation creates new key material but keeps the old, so existing ciphertext still decrypts under the same key.

• Key ID/ARN stays constant
• KMS retains all old material
• New writes use new material
• Trade-off: old data not re-encrypted

## Secrets are hardcoded in a Terraform repo — how do you remediate and prevent recurrence?
Aliases: Hardcoded secrets in Terraform? | How to remediate leaked secrets in git? | Prevent secrets in repos?
Tags: secrets manager, terraform, secret scanning, ssm, ci/cd
Treat the exposed secrets as compromised and rotate them immediately — purging git history never undoes the leak.

• Rotate exposed credentials first
• Move to Secrets Manager/SSM
• Add CI secret scanning, push protection
• Trade-off: history rewrite disrupts collaborators

## How would you design secrets management for a fleet of microservices?
Aliases: Secrets management for microservices? | How to distribute secrets to services? | Rotation across many services?
Tags: secrets manager, microservices, rotation, iam, caching
Centralize in Secrets Manager with automatic rotation and per-service IAM scoping, fetching at runtime instead of baking into env.

• Rotation via Lambda per secret
• IAM scoped to secret ARNs
• Cache client-side to cut calls
• Trade-off: caching delays rotation propagation

## What's the risk of SSE-S3 versus SSE-KMS for a compliance-sensitive bucket?
Aliases: SSE-S3 vs SSE-KMS? | Which S3 encryption for compliance? | Does SSE-S3 give audit logs?
Tags: s3, sse-kms, sse-s3, cloudtrail, encryption
SSE-S3 encrypts but gives no key access control or decrypt audit; SSE-KMS adds both, which compliance needs.

• SSE-KMS: key policy gates decrypt
• SSE-KMS: CloudTrail logs every decrypt
• SSE-S3: encrypted but unaudited, free
• Trade-off: KMS cost; use Bucket Keys

## Data must be encrypted in transit internally, not just at the edge — how do you enforce that?
Aliases: How to enforce TLS everywhere internally? | Encryption in transit between services? | Require HTTPS on AWS?
Tags: tls, aws:securetransport, encryption in transit, mtls, security groups
Enforce TLS at the resource layer — deny any request where aws:SecureTransport is false, and require mTLS between services.

• Deny aws:SecureTransport false in policies
• Security groups allow only 443
• mTLS or service mesh internally
• Trade-off: cert management, TLS overhead
