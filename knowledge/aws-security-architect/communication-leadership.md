# Communication & Architecture Leadership

## How would you explain a critical security risk to a non-technical executive?
Aliases: explain risk to business stakeholders | brief an executive on risk | communicating risk to leadership
Tags: risk communication, stakeholders, business impact, prioritization
I lead with business impact and likelihood, not the CVE — what could be lost, how likely, and what it costs to fix.

• Impact in dollars, not jargon
• Likelihood plus exploitability, plainly
• Options: accept, mitigate, remediate
• CYBIC exec-impersonation phishing brief

## How do you quantify and prioritize risk when everything is flagged critical?
Aliases: prioritize security findings | quantify risk | risk scoring methodology
Tags: risk quantification, prioritization, cvss, threat modeling, hipaa
I score by real exposure, not raw CVSS — likelihood times business impact, weighted by reachability and data sensitivity.

• CVSS as input, not verdict
• Reachability: internet-facing vs internal
• Data sensitivity: HIPAA/PII blast radius
• Rank by exploitability plus impact

## Explain how you would frame a security-versus-velocity trade-off to an engineering team.
Aliases: security vs speed | balancing security and delivery | trade-offs with engineering
Tags: trade-offs, devsecops, velocity, ci/cd, oidc
I frame security as guardrails that let teams ship faster safely, not gates that stop them.

• Shift-left: automated scans in CI
• Strangler-fig migration, phased canary rollouts
• OIDC keyless auth, no secrets
• Paved road beats manual review

## How do you use architecture diagrams and threat models to communicate risk?
Aliases: threat model as communication | architecture diagrams for stakeholders | data flow diagrams
Tags: threat modeling, architecture diagrams, stride, trust boundaries
A diagram with trust boundaries makes risk visible — I use it as the shared language between security, engineering, and the business.

• Data flow diagram, trust boundaries
• STRIDE per boundary crossing
• Highlight blast radius visually
• CYBIC: threat models all products

## How do you influence security decisions when you have no direct authority?
Aliases: influence without authority | driving change without ownership | getting buy-in for security
Tags: influence, stakeholder buy-in, leadership, remediation
I influence through evidence and empathy — I demonstrate the risk concretely, then make the secure path the easy path.

• Proof-of-concept exploit, not theory
• Align to team's own goals
• Provide the fix, not homework
• CYBIC: sole owner, cross-team

## What makes a security team an enabler instead of a blocker?
Aliases: security as enabler not blocker | being a business enabler | avoiding the department of no
Tags: security enabler, paved road, secure-by-design, scp, culture
An enabler builds paved roads and secure defaults so teams get security for free, instead of saying no after the fact.

• Secure-by-design requirements upfront
• Self-service guardrails, SCPs, templates
• Early design reviews, not gates
• Cloudflare Zero Trust replaced VPN

## How would you drive a critical finding to verified closure?
Aliases: remediation to verified closure | ensuring fixes actually work | closing out findings
Tags: remediation, verification, retesting, sast
I close findings by retesting and static analysis — verification, never developer attestation that it is fixed.

• Retest the actual exploit
• Static analysis confirms root cause
• Owner, deadline, tracked ticket
• CYBIC: verification-driven remediation

## When would you recommend accepting a risk rather than fixing it?
Aliases: risk acceptance | when to accept risk | formal risk acceptance process
Tags: risk acceptance, risk register, compensating controls, governance
I accept risk when remediation cost outweighs impact — but only with a documented owner, expiry date, and compensating controls.

• Documented, time-boxed acceptance
• Named business owner signs off
• Compensating controls in place
• Revisit on expiry, risk register

## Design a communication plan for a critical vulnerability disclosed to you.
Aliases: vuln disclosure communication | comms plan for critical bug | coordinating a critical fix
Tags: communication plan, vulnerability, stakeholders, incident
I define audiences and cadence: technical owners get detail and a fix, leadership gets impact and timeline, on a fixed rhythm.

• Separate technical vs executive messaging
• Single source of truth
• Fixed update cadence, clear owner
• Close loop, verified fix

## How do you present a trade-off between security, cost, and user experience to leadership?
Aliases: security cost UX trade-off | presenting trade-offs to leadership | balancing security and usability
Tags: trade-offs, cost, user experience, mfa, decision-making
I present options with explicit trade-offs and a recommendation — each with cost, residual risk, and UX impact so leadership decides.

• Two or three options, one recommendation
• Residual risk per option
• Cost and UX quantified
• MFA/Zero Trust: friction vs assurance

## Tell me about a time you led stakeholder communication during a live incident.
Aliases: incident stakeholder comms | communicating during an incident | crisis communication
Tags: incident response, communication, ddos, containment
During a live event I ran technical containment and stakeholder comms in parallel — keeping leadership informed throughout the response.

• Live-event DDoS mitigation
• Exec-impersonation phishing containment
• Regular sitreps, calm cadence
• Post-incident analysis, lessons shared
