# Scenario Drills — Network Security

## A workload in VPC A needs to reach a service in another account's VPC — what are my options and their trade-offs?
Aliases: connect two VPCs across accounts | VPC A to VPC B different account | cross-account VPC connectivity options
Tags: vpc peering, transit gateway, privatelink, cross-account
Three options — VPC peering, Transit Gateway, or PrivateLink — chosen by scale, CIDR overlap, and how much you want exposed.

• Peering: simple, non-transitive, no overlap
• TGW: hub-and-spoke, scales, transitive
• PrivateLink: one service, overlapping CIDRs OK
• Trade-off: cost vs scale vs exposure

## You've got 15 VPCs meshed with peering and it's unmanageable — what now?
Aliases: 15 VPCs peered is unmanageable | peering mesh doesn't scale | replace VPC peering mesh
Tags: transit gateway, vpc peering, hub-and-spoke, routing, scale
Collapse the peering mesh onto a Transit Gateway hub-and-spoke, replacing N-squared connections with centralized, managed routing.

• Attach each VPC to TGW
• Route tables segment traffic domains
• One managed hub, not mesh
• Trade-off: TGW attachment and data cost

## How do you let an EC2 instance reach S3 without traffic ever touching the internet?
Aliases: EC2 to S3 without internet | private S3 access from VPC | reach S3 no NAT gateway
Tags: gateway endpoint, s3, vpc endpoint, endpoint policy
Use a gateway VPC endpoint for S3 so traffic stays on the AWS backbone — no IGW or NAT.

• Add endpoint route to table
• Endpoint policy restricts allowed buckets
• Bucket policy can require endpoint
• Trade-off: gateway only S3/DynamoDB

## How would you design network inspection for east-west traffic between multiple VPCs?
Aliases: inspect east-west traffic between VPCs | central inspection VPC design | firewall inter-VPC traffic
Tags: network firewall, transit gateway, inspection vpc, east-west, ids/ips
Route all inter-VPC traffic through a central inspection VPC running AWS Network Firewall, steered by Transit Gateway route tables.

• TGW appliance-mode keeps flow symmetry
• Network Firewall does IDS/IPS
• Stateful rules, domain filtering, logging
• Trade-off: added latency and cost

## Your public API is getting SQL injection attempts and credential stuffing — what layers do you add?
Aliases: public API SQLi and credential stuffing | protect API from injection and bots | layered API attack defense
Tags: waf, bot control, sqli, credential stuffing, rate limiting
Front the API with WAF managed rules for SQLi, rate-based and bot-control rules for stuffing, and harden authentication.

• WAF managed rules block SQLi
• Rate-based and Bot Control throttle stuffing
• MFA, lockout, ATP detection
• Trade-off: tune to avoid false positives

## What's the difference between a security group and a NACL, and when do you specifically need a NACL?
Aliases: security group vs NACL | when do you need a NACL | difference SG and network ACL
Tags: security group, nacl, stateful, stateless, subnet
Security groups are stateful and allow-only per ENI; NACLs are stateless subnet-level filters that can also explicitly deny.

• NACL blocks a malicious CIDR
• Deny applies subnet-wide, coarse
• Stateless: allow both directions explicitly
• Trade-off: NACLs harder to manage

## A database in a supposedly private subnet is reachable from the internet — how do you investigate?
Aliases: private subnet database reachable from internet | why is my private DB exposed | investigate unexpected internet exposure
Tags: route table, internet gateway, security group, public ip, subnet
The subnet isn't truly private — trace the exposure path: route table, internet gateway, public IP, then security group rules.

• Check route table for IGW
• Check instance public IP assignment
• Audit security group ingress rules
• Remediate, then confirm with flow logs

## When would you choose PrivateLink over VPC peering?
Aliases: PrivateLink vs VPC peering | when to use PrivateLink | one-way service exposure without peering
Tags: privatelink, vpc peering, overlapping cidr, saas, endpoint service
Choose PrivateLink when you expose a single service one-way, have overlapping CIDRs, or don't want to merge the networks.

• Consumer reaches provider, not reverse
• Works with overlapping CIDR ranges
• Ideal SaaS/multi-tenant exposure model
• Trade-off: per-service, not broad connectivity

## How would you design DNS security for an enterprise?
Aliases: design DNS security for enterprise | secure enterprise DNS | protect against DNS tunneling and exfil
Tags: route 53, dns firewall, dnssec, guardduty, dns tunneling
Layer Route 53 Resolver DNS Firewall to block known-bad domains, enable DNSSEC validation, and monitor for tunneling and exfiltration.

• DNS Firewall domain allow/deny lists
• DNSSEC signing and validation
• GuardDuty flags DNS exfiltration
• Trade-off: managed lists need tuning

## What's the difference between Shield Standard and Advanced, and when do you pay for Advanced?
Aliases: Shield Standard vs Advanced | when to pay for Shield Advanced | is Shield Advanced worth it
Tags: shield, ddos, shield advanced, srt, cost protection
Shield Standard is free L3/L4 protection; pay for Advanced on business-critical apps needing L7 defense, cost protection, and SRT access.

• Advanced adds L7 attack mitigation
• DDoS cost-protection credits scaling charges
• 24/7 Shield Response Team
• Trade-off: ~$3k/month commitment

## How do you architect ingress for an app that must be internet-facing but whose backend must never be?
Aliases: internet-facing app private backend | expose frontend keep backend private | secure ingress architecture
Tags: alb, cloudfront, security group chaining, private subnet, ingress
Put a public ALB in public subnets, keep backends in private subnets, and chain security groups so only the ALB reaches them.

• Backend SG allows only ALB SG
• No public IP on backends
• CloudFront and WAF at edge
• Trade-off: NAT for backend egress

## VPC flow logs show a prod instance talking to an unexpected external IP — walk me through triage.
Aliases: flow logs unexpected external IP | prod instance beaconing triage | investigate suspicious outbound traffic
Tags: vpc flow logs, guardduty, incident response, isolation, forensics
Treat it as possible compromise: correlate with GuardDuty, identify the process and destination, then isolate the instance for forensics.

• Check GuardDuty findings and threat intel
• Identify process, owner, credentials
• Isolate with quarantine security group
• Trade-off: preserve evidence before terminating

## You have hundreds of interface endpoints and the cost is high — how do you optimize without losing the private path?
Aliases: too many interface endpoints costing money | optimize VPC endpoint cost | centralize interface endpoints
Tags: interface endpoint, privatelink, route 53, private hosted zone, gateway endpoint
Centralize interface endpoints in a shared VPC, resolve them cross-VPC via Route 53 private hosted zones, and use free gateway endpoints where possible.

• Shared endpoints reached via TGW/peering
• Private hosted zones override endpoint DNS
• Gateway endpoints free for S3/DynamoDB
• Trade-off: central VPC is dependency
