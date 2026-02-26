CliniqAI — Implementation Snapshot & Roadmap

Last Updated: 27th February 2026

1️⃣ Current Implementation (Repository State)
Backend Architecture

Framework: FastAPI
Structure: Modular router design

API Modules

/api/calls — Telephony entrypoint & conversation handling

/api/tasks — Task lifecycle management

/api/transcripts — Persistent conversation history

/api/escalations — Safety & human handoff logic

/api/analytics — Operational metrics endpoints

/api/notifications — Real-time staff awareness layer

Core Domain Models

calls

tasks

transcripts

escalations

notifications

Telephony Flow (Twilio Lifecycle)

Inbound webhook handling

Greeting initialization

Speech gathering and collection

Controlled AI response generation

Conversational continuation loop

Deterministic Conversation Orchestration

State-machine controlled dialogue flow

Structured slot collection for appointment scheduling

Intent locking during task execution

Post-task receptionist mode

Intent Detection Layer

Supported intents:

Appointment scheduling

Callback requests

General clinic queries

Medication/refill detection safeguards

Safety Escalation System

Escalation triggers:

Emergency keyword detection

Explicit human handoff requests

Repeated AI misunderstanding detection

AI provider instability fallback

Principle: Uncertainty defaults to escalation.

Persistence & Notifications

Persistent transcript storage and retrieval

In-app notification generation

Role-based delivery (staff / doctor)

Optional SMS fanout support

Frontend Capabilities

Role-based navigation for clinic workflows

Staff dashboard

Doctor dashboard

Operational Interfaces

Task Queue

pending

in_progress

completed

Additional Interfaces

Transcript viewer linked to call sessions

Escalation alert center

Notification dropdown:

Mark as read

Clear all

Operational Efficiency

Search

Filtering

Task grouping

2️⃣ Next Iteration — Improvements Needed
Product & Workflow Enhancements

Replace demo authentication with production-grade backend auth

Implement full RBAC across clinic roles

Introduce admin dashboard for clinic configuration

Add explicit doctor decision workflow:

Approve

Reject

Request follow-up

Data Model Enhancements

Introduce clinics and users tables for multi-tenant isolation

Add call recording metadata:

recording_url

duration_sec

consent_indicators

Expand audit trail coverage for sensitive reads and actions

Analytics Expansion

Average call duration calculation

Hourly call load visualization

Task completion SLA tracking

Provider latency and failure monitoring

Safety Enhancements

Duration-based escalation thresholds

Stronger distress & emotional sentiment detection

Deterministic medication and test-result guardrails

3️⃣ Risk Areas & Failure Modes
Safety Risk

Missed emergency escalation (highest risk scenario)

Medical requests bypassing insufficient guardrails

Safety Principle:

When uncertain, escalate.

Reliability Risk

In-memory conversation context volatile across service restarts

External provider outages (LLM / STT / TTS)

Notification side effects lacking durable retry guarantees

Compliance Risk

Demo authentication not production compliant

Lack of tenant isolation

PHI exposure risk without strict provider boundaries

Operational Risk

Weak migration discipline causing instability

Limited observability delaying degraded behavior detection

4️⃣ Mitigation Strategy
Safety Mitigation

Execute emergency & medical escalation rules before LLM response generation

Maintain conservative escalation thresholds during pilot

Weekly manual review of failed & escalated calls

Reliability Mitigation

Move conversation context to Redis (TTL-backed persistence)

Introduce provider fallback + circuit breaker behavior

Add retry & dead-letter handling for outbound notifications

Compliance Mitigation

Enforce backend authentication

Enforce server-side RBAC

Add tenant scoping (clinic_id) to all queries

Restrict PHI transmission to BAA-supported vendors only

Operational Mitigation

Adopt Alembic-based schema migration workflow

Use staged deployment strategy

Implement monitoring & alerting for:

Latency spikes

Escalation anomalies

Provider failures

5️⃣ Production Rollout Plan
Phase A — Single Clinic Pilot

Restrict automation to low-risk administrative requests

Conservative escalation thresholds

Manual review of first 100–300 calls

Phase B — Stabilization

Analyze misclassification & escalation trends

Tune prompts, rules & confidence thresholds

Validate staff workflow usability

Phase C — Controlled Expansion

Expand to 3–5 clinics

Validate tenant isolation under load

Introduce service-level objectives

Phase D — General Production Rollout

Regional canary deployments

Maintain rollback capability per deployment & migration

6️⃣ Async Migration Plan (Required)
Motivation

Synchronous external side effects increase latency and reduce reliability during provider failures.

Target Architecture

API commits core DB transaction

System emits domain event (task.created, escalation.created)

Worker consumes event asynchronously

Worker performs notifications & integrations

Delivery status tracked independently

Migration Steps

Introduce Redis-backed worker infrastructure

Create job event table with idempotency keys

Move notification delivery outside request thread

Implement exponential backoff retry logic

Add dead-letter queue & replay tooling

Async Safety Controls

Idempotent handlers

Retry limits

Dead-letter monitoring

Feature-flag controlled rollout

7️⃣ Database Migration Strategy

Initialize Alembic baseline

Apply additive migrations first

Incremental backfill (clinic_id, auth references)

Enforce constraints after validation

Deploy in compatibility mode

Remove legacy logic post-verification

Migration Principles

Validate against staging snapshot

Every production migration includes rollback plan

Prefer forward-only migrations

8️⃣ TTS Provider Decision — Pilot

Baseline: Google Cloud TTS

Migration to ElevenLabs only if:

Measurable patient experience improvement

Acceptable cost efficiency

Compliance validation

Strategy:
Maintain Google TTS baseline and conduct controlled A/B testing before migration.