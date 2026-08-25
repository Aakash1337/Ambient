# Cybic Voice Intelligence for Customer Engagement — Current Capability and Expansion Path

## Executive Summary

- **Alignment is selective, not feature-for-feature.** The pre-existing Ambient prototype
  demonstrates real-time audio capture, local transcription, information-request detection,
  short-session context, response generation, optional local speech output, and replayable
  interaction records. That is credible evidence for the conversation-intelligence portion of
  the platform vision—not evidence that the requested platform already exists.
- **The defining requirement is one continuous customer journey.** The target platform must
  identify the customer and maintain one persistent conversation across voice, website chat,
  SMS, email, messaging channels, devices, and live representatives. Every AI response,
  qualification step, appointment, CRM action, workflow, and handoff should use this shared
  customer context.
- **The recommended MVP should prove one complete cross-channel business outcome.** Start with
  inbound voice, website chat, transactional SMS, one CRM, and one calendar integration. The MVP
  should recognize the customer, continue without repetition, answer from approved knowledge,
  capture and qualify the lead, book an appointment, update the CRM, and hand off with a summary
  and transcript.
- **Most platform capabilities remain implementation work.** Production telephony, durable
  identity and conversation state, intelligent scheduling, business integrations,
  administration, analytics, multi-tenancy, security controls, and horizontal scale require
  discovery, architecture, development, and acceptance testing. Timeline and cost must be
  finalized after the launch scope, systems of record, compliance obligations, volumes, and
  ownership model are confirmed.

## Purpose

Demonstrate Cybic's current AI-assisted voice capabilities, explain their selective relevance to
inbound/outbound customer engagement, and distinguish that experience from the substantial new
work required for the MVP and long-term platform.

> **Scope boundary:** The current repository is a single-seat desktop agent-assist prototype. It
> captures microphone and system audio, transcribes locally, detects information-seeking
> utterances, generates contextual answer cards, writes local session logs, and can play TTS on
> Linux. It does not currently provide telephony, web chat, SMS/email/social messaging, customer
> identity resolution, durable cross-channel history, lead qualification, scheduling, CRM
> actions, live transfer, an admin/reporting portal, multitenancy, or production APIs. Those are
> target-platform requirements and roadmap items, not claims about the demo.

## Problem Statement

Customers encounter disconnected channels, systems, and employees. Information gathered in web
chat may be unavailable when the customer calls; qualification may be repeated after a transfer;
and scheduling, follow-up, and CRM updates may require manual work across several systems. This
fragmentation increases customer effort, slows response, creates inconsistent service, and can
cause opportunities to be lost.

The proposal's requirement is therefore not merely a voice bot, chatbot, or scheduling tool. It is an
identity-resolved customer-engagement platform that preserves one customer journey across every
channel and turns each conversation into governed business actions. It should operate as a
24/7 virtual sales and customer-service representative while involving employees whenever the
customer, policy, or confidence level requires a person.

## Core Design Principle — One Customer Journey

The unified customer and conversation record is the product's architectural center, not a later
integration. Voice is the first demonstrated channel.

Every interaction should be normalized into the same timeline and linked, when safely possible,
to a canonical customer profile using verified phone number, email, CRM ID, authenticated login,
or signed session identifier. Caller ID may help recognition but should not authenticate a
sensitive action. Ambiguous matches must be reviewed rather than silently merging two customers;
link, merge, unmerge, consent, and provenance decisions must be auditable.

Before each AI or employee interaction, the platform should assemble an authorized context
package containing:

- Relevant conversation history and an AI-generated summary
- Customer profile and verified contact details
- Qualification status and missing information
- Previous appointments, purchases, and open opportunities
- Completed and pending actions
- Recommended next action

This directly supports the proposal's defining scenario: a customer can discuss roof damage and
appointment preferences in web chat, call five minutes later, and continue without repeating the
same information. The same continuity must work when an employee participates and the customer
later returns through another channel.

## Our Solution (Why It Helps)

Cybic would place a unified customer and conversation layer between communication channels and
the client's existing systems. Voice, web chat, SMS, email, supported messaging applications,
and future channel adapters would publish interactions into the same identity-resolved timeline.

The AI would use that timeline, the customer profile, and approved company knowledge to
understand intent, collect and confirm information, qualify the opportunity, recommend or perform
the next action, and prepare a complete live-agent handoff. A deterministic policy and workflow
layer would decide which actions are allowed, what data is required, and whether customer
confirmation or employee approval is needed. CRM, calendar, messaging, and reporting connectors
would execute and measure the resulting business outcomes.

Ambient contributes the demonstrated real-time conversation-understanding components:
audio capture, transcription, information-request detection, short-session context, response
generation, optional local TTS, evaluation patterns, and interaction logging. The target platform
adds the durable identity, orchestration, channels, actions, governance, and scale around them.

The platform can support two operating modes:

- **Agent-assist:** AI listens, transcribes, recommends, and prepares actions; an employee speaks
  and approves changes.
- **Automated engagement:** AI manages an authorized journey and performs approved actions, with
  immediate handoff when policy, confidence, customer preference, or failure requires it.

## Selective Alignment to the Platform Vision

**Verdict: the current prototype has direct overlap with a limited part of the proposal, adjacent
experience in a few supporting areas, and substantial non-overlap.**

- **Direct overlap:** live two-sided audio capture, transcription, information-request detection,
  short-session context, contextual answers, evaluation signals, and interaction logs. Present
  these as the working voice-intelligence and agent-assist flow.
- **Adjacent experience:** optional local TTS, configurable context profiles, self-correction,
  session replay, privacy-aware local processing, and resilient real-time pipelines. Present these
  as useful engineering patterns, not equivalents of the requested platform features.
- **Net-new platform work:** telephony, persistent identity, omnichannel history, web/SMS/email/
  social channels, lead state, qualification, managed knowledge, scheduling, CRM actions, live
  transfer, administration, analytics, APIs, multitenancy, and enterprise scale. Present these as
  proposed architecture and delivery scope, not current product capability.

This distinction is important to the credibility of the demo. The story is not “we already built
everything in the proposal.” It is “we have independently solved several hard real-time
conversation problems, understand their operating constraints, and can show exactly where that
experience reduces risk—and where a new platform must still be built.”

## The Majority of the Requested Platform Is New Work

Only short-session conversational context is demonstrated at roughly the scope described in the
proposal. The voice pipeline, information-request gate, local profiles, TTS, and quality patterns
are relevant components, but each requires substantial production work. The proposal's defining
platform capabilities—persistent identity, omnichannel continuity, business workflows,
integrations, administration, analytics, and enterprise scale—are new work.

The implication is deliberate. The demo establishes relevant experience in real-time
conversation intelligence, while the proposal remains a substantive new platform engagement.

## Showcasing What We Have Today

```text
Customer + Agent Audio → Local Transcription → Information-Request Gate
→ Short-Session Context → Answer Card / Optional Local Voice
→ Answer Audit + Local Interaction Log
```

The current demo can show:

- Both sides of a desktop call captured from the agent's microphone and system audio
- Local speech transcription with automatic device handling and health warnings
- Detection of explicit questions, indirect information requests, and command-style asks
- Short current-session follow-up handling using recent transcript and answer context
- Switchable local Markdown profiles for topic, background, and transcription vocabulary
- Concise answer cards and optional local Linux speech output
- Answer audit, missed-question recovery, and labelled gate evaluation
- Local JSONL transcripts, decision reasons, answer status, latency, and replay

It cannot currently answer or originate telephone calls, route generated speech into a call,
maintain customer identity across sessions, execute a qualification flow, retrieve from a managed
knowledge base, book an appointment, transfer to an employee, update a CRM, or send messages.

## Demo Capabilities

The live presentation should stay focused on capabilities that actually exist:

- **Two-sided listening:** the audience sees the agent microphone and customer/system audio
  captured together. This establishes experience with real-time voice pipelines and
  channel-aware processing.
- **Local transcription:** speech appears as a live transcript. This demonstrates low-latency
  speech processing and a privacy-aware processing boundary.
- **Information-request detection:** questions and indirect asks produce answers while small talk
  and narration do not. This demonstrates gating, attention control, and false-positive
  evaluation—not full business-intent classification.
- **Short-session context:** a follow-up can refer to an earlier question or answer. This
  demonstrates context assembly inside one live conversation—not persistent customer memory.
- **Guided response generation:** a concise answer uses the active local context profile. This is
  prompt/context engineering—not a managed enterprise knowledge base.
- **Optional voice response:** the Linux prototype can speak configured answers locally. This
  demonstrates TTS and self-hearing controls—not production telephony or full-duplex Voice AI.
- **Audit and recovery:** answers can be reviewed, missed questions recovered, and sessions
  replayed. This demonstrates evaluation, observability, and correction—not business analytics.

The demo should stop there. Qualification, appointment booking, CRM updates, cross-channel
identity, employee transfer, campaigns, administration, analytics, and multi-tenant operation
belong in the proposed MVP or later roadmap and should not be simulated as if implemented.

## Presentation Positioning

To make the experience credible, present the work in this order:

1. **Show the existing product on its own terms.** Explain that it was built as an ambient
   real-time conversation assistant and demonstrate the live pipeline without first translating
   every screen into proposal language.
2. **Identify only the genuine overlap.** Connect the working audio, transcription, request
   detection, session context, response, evaluation, and logging components to the voice and
   conversation-intelligence parts of the platform vision.
3. **Name the boundary before discussing expansion.** State plainly that customer identity,
   omnichannel persistence, business workflows, telephony, and enterprise applications are not
   in the demo.
4. **Use one future journey, not a catalogue of promised screens.** The web-chat-to-phone MVP
   journey is enough to explain how existing experience would be extended into a business
   outcome.
5. **Treat the full proposal as a roadmap and discovery input.** Avoid claiming a ready-made
   connector, workflow, dashboard, or optimizer until it has been designed and tested against
   the client's systems and rules.

Avoid proposal-mirroring language such as “we already support all requested channels” or “the
demo proves end-to-end automation.” Prefer precise language: “we have implemented this component,”
“this pattern is reusable,” and “this capability is net-new work proposed for the MVP.”

## Current Demo, Recommended MVP, and Target Platform

- **Channels** — Current: desktop call audio, answer cards, and optional local Linux TTS, without
  call control. MVP: inbound AI voice, website chat, transactional SMS, and optional consented
  callbacks/reminders. Target: voice, web, SMS, email, supported messaging, mobile apps, and future
  API-connected channels.
- **Identity and continuity** — Current: short in-process context and unidentified local logs.
  MVP: a persistent customer profile and timeline linked by verified phone, email, CRM ID, or
  session. Target: governed continuity across channels, devices, and employees.
- **Conversation intelligence** — Current: information-request detection and contextual answers.
  MVP: three to five business intents, structured capture, qualification, approved knowledge,
  summaries, and next action. Target: specialized sales/service agents covering broader product,
  objection, financing, and service workflows.
- **Scheduling** — Current: none. MVP: one calendar with availability, duration, territory, ZIP,
  skill, and confirmation rules. Target: multi-resource optimization using travel, GPS,
  specialization, workload, priority, emergency, and approved performance factors.
- **CRM and workflows** — Current: none. MVP: one CRM with core lead, note, status, task,
  appointment, and transcript-reference actions. Target: vendor-neutral connectors covering the
  requested systems, campaigns, notifications, and custom APIs.
- **Handoff** — Current: the human remains present in agent-assist mode. MVP: warm voice/chat
  handoff with the complete context packet. Target: a unified employee workspace using the same
  timeline across channels.
- **Administration and analytics** — Current: local profiles, logs, tests, and latency records.
  MVP: roles, knowledge/prompt management, transcript review, audit, and core funnel/AI metrics.
  Target: workflow builder, automation management, multi-company administration, and executive BI.
- **Security and scale** — Current: useful local-processing and evaluation lessons. MVP:
  encryption, tenant-aware access, consent, retention, immutable action audit, monitoring,
  backups, and agreed reliability targets. Target: enterprise isolation, thousands of simultaneous
  interactions, regional deployment, high availability, and disaster recovery.

> **Channel correction:** Google discontinued Google Business Messages on July 31, 2024.
> Replace it with a generic future-channel requirement or select a currently supported Google or
> third-party entry point during discovery. See the
> [official Google announcement](https://developers.google.com/business-communications/business-messages/resources/release-notes/update-on-gbm?hl=en).

## How It Supports the Proposed Solution

The original voice journey remains valid:

```text
Customer Call → Voice AI → Qualification → Business Rules → Routing / Scheduling → CRM
```

The complete platform places that journey inside a reusable omnichannel architecture:

```text
Any Channel → Identity Resolution → Unified Customer Conversation
→ AI + Approved Knowledge → Policy / Business Rules / Workflows
→ Routing / Scheduling / CRM → Human or Automated Follow-up
```

The call-center problem is the voice expression of a broader continuity problem. The first
flow demonstrates one business journey; the second allows the same identity, knowledge,
qualification, scheduling, CRM, and handoff services to support every channel without losing
customer context.

## Recommended Target Architecture

```text
Web Chat | Voice | SMS | Email | WhatsApp | Messenger | Future Channels
                              ↓
                 Channel and Voice Media Adapters
                              ↓
     Unified Interaction Gateway — events, consent, delivery, idempotency
                              ↓
              Identity Resolution and Conversation Hub
          customer profile + unified timeline + journey state
                              ↓
                    Customer Context Package
 history + summary + qualification + appointments + recommended action
                              ↓
                     Agentic AI Orchestrator
        knowledge/RAG | qualification | scheduling | handoff
                              ↓
      Policy, Rules, Tool Gateway, and Durable Workflow Engine
                              ↓
 CRM | Calendars | Dispatch | Contact Center | Messaging | Custom APIs

Every event → audit/event stream → operational analytics, warehouse, AI evaluation
Admin control plane → tenants, users, knowledge, prompts, rules, integrations, reporting
```

Key architectural rules:

- Each channel uses its own adapter but publishes the same canonical interaction event.
- The platform owns conversation history, summaries, consent evidence, AI decisions, and action
  audit; the CRM remains authoritative for contacts/opportunities, and calendar/dispatch systems
  remain authoritative for availability.
- The AI may propose an action, but deterministic policy decides whether it is allowed and
  whether confirmation or approval is required.
- Models never receive unrestricted CRM or database access. Every action uses a least-privilege,
  schema-validated, idempotent, retryable, and audited tool.
- Full authorized history remains available to employees, while the AI receives a bounded
  summary and relevant retrieved events rather than every lifetime transcript.
- Long-running reminders, campaigns, retries, and follow-ups use a durable workflow engine—not
  model memory or an in-process queue.
- Production messages and business actions must never be silently discarded. The prototype's
  drop-oldest in-memory queues are appropriate for live assistance, not durable workflows.

## Recommended Technology and Hosting Direction

The production stack should remain provider-adaptable and favor managed, horizontally scalable
services:

- **Interfaces:** React/Next.js for customer chat, employee workspace, and administration
- **Services:** Python/FastAPI for AI orchestration and integrations, with explicit service
  boundaries rather than a desktop process
- **Data:** PostgreSQL with `pgvector` for the MVP, Redis for ephemeral state and locks, object
  storage for recordings/documents, and a warehouse or lakehouse for analytics
- **Events and workflows:** a managed event bus plus Temporal or an equivalent durable workflow
  engine with retries, timeouts, idempotency, dead-letter handling, and manual recovery
- **Observability:** OpenTelemetry-compatible logs, metrics, and traces with tenant-aware alerts
- **Deployment:** containers and infrastructure as code on the selected cloud, with autoscaling,
  backups, restore tests, regional data placement, and separate low-latency voice media workers
- **Provider abstraction:** model, embedding, STT, TTS, telephony, email, and messaging providers
  sit behind adapters so evaluation or commercial needs can change a provider without redesign

The current Python/asyncio, Whisper, local gate, answer-generation, TTS, and test assets are
valuable for prototype reuse and evaluation. The Textual UI, local JSONL store, local playback,
and command-line model invocation are not the target production architecture.

## Recommended MVP

The MVP should prove the proposal's hardest differentiator—continuity—through one controlled
end-to-end journey:

```text
Customer discusses roof damage in web chat
→ provides and verifies a mobile number
→ calls five minutes later
→ AI or employee receives the previous history and summary
→ qualification continues without repetition
→ service area and required skills are validated
→ the best valid representative and slot are offered
→ appointment is confirmed once
→ CRM is updated
→ SMS confirmation is sent
→ warm-transfer packet is available if escalation occurs
```

Recommended MVP scope:

- One company, while tenant/brand/location keys are built into every record and authorization
- Website chat, inbound voice, and transactional SMS
- Three to five high-value sales/service intents
- Verified identity using mobile number, email, CRM ID, or signed web session
- Unified customer profile, conversation timeline, summary, and qualification state
- Curated knowledge ingestion with citations and administrator approval
- Lead capture, ZIP/service-area validation, qualification, and governed next actions
- One calendar/scheduling ecosystem and one CRM
- Warm employee handoff with complete context
- Basic admin functions, audit history, transcript review, and core funnel/AI metrics
- Security, consent, retention, observability, backups, and domain evaluation from launch

Defer every CRM at once, every social channel, broad outbound campaigns, probabilistic identity
merging, advanced route optimization, full executive BI, and unrestricted AI autonomy until the
end-to-end journey is working and measured.

## How We Add the Requested Capabilities — Phased Delivery

1. **Discovery and solution definition — 3–4 weeks.** Select launch journeys, channels, vendors,
   identity rules, systems of record, compliance obligations, KPI definitions, source/IP terms,
   and acceptance tests.
2. **Platform spine and shadow mode — 6–8 weeks.** Build the tenant/auth foundation, canonical
   events, conversation store, verified identity, knowledge ingestion, adapter framework, audit
   trail, and AI recommendations without autonomous writes.
3. **End-to-end MVP — 8–12 weeks.** Deliver website chat, inbound voice, transactional SMS,
   cross-channel continuity, qualification, service-area validation, grounded answers, one
   calendar, one CRM, warm handoff, basic administration, and reporting.
4. **Controlled production pilot — 6–8 weeks.** Limit locations and traffic while completing
   security/load testing, monitoring, recovery, employee approvals, outcome reconciliation,
   domain evaluation, and measured expansion of automation.
5. **Expansion waves — 8–12 weeks per wave.** Add outbound voice, email, supported messaging,
   more CRMs/calendars, campaigns, uploads, advanced scheduling, richer BI, and multi-company
   self-service according to priority.

With a dedicated cross-functional team and timely access to vendor sandboxes, policies, and
customer data, the directional estimate for a customer-facing MVP is **17–24 weeks from
kickoff**. Production pilot and hardening follow; they should not be hidden inside the demo or
MVP estimate. This is a planning range, not a fixed commercial commitment.

## Evaluation, Security, and Operations

The proposal requires enterprise controls from the start:

- **Evaluation:** test intent, extraction, grounding, response, qualification, scheduling,
  routing, handoff, and action correctness separately. Use stricter thresholds for autonomous
  actions than for employee suggestions, with regression gates by model, prompt, knowledge,
  workflow, and connector version.
- **Identity and tenancy:** include tenant, brand, location, customer, journey, and channel
  identifiers in every record; require verification for sensitive actions and protect against
  accidental customer merges.
- **Access and data protection:** SSO/OIDC, MFA where appropriate, RBAC plus scoped attributes,
  encryption in transit and at rest, managed secrets/KMS, tenant-isolated knowledge, PII
  redaction, retention/deletion controls, and immutable action audit.
- **AI and tool safety:** separate trusted instructions from caller and document content, defend
  against prompt injection, restrict tools by least privilege, require confirmation/approval for
  sensitive actions, and retain rollback or reconciliation paths.
- **Communication compliance:** configure AI/recording disclosure, outreach consent,
  do-not-call, calling windows, opt-out, template approval, and retention rules by jurisdiction
  with legal review.
- **Reliability:** horizontal stateless services, durable queues, retry/dead-letter handling,
  per-tenant quotas, rate limits, health checks, monitoring, on-call response, backups, restore
  drills, incident response, and agreed RTO/RPO/SLO targets.
- **Maintenance:** budget for connector/API changes, knowledge operations, model/prompt/evaluation
  regression, security patching, cost and latency tuning, capacity planning, disaster-recovery
  tests, and customer support.

Current privacy should be stated precisely: raw audio, transcription, and the first question gate
run locally, but accepted question text and a bounded context window reach the configured external
answer model. Local session logs are plaintext and have no enterprise access or retention layer.

## AI Model Strategy

Use a task-specific, provider-neutral model gateway rather than one model for every job:

- Streaming speech-to-text and text-to-speech selected for call quality, latency, language,
  data-use terms, interruption support, and cost
- Small, fast models for intent classification and structured extraction
- A higher-capability dialogue/reasoning model for complex responses and orchestration
- Embeddings and reranking for permission-aware knowledge retrieval with citations
- Deterministic rules and schema-validated tools for business decisions and writes
- Primary/fallback providers chosen through domain-specific quality, latency, privacy, reliability,
  and cost evaluations

"Continuous learning" should mean administrator-approved knowledge publishing and
evaluation-driven model/prompt improvements—not uncontrolled self-training from customer calls.

## Open Decisions Required for Timeline and Cost

- Which customer journeys, intents, brands, locations, and business units are in release one?
- Which launch channels are mandatory, and does outbound voice mean callbacks/reminders or full
  campaigns?
- Which CRM, calendar, telephony, messaging, email, and identity systems are authoritative, and
  what API/sandbox access exists?
- Which scheduling constraints are mandatory for MVP, and which belong to later optimization?
- What identity matching, verification, merge, conflict, consent, and manual-review rules apply?
- Which actions may AI perform autonomously, which require customer confirmation, and which
  require employee approval?
- Which privacy, recording, marketing-contact, retention, deletion, residency, accessibility,
  and audit obligations apply in each region?
- What concurrency, latency, uptime, support, recovery, and data-migration targets apply?
- How are lead, qualified lead, appointment, demo, sale, resolution, revenue, savings, CSAT, and
  NPS defined, and which system owns each measure?
- What hosting, maintenance/SLA, source-code ownership, pre-existing Cybic IP, bespoke client IP,
  third-party licensing, AI-model/data rights, credential handoff, and repository ownership terms
  are expected?

A responsible cost estimate should follow Phase 0 and separate:

- One-time product, integration, security, data, migration, and testing work
- Recurring telephony, message, email, model, speech, storage, compute, observability, support,
  and third-party-license costs
- Sensitivity to conversation minutes, message volume, model usage, recordings, retention,
  concurrency, locations, integrations, and support level

## Scope and Production-Readiness Caveat

This document aligns the existing demo to the proposed platform; it is not a binding production
architecture, commercial estimate, or claim that the full platform exists today. Production
telephony, durable omnichannel state, identity resolution, business-system actions,
administration, analytics, enterprise security, and scale remain to be designed, implemented,
and validated. Final architecture, stack, cost, hosting, maintenance, model selection, and
source-code/IP terms should follow discovery and validation of the named integrations.

## Demo Outcome

Demonstrates Cybic's ability to move from conversational AI to actionable business workflows
rather than simply providing a voice chatbot.

The live demonstration proves reusable components of the speech, short-session context,
response, evaluation, and interaction-record layer. It does not claim that the complete
omnichannel platform or its workflows already exist. It establishes a credible starting point
for one complete journey: identify and understand the customer, capture and qualify the
opportunity, apply governed business rules, route or schedule the next step, update the CRM,
preserve the interaction for future channels, and involve a live representative whenever
automation should stop.

---

**Source references:** [Platform proposal](AGENTIC-AI-PLATFORM-PROPOSAL.md) ·
[Current call-center demo](CALLCENTER-DEMO.md) · [Architecture](../ARCHITECTURE.md) ·
[Project README](../../README.md)
