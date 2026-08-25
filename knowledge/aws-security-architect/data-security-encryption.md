# Data Security & Encryption

## Explain how envelope encryption works in KMS.
Aliases: How does KMS encrypt data | What is a data key | Why not encrypt with the KMS key directly
Tags: kms, envelope-encryption, data-key, generatedatakey
KMS encrypts a data key, that data key encrypts your data — so KMS never touches the plaintext bulk data.

• GenerateDataKey returns plaintext + ciphertext
• 4 KB direct encrypt limit
• decrypt data key, then data
• store encrypted data key alongside

## When would you use a customer-managed key versus an AWS-managed key?
Aliases: CMK vs AWS managed key | difference between KMS key types | why customer-managed keys
Tags: kms, cmk, aws-managed-keys, key-policy
Customer-managed keys give you control over the key policy, rotation cadence, and grants; AWS-managed keys are convenient but opaque.

• customer-managed: custom key policy
• AWS-managed: auto yearly rotation, free
• customer-managed needed for cross-account
• per-tenant isolation needs customer-managed keys

## How does access control actually work in KMS — key policies, grants, and IAM?
Aliases: key policy vs IAM policy | what are KMS grants | who can use a KMS key
Tags: kms, key-policy, grants, iam
The key policy is the root of trust; IAM policies only work if the key policy delegates to the account.

• grants: temporary, granular delegation
• grants for AWS services
• "Enable IAM" statement delegates
• RevokeGrant for immediate removal

## How does KMS key rotation work, and what does it not do?
Aliases: does rotation re-encrypt data | key rotation cadence | rotate imported key material
Tags: kms, key-rotation, backing-key
Automatic rotation generates new backing key material yearly by default, but keeps old material to decrypt existing ciphertext.

• does NOT re-encrypt data
• same key ID/ARN preserved
• configurable 90–2560 days
• imported material: manual rotation only

## When would you use KMS multi-Region keys?
Aliases: what are multi-region keys | cross-region encryption with KMS | DR with KMS
Tags: kms, multi-region-keys, disaster-recovery, replication
Multi-Region keys share the same key material and key ID across Regions, so ciphertext encrypted in one Region decrypts in another.

• primary + replica keys
• DynamoDB global tables, DR
• independent key policies per Region
• not default; regional keys preferred

## How do you grant another account access to your data and prevent the confused deputy problem?
Aliases: what is ExternalId | cross-account role trust | confused deputy mitigation
Tags: sts, externalid, cross-account, confused-deputy, iam
For third-party cross-account roles I'd require an ExternalId in the trust policy so a vendor can't be tricked into using my role.

• ExternalId: sts:AssumeRole condition
• KMS: key policy grants principal
• aws:SourceArn / SourceAccount conditions
• least-privilege scoped role

## Walk me through encryption at rest versus in transit on AWS.
Aliases: at rest vs in transit | how do you encrypt data end to end | TLS vs KMS
Tags: encryption-at-rest, encryption-in-transit, tls, kms
At rest is KMS-backed server-side encryption on the storage layer; in transit is TLS on every hop between services.

• at rest: S3, EBS, RDS, Aurora
• in transit: TLS 1.2/1.3
• enforce aws:SecureTransport in policy
• ATTEST: both, per-tenant keys

## How do you manage TLS certificates on AWS with ACM?
Aliases: what is ACM | public vs private certs | certificate renewal on AWS
Tags: acm, tls, certificates, private-ca
ACM issues and auto-renews free public certificates that integrate directly with ELB, CloudFront, and API Gateway.

• integrated free certs: not exportable
• exportable public certs since 2025
• AWS Private CA for internal
• DNS validation enables auto-renewal

## When would you choose Secrets Manager over SSM Parameter Store?
Aliases: Secrets Manager vs Parameter Store | where to store secrets | secret rotation on AWS
Tags: secrets-manager, parameter-store, ssm, rotation
Secrets Manager when I need automatic rotation, cross-account, or cross-Region replication; Parameter Store SecureString for simple, free config secrets.

• Secrets Manager: built-in Lambda rotation
• Parameter Store standard: free
• ATTEST: Secrets Manager centralized creds
• both KMS-encrypted at rest

## Explain the S3 server-side encryption options and when to use each.
Aliases: SSE-S3 vs SSE-KMS vs DSSE | S3 encryption types | what is DSSE-KMS
Tags: s3, sse-s3, sse-kms, dsse-kms, bucket-keys
SSE-S3 is AES-256 with S3-managed keys; SSE-KMS adds auditable KMS control; DSSE-KMS is dual-layer for high-compliance workloads.

• SSE-S3 default since 2023
• S3 Bucket Keys cut KMS costs
• SSE-C: customer-provided keys
• SSE-KMS: CloudTrail key auditing

## How do you prevent an S3 bucket from being publicly exposed?
Aliases: S3 Block Public Access | stop public S3 buckets | enforce TLS on S3
Tags: s3, block-public-access, bucket-policy, securetransport
Block Public Access at the account level is my backstop; then least-privilege bucket policies and enforced TLS on top.

• 4 BPA settings, on by default
• deny aws:SecureTransport false
• deny unencrypted PutObject
• account BPA overrides bucket-level

## How would you discover and classify sensitive data across your S3 estate?
Aliases: what is Macie | find PII in S3 | data classification on AWS
Tags: macie, data-classification, pii, s3
Macie uses managed data identifiers and machine learning to discover and classify PII across S3 automatically.

• managed + custom identifiers
• automated sensitive data discovery
• findings to Security Hub / EventBridge
• delegated admin, org-wide

## How would you isolate tenant data cryptographically in a multi-tenant SaaS platform?
Aliases: per-tenant encryption keys | multi-tenant data isolation | crypto-shredding
Tags: kms, per-tenant-keys, multi-tenancy, crypto-shredding
On ATTEST I gave each tenant its own KMS key, so isolation is enforced cryptographically, not just by application logic.

• per-tenant customer-managed key + policy
• delete key = crypto-shredding
• paired with Aurora row-level security
• watch KMS quotas, costs

## What are your options when a customer demands to control their own key material — BYOK?
Aliases: what is BYOK | import key material into KMS | external key store XKS
Tags: kms, byok, imported-key-material, xks, cloudhsm
BYOK means importing your own key material into KMS; for full external control I'd use the KMS External Key Store.

• imported material: no auto-rotation
• XKS: keys in external HSM
• CloudHSM-backed custom key store
• you bear availability responsibility
