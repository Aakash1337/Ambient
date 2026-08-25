# AWS Security Service Glossary — Extended

## What is AWS CloudHSM?
Aliases: CloudHSM | dedicated HSM | hardware security module
Tags: cloudhsm, hsm, keys, fips, single-tenant
AWS CloudHSM gives you single-tenant, dedicated hardware security modules for generating and managing your own cryptographic keys.

• Single-tenant, dedicated HSMs
• FIPS 140-2 Level 3
• You control keys fully
• AWS cannot access keys

## What is AWS Directory Service?
Aliases: Directory Service | managed AD | managed Active Directory
Tags: directory, active-directory, ldap, domain, authentication
AWS Directory Service offers managed directories in the cloud, including Microsoft Active Directory, for authentication and domain joins.

• Managed Microsoft AD
• Domain-join EC2 instances
• Integrates on-prem AD

## What is AWS Firewall Manager?
Aliases: Firewall Manager | FMS | central firewall policy
Tags: firewall-manager, waf, shield, network-firewall, org
AWS Firewall Manager centrally configures and enforces firewall rules across all accounts and resources in your AWS Organization.

• Manages WAF, Shield Advanced, Network Firewall
• Security groups, DNS Firewall
• Requires AWS Organizations

## What is AWS RAM (Resource Access Manager)?
Aliases: RAM | Resource Access Manager | resource sharing
Tags: ram, sharing, subnets, transit-gateway, cross-account
AWS Resource Access Manager securely shares your AWS resources like subnets, Transit Gateways, and Private CA across accounts.

• Share subnets, TGW, Private CA
• Across accounts or Organization
• No resource duplication

## What is AWS Verified Access?
Aliases: Verified Access | zero-trust access | VPN-less access
Tags: verified-access, ztna, vpn-less, identity, zero-trust
AWS Verified Access provides secure, VPN-less access to corporate applications based on identity and device context.

• No VPN needed
• Identity and device policies
• Per-request access evaluation

## What is Amazon Verified Permissions?
Aliases: Verified Permissions | Cedar authorization | fine-grained permissions
Tags: verified-permissions, cedar, authorization, fine-grained, policy
Amazon Verified Permissions is a managed authorization service using the Cedar policy language for fine-grained application permissions.

• Uses Cedar policy language
• Fine-grained app authorization
• Externalizes permissions from code

## What is Network Access Analyzer?
Aliases: Network Access Analyzer | reachability analysis | path analysis
Tags: network-access-analyzer, reachability, vpc, segmentation, paths
Network Access Analyzer identifies unintended network access paths to your resources by analyzing reachability across your VPC configurations.

• Finds unintended network paths
• Reachability analysis
• Verifies network segmentation

## What is AWS Private CA (Private Certificate Authority)?
Aliases: Private CA | ACM Private CA | private PKI
Tags: private-ca, pki, certificates, x509, tls
AWS Private CA is a managed private certificate authority for issuing and managing X.509 certificates within your organization.

• Issues private X.509 certs
• Build your own PKI
• Shareable via RAM

## What is AWS Trusted Advisor?
Aliases: Trusted Advisor | best-practice checks | account advisor
Tags: trusted-advisor, best-practices, security, checks, recommendations
AWS Trusted Advisor inspects your environment and recommends best practices across cost, performance, security, fault tolerance, and limits.

• Best-practice checks
• Security recommendations included
• Full checks need Business support

## What is AWS Systems Manager?
Aliases: Systems Manager | SSM | ops management
Tags: systems-manager, ssm, patching, session-manager, automation
AWS Systems Manager provides unified operational management for your resources, including patching, automation, and secure shell-less instance access.

• Patch Manager, Automation
• Session Manager, no SSH
• Parameter Store for config

## What is AWS Signer?
Aliases: Signer | code signing | AWS code signing
Tags: signer, code-signing, integrity, lambda, iot
AWS Signer is a fully managed code-signing service that ensures the trust and integrity of your deployed code.

• Managed code signing
• Lambda, IoT, containers
• Verifies code integrity

## What is AWS Elastic Disaster Recovery?
Aliases: Elastic Disaster Recovery | DRS | AWS DRS
Tags: drs, disaster-recovery, replication, failover, resilience
AWS Elastic Disaster Recovery continuously replicates your servers into AWS for fast, low-cost recovery during outages or disasters.

• Continuous block-level replication
• Low-cost staging area
• Fast failover and failback

## What is AWS Service Catalog?
Aliases: Service Catalog | product catalog | approved products
Tags: service-catalog, governance, self-service, cloudformation, products
AWS Service Catalog lets organizations create and manage curated catalogs of approved IT products for self-service deployment.

• Curated approved products
• Governance and standardization
• Backed by CloudFormation
