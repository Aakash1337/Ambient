# Scenario Drills — Incident Response

## An IAM access key is confirmed compromised and actively being used — walk me through your full runbook.
Aliases: IAM access key compromised runbook | leaked access key incident response | active key compromise full runbook
Tags: iam, cloudtrail, access-key, sts, incident-response
Treat as active intrusion: deactivate the key now, revoke any temporary sessions, then scope blast radius before eradicating.

• Deactivate key, revoke temp sessions
• Scope blast radius via CloudTrail
• Hunt persistence, eradicate, recover
• Trade-off: contain fast, preserve evidence

## An EC2 instance is compromised. Why might you not terminate it right away?
Aliases: why not terminate compromised EC2 | reasons to keep compromised instance | preserve compromised EC2 for forensics
Tags: ec2, forensics, ebs-snapshot, incident-response
Terminating destroys forensic evidence — isolate and snapshot first, so you can prove scope and root cause.

• Termination wipes memory, disks
• Isolate, snapshot EBS first
• Preserve for attribution, legal
• Trade-off: evidence preservation delays cleanup

## How do you isolate a compromised instance while preserving it for investigation?
Aliases: isolate compromised instance for investigation | quarantine EC2 preserve forensics | contain instance without destroying evidence
Tags: ec2, security-group, nacl, ebs-snapshot, forensics
Attach an empty (no-rules) security group, capture volatile memory live, then snapshot each EBS volume.

• Empty SG: no inbound/outbound
• Capture live memory, snapshot EBS
• NACL deny for hard cutoff
• Trade-off: SG stateful vs NACL stateless

## How would you design automated response for common GuardDuty findings?
Aliases: automated GuardDuty response | auto-remediate GuardDuty findings | EventBridge Lambda for GuardDuty
Tags: guardduty, eventbridge, lambda, sns, security-hub, automation
Route GuardDuty findings through EventBridge to Lambda that auto-isolates, disables keys, and pages responders by severity.

• EventBridge rule matches finding type
• Lambda: isolate SG, disable key
• Notify via SNS and Security Hub
• Trade-off: automation risks false-positive outage

## The compromised credentials belong to a service prod depends on — how do you contain it without causing an outage?
Aliases: contain compromised service credentials no outage | prod-critical credential compromise | avoid outage while containing creds
Tags: iam, explicit-deny, failover, rotation, incident-response
Don't blanket-disable — apply a scoped explicit deny to the abused actions, stage a failover identity, then rotate cleanly.

• Scoped explicit deny wins
• Stand up failover identity/role
• Rotate credentials in stages
• Trade-off: partial containment vs uptime

## Post-incident, leadership asks "are we sure they're gone?" — how do you answer credibly?
Aliases: are we sure attacker is gone | prove eradication after incident | confirm no persistence remains
Tags: persistence, eradication, iam, lambda, monitoring
Only after a persistence hunt across every foothold plus sustained clean monitoring — never a single clean scan.

• Hunt new users, keys, roles
• Check Lambda backdoors, scheduled tasks
• Verify with sustained clean monitoring
• Trade-off: certainty is probabilistic, not absolute

## You had a DDoS attack — walk me through detection, mitigation, and what you hardened afterward.
Aliases: walk me through a DDoS response | DDoS detection mitigation hardening | how did you handle a DDoS
Tags: ddos, shield, waf, cloudfront, route53
Detect at the edge via CloudWatch and Shield metrics, absorb with Shield and WAF, then harden capacity and rate limits.

• Shield Advanced, WAF rate limits
• Scale via CloudFront, Auto Scaling
• Engage Shield Response Team (SRT)
• Trade-off: Shield Advanced cost vs protection

## An executive-impersonation phishing campaign hit the company — what's your response beyond the technical?
Aliases: executive impersonation phishing response | CEO impersonation phishing beyond technical | whaling attack non-technical response
Tags: phishing, bec, incident-response, comms, awareness
Treat executive-impersonation phishing as comms plus tech: contain, notify targets, brief leadership, and close the gaps.

• Contain sender, pull malicious mail
• Direct comms to targeted users
• Awareness training, verification workflows
• Trade-off: speed vs measured messaging

## How do you run a blameless post-mortem that actually changes anything?
Aliases: run a blameless post-mortem | post-mortem that drives change | effective incident retrospective
Tags: post-mortem, blameless, root-cause, action-items
A post-mortem only changes things when it produces owned, dated action items with follow-through — not just a blame-free timeline.

• Build factual timeline, no blame
• Identify systemic root cause
• Action items: owners, dates, tracking
• Trade-off: candor needs psychological safety

## What's your first hour when you're paged for "something's wrong in prod, might be a breach"?
Aliases: first hour of a suspected breach | something's wrong in prod possible breach | initial breach triage steps
Tags: incident-response, triage, cloudtrail, guardduty, escalation
First hour is triage: confirm it's real, scope the blast radius, contain, escalate, and document every step as I go.

• Triage: real incident or noise
• Scope via CloudTrail, GuardDuty
• Contain, escalate, declare incident
• Trade-off: act fast, avoid tunnel-vision
