# AWS Security Service Glossary

## What is IAM?
Aliases: iam | what does iam do | Identity and Access Management
Tags: iam, identity, access-control
IAM is AWS's identity service that manages users, groups, roles, and policies to control who can access which resources.

• Roles grant temporary credentials
• Default-deny; explicit deny overrides
• Global service, not region-scoped

## What is STS?
Aliases: sts | what does sts do | Security Token Service
Tags: sts, identity, temporary-credentials
STS is AWS's service that issues short-lived, temporary credentials for assuming roles and federating access.

• Powers AssumeRole and federation
• Credentials expire automatically
• Backbone of cross-account access

## What is IAM Identity Center?
Aliases: iam identity center | what does iam identity center do | AWS SSO
Tags: iam identity center, sso, federation
IAM Identity Center is AWS's workforce single sign-on that centrally manages human access across accounts and applications.

• Successor to AWS SSO
• Connects to external identity providers
• Assigns permission sets per account

## What is AWS Organizations?
Aliases: aws organizations | what does aws organizations do | Organizations
Tags: aws organizations, multi-account, governance
AWS Organizations is AWS's service for centrally managing and governing multiple AWS accounts as a single organization.

• Groups accounts into OUs
• Enables consolidated billing
• Foundation for SCPs

## What is SCP (Service Control Policy)?
Aliases: scp | what does scp do | Service Control Policy
Tags: scp, guardrail, organizations
SCP is an Organizations guardrail that sets the maximum permissions accounts can have but never grants any access itself.

• Restricts, never grants
• Applies to OUs or accounts
• Does not affect management account

## What is AWS Control Tower?
Aliases: aws control tower | what does control tower do | Control Tower
Tags: control tower, landing-zone, governance
AWS Control Tower is AWS's service that sets up and governs a secure, multi-account landing zone using best-practice guardrails.

• Automates landing-zone setup
• Guardrails via SCPs and Config
• Built on top of Organizations

## What is KMS?
Aliases: kms | what does kms do | Key Management Service
Tags: kms, encryption, keys
KMS is AWS's managed key service that creates and controls encryption keys, where the key policy is the root of access.

• Key policy governs key access
• IAM alone cannot grant use
• Integrates with most AWS services

## What is AWS Secrets Manager?
Aliases: aws secrets manager | what does secrets manager do | Secrets Manager
Tags: secrets manager, secrets, rotation
Secrets Manager is AWS's service that stores, retrieves, and automatically rotates secrets like database credentials and API keys.

• Built-in automatic rotation
• Charged per secret
• Encrypts secrets with KMS

## What is SSM Parameter Store?
Aliases: ssm parameter store | what does parameter store do | Parameter Store
Tags: parameter store, config, secrets
Parameter Store is AWS's service that stores configuration data and secrets as parameters, with optional KMS-encrypted SecureString values.

• Stores config; no native rotation
• Free standard tier
• SecureString uses KMS

## What is ACM (Certificate Manager)?
Aliases: acm | what does acm do | Certificate Manager
Tags: acm, tls, certificates
ACM is AWS's service that provisions, manages, and auto-renews SSL/TLS certificates for integrated AWS services.

• Free public certificates
• Auto-renews managed certs
• Integrates with ELB, CloudFront

## What is Amazon Macie?
Aliases: amazon macie | what does macie do | Macie
Tags: macie, data-classification, s3
Macie is AWS's service that uses machine learning to discover and classify sensitive data like PII in S3 buckets.

• Scans S3 for sensitive data
• Detects PII and credentials
• Reports data-security posture

## What is Amazon GuardDuty?
Aliases: amazon guardduty | what does guardduty do | GuardDuty
Tags: guardduty, threat-detection, monitoring
GuardDuty is AWS's threat detection service that continuously analyzes logs to identify malicious activity and compromised resources.

• Analyzes CloudTrail, VPC, DNS logs
• No agents to deploy
• Detection, not remediation

## What is AWS Security Hub?
Aliases: aws security hub | what does security hub do | Security Hub
Tags: security hub, cspm, posture
Security Hub is AWS's service that aggregates security findings and runs posture checks against standards for centralized cloud security management.

• Aggregates findings across services
• Runs CSPM compliance checks
• Central single pane of glass

## What is AWS CloudTrail?
Aliases: aws cloudtrail | what does cloudtrail do | CloudTrail
Tags: cloudtrail, audit, api-logging
CloudTrail is AWS's service that records account activity and API calls for audit, governance, and compliance.

• Logs who did what
• Management and data events
• Foundation for forensic audit

## What is AWS Config?
Aliases: aws config | what does config do | Config
Tags: config, compliance, configuration
AWS Config is AWS's service that records resource configurations over time and evaluates them against compliance rules.

• Tracks configuration changes
• Rules flag non-compliance
• Enables configuration history

## What is Amazon Detective?
Aliases: amazon detective | what does detective do | Detective
Tags: detective, investigation, forensics
Detective is AWS's service that analyzes and visualizes log data to investigate the root cause of security findings.

• Investigates, doesn't detect
• Builds behavior graphs
• Deep-dives GuardDuty findings

## What is Amazon Security Lake?
Aliases: amazon security lake | what does security lake do | Security Lake
Tags: security lake, data-lake, ocsf
Security Lake is AWS's service that centralizes security data from AWS and third parties into a normalized OCSF data lake.

• Normalizes to OCSF format
• Stores in your S3
• Query with your own tools

## What is Amazon Inspector?
Aliases: amazon inspector | what does inspector do | Inspector
Tags: inspector, vulnerability-scanning, cve
Inspector is AWS's service that continuously scans workloads for software vulnerabilities and unintended network exposure.

• Scans EC2, ECR, Lambda
• Finds CVEs automatically
• Prioritizes by risk score

## What is AWS WAF?
Aliases: aws waf | what does waf do | Web Application Firewall
Tags: waf, web-security, layer7
WAF is AWS's web application firewall that filters HTTP/HTTPS traffic to protect apps from common exploits like SQL injection.

• Rules block layer-7 attacks
• Protects CloudFront, ALB, API Gateway
• Managed rule groups available

## What is AWS Shield and Shield Advanced?
Aliases: aws shield | what does shield do | Shield Advanced
Tags: shield, ddos, protection
Shield is AWS's DDoS protection, where Standard is automatic and free while Advanced is paid with enhanced defense and support.

• Standard: automatic, free
• Advanced: paid, DDoS response team
• Advanced adds cost protection

## What is AWS Network Firewall?
Aliases: aws network firewall | what does network firewall do | Network Firewall
Tags: network firewall, vpc, network-security
Network Firewall is AWS's managed stateful firewall that filters and inspects traffic at the VPC network boundary.

• Stateful and stateless rules
• Protects entire VPC
• Supports intrusion prevention

## What is AWS PrivateLink?
Aliases: aws privatelink | what does privatelink do | PrivateLink
Tags: privatelink, private-connectivity, endpoints
PrivateLink is AWS's technology that provides private connectivity to services over interface endpoints without traversing the public internet.

• Powers interface VPC endpoints
• Traffic stays on AWS network
• Exposes services privately

## What is AWS Transit Gateway?
Aliases: aws transit gateway | what does transit gateway do | Transit Gateway
Tags: transit gateway, networking, connectivity
Transit Gateway is AWS's hub that connects multiple VPCs and on-premises networks through a single central routing point.

• Hub-and-spoke networking
• Simplifies VPC peering mesh
• Connects VPN and Direct Connect

## What is VPC endpoints (gateway vs interface)?
Aliases: vpc endpoints | what do vpc endpoints do | gateway vs interface endpoint
Tags: vpc endpoints, private-connectivity, networking
VPC endpoints privately connect a VPC to AWS services, where gateway endpoints use route tables and interface endpoints use ENIs.

• Gateway: S3 and DynamoDB only
• Gateway via route table, free
• Interface: ENI, PrivateLink, hourly cost

## What is IAM Access Analyzer?
Aliases: iam access analyzer | what does access analyzer do | Access Analyzer
Tags: access analyzer, least-privilege, identity
IAM Access Analyzer is AWS's service that identifies resources shared externally and validates policies against least-privilege best practices.

• Flags external, unintended access
• Validates and generates policies
• Uses provable reasoning

## What is AWS Audit Manager?
Aliases: aws audit manager | what does audit manager do | Audit Manager
Tags: audit manager, compliance, audit
Audit Manager is AWS's service that continuously collects evidence and maps it to frameworks to simplify compliance audits.

• Automates evidence collection
• Prebuilt compliance frameworks
• Produces audit-ready reports

## What is Amazon Cognito?
Aliases: amazon cognito | what does cognito do | Cognito
Tags: cognito, authentication, ciam
Cognito is AWS's service that adds sign-up, sign-in, and access control for customer-facing web and mobile applications.

• User pools authenticate users
• Identity pools grant AWS access
• Supports social and SAML login

## What is Amazon CloudWatch?
Aliases: amazon cloudwatch | what does cloudwatch do | CloudWatch
Tags: cloudwatch, monitoring, observability
CloudWatch is AWS's monitoring service that collects metrics, logs, and alarms to observe resources and application health.

• Metrics, logs, and alarms
• Triggers automated actions
• Dashboards for observability

## What is Amazon EventBridge?
Aliases: amazon eventbridge | what does eventbridge do | EventBridge
Tags: eventbridge, event-bus, automation
EventBridge is AWS's serverless event bus that routes events between AWS services, SaaS apps, and custom targets by rules.

• Rules route events to targets
• Connects SaaS and AWS events
• Enables event-driven automation
