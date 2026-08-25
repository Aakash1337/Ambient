# Network Security

## Walk me through how you'd design a secure multi-tier VPC.
Aliases: How do you architect a secure VPC? | Design a three-tier VPC | Secure VPC layout from scratch
Tags: vpc, subnets, cidr, defense-in-depth, segmentation
I start with non-overlapping CIDR planning, then segment into public, private, and data tiers across multiple AZs.

• Non-overlapping CIDRs, room to grow
• Public, app, data subnet tiers
• Multi-AZ for availability
• SG per tier, least privilege

## What actually makes a subnet public versus private?
Aliases: Difference between public and private subnets | When is a subnet public? | Public vs private subnet routing
Tags: subnets, route-table, igw, nat, segmentation
A subnet is public only if its route table has a default route to an internet gateway; nothing else changes it.

• Route to IGW = public
• Private subnet routes via NAT
• Public IP plus IGW route
• Data tier: no internet route

## Explain the difference between security groups and network ACLs.
Aliases: SG vs NACL | Stateful versus stateless firewalls | When NACL over security group
Tags: security-groups, nacl, stateful, stateless, ephemeral-ports
Security groups are stateful and instance-level with allow-only rules; NACLs are stateless and subnet-level with ordered allow and deny.

• SG stateful, return traffic auto
• NACL stateless, needs ephemeral ports
• SG allow-only; NACL allow plus deny
• NACL for explicit deny/blocklist

## How would you connect dozens of VPCs across many accounts?
Aliases: When to use Transit Gateway | TGW vs VPC peering | Hub-and-spoke networking on AWS
Tags: transit-gateway, vpc-peering, ram, routing, multi-account
I'd use Transit Gateway as a regional hub-and-spoke router instead of a mesh of non-transitive VPC peerings.

• Peering non-transitive, N-squared mesh
• TGW route tables segment traffic
• Share via RAM, cross-account
• Appliance mode for symmetric inspection

## When would you use a gateway endpoint versus an interface endpoint?
Aliases: Gateway vs interface VPC endpoint | Types of VPC endpoints | S3 endpoint choice
Tags: vpc-endpoints, privatelink, gateway-endpoint, interface-endpoint, s3
Gateway endpoints are free route-table entries for only S3 and DynamoDB; interface endpoints are PrivateLink ENIs for almost everything else.

• Gateway: S3, DynamoDB, free
• Interface: ENI, hourly plus data
• Interface backed by PrivateLink
• Endpoint policies restrict access

## What is AWS PrivateLink and when would you use it?
Aliases: Explain PrivateLink | Expose a service privately | PrivateLink vs peering
Tags: privatelink, endpoint-service, nlb, private-connectivity, saas
PrivateLink privately exposes a service through an interface endpoint so traffic never traverses the internet, IGW, or peering.

• Provider fronts NLB/GWLB
• Consumer creates interface endpoint
• No overlapping-CIDR concerns
• SaaS and cross-account private access

## How do resources in a private subnet reach the internet?
Aliases: What is a NAT gateway? | Egress for private subnets | NAT gateway vs egress-only IGW
Tags: nat-gateway, egress, private-subnet, ipv6, igw
A NAT gateway in a public subnet lets private instances make outbound IPv4 connections while blocking unsolicited inbound.

• NAT lives in public subnet
• Outbound only, no inbound
• IPv6 uses egress-only IGW
• Per-AZ for resilience

## When would you deploy AWS Network Firewall?
Aliases: What is AWS Network Firewall? | Layer 3-7 firewall on AWS | Network Firewall vs security groups
Tags: network-firewall, ips, suricata, domain-filtering, inspection
I'd deploy Network Firewall for managed stateful inspection, IPS, and domain filtering beyond what security groups and NACLs offer.

• Suricata-compatible stateful rules
• Domain/FQDN egress filtering
• Dedicated firewall subnet per AZ
• Centralized in inspection VPC

## How would you protect a public web application at layer 7?
Aliases: What is AWS WAF? | Protect an ALB or CloudFront | WAF managed rules and rate limiting
Tags: waf, web-acl, cloudfront, alb, rate-limiting
I'd attach a WAF web ACL to CloudFront, ALB, or API Gateway with managed rule groups and rate-based rules.

• Managed rules: OWASP, bad inputs
• Rate-based rules throttle floods
• Bot Control, IP reputation
• Attach to CloudFront/ALB/API-GW

## Explain Shield Standard versus Shield Advanced.
Aliases: AWS DDoS protection tiers | When to buy Shield Advanced | Shield cost protection
Tags: shield, ddos, shield-advanced, srt, cost-protection
Shield Standard is free automatic L3/L4 protection for everyone; Advanced adds L7 defenses, the response team, and cost protection.

• Standard: free, L3/L4, automatic
• Advanced: ~$3k/month, one-year commit
• Shield Response Team, cost protection
• Includes WAF on protected resources

## How do you secure DNS in AWS, and what does DNSSEC add?
Aliases: Route 53 DNSSEC | Prevent DNS spoofing | DNS security on AWS
Tags: route53, dnssec, kms, spoofing, cache-poisoning
I'd enable DNSSEC signing on Route 53 public hosted zones to prevent spoofing and cache poisoning via a cryptographic chain of trust.

• DNSSEC signs authoritative responses
• KMS asymmetric key, ECC_NIST_P256
• Stops cache poisoning/spoofing
• Resolver can validate DNSSEC

## What is Route 53 Resolver DNS Firewall and when would you use it?
Aliases: DNS Firewall | Block DNS exfiltration | Filter outbound DNS queries
Tags: dns-firewall, resolver, exfiltration, domain-lists, guardduty
Resolver DNS Firewall filters outbound DNS queries from a VPC, blocking known-malicious domains and DNS-based exfiltration.

• Domain allow/block lists
• AWS-managed malware/botnet lists
• Stops DNS tunneling/exfiltration
• Pairs with GuardDuty findings

## How would you implement centralized egress filtering?
Aliases: Restrict outbound traffic | Centralized egress architecture | Control what leaves the VPC
Tags: egress, network-firewall, dns-firewall, transit-gateway, endpoints
I'd route all outbound traffic through a central egress VPC with Network Firewall for domain filtering, fronted by Transit Gateway.

• Central egress VPC via TGW
• Network Firewall FQDN allowlists
• DNS Firewall blocks bad domains
• VPC endpoints keep AWS private

## What do VPC Flow Logs capture, and what are their limits?
Aliases: VPC Flow Logs explained | Do flow logs capture packets? | Flow log destinations
Tags: flow-logs, cloudwatch, s3, metadata, monitoring
Flow Logs capture connection metadata, the 5-tuple, action, and byte counts, but never packet payloads.

• Metadata only, no payload
• 5-tuple, ACCEPT/REJECT, bytes
• Publish to CloudWatch/S3/Firehose
• Misses DNS, instance-metadata, DHCP

## How would you build a centralized traffic inspection architecture?
Aliases: Inspection VPC design | Gateway Load Balancer inspection | East-west traffic inspection
Tags: inspection-vpc, gateway-load-balancer, transit-gateway, appliance-mode, network-firewall
I'd centralize inspection in a dedicated VPC using Gateway Load Balancer or Network Firewall, with Transit Gateway appliance mode for flow symmetry.

• Central inspection VPC
• GWLB fronts security appliances
• TGW appliance mode = symmetry
• Inspect east-west and egress

## How would you apply Zero Trust to AWS networking?
Aliases: Zero Trust network design | Microsegmentation on AWS | Replace VPN with identity-aware access
Tags: zero-trust, security-groups, verified-access, vpc-lattice, microsegmentation
Having replaced VPN with Cloudflare Zero Trust, I'd apply the same on AWS: identity-aware access and no implicit network trust.

• SG referencing for microsegmentation
• Verified Access replaces VPN
• VPC Lattice, IAM service-to-service
• PrivateLink over public exposure
