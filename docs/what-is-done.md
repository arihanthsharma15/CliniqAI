# What Is Done
## CliniqAI Implementation Snapshot + Next Improvements

**Last Updated:** February 16, 2026

---

## 1) Implemented in Repo (Current)

### Backend
- FastAPI app with routers:
  - `/api/calls`
  - `/api/tasks`
  - `/api/transcripts`
  - `/api/escalations`
  - `/api/analytics`
  - `/api/notifications`
- Core models available:
  - `calls`, `tasks`, `transcripts`, `escalations`, `notifications`
- Twilio webhook call flow implemented:
  - greeting, gather, collect, response
- Intent extraction implemented:
  - appointment, callback, general, medication/refill intent patterns
- Escalation logic implemented:
  - emergency keywords
  - explicit human request
  - failed understanding and AI instability cases
- Transcript persistence and retrieval implemented
- Notification generation implemented (in-app + optional SMS fanout)

### Frontend
- Role-based routing for staff and doctor dashboards
- Task queue UI with status actions:
  - pending
  - in_progress
  - completed
- Transcript panel by call reference
- Escalation alert center
- Notification dropdown with mark-read/clear-all
- Search and task grouping in operations dashboard

---

## 2) Improvements Needed (Next)

### Product/Flow
- Replace demo auth with real backend auth + RBAC
- Add admin dashboard and clinic-level configuration
- Add explicit doctor action workflow (approve/reject)

### Data Model
- Add `clinics` and `users` tables for full multi-tenant model
- Add recording metadata (`recording_url`, `duration_sec`, consent flags)
- Add stronger audit trail metadata on sensitive reads/actions

### Analytics
- Add avg duration calculation
- Add hourly load metrics and completion SLA metrics
- Add operational latency/error metrics for providers

### Safety
- Add explicit duration-based escalation threshold
- Add stronger distress sentiment detection
- Tighten medication/test-result hard guardrails

---

## 3) Potential Failures & Risks

### Safety Risk
- Missed emergency escalation can create patient harm.
- Medical-related requests may be mishandled if guardrails are loose.

### Reliability Risk
- In-memory conversation context can be lost on restart.
- Provider outages (LLM/STT/TTS) can degrade call quality.
- SMS/notification side effects can fail without durable retries.

### Compliance Risk
- Demo auth and no tenant isolation are not production-safe.
- PHI exposure risk if provider/BAA boundaries are not enforced.

### Operational Risk
- No migration discipline can cause deployment breakage.
- Limited observability can hide degradation until users complain.

---

## 4) Mitigation Plan

### Safety Mitigation
- Hard-code emergency and medical escalation before LLM response.
- Use conservative escalation defaults during pilot.
- Review escalated and failed calls weekly.

### Reliability Mitigation
- Move context and pending alerts to Redis (durable with TTL).
- Add provider fallback matrix and circuit breaker behavior.
- Add retry + dead-letter queue for outbound notifications.

### Compliance Mitigation
- Enforce backend authentication and server-side RBAC.
- Add tenant scoping (`clinic_id`) in all domain queries.
- Restrict PHI flow to BAA-supported providers only.

### Operational Mitigation
- Adopt Alembic migrations and staged rollout controls.
- Add dashboards/alerts for latency, failures, escalation spikes.

---

## 5) Production Flow (1 Clinic Pilot to Scale)

### Phase A: 1 Clinic Pilot
- Scope calls to low-risk categories first.
- Keep escalation threshold conservative.
- Monitor first 100-300 calls manually.

### Phase B: Stabilization
- Address recurring misclassification/escalation patterns.
- Tune prompts, intent rules, and confidence thresholds.
- Confirm staff workflow acceptance.

### Phase C: Controlled Expansion
- Move to 3-5 clinics.
- Validate tenant isolation and reliability under higher volume.
- Introduce stricter SLO targets.

### Phase D: General Production Rollout
- Roll out regionally with canary strategy.
- Keep rollback plan per deployment and per migration.

---

## 6) Async Migration Plan (Required)

### Why
Current flow has synchronous external side effects that can impact request latency and reliability.

### Target Async Design
1. API writes core DB records in transaction.
2. API emits job event (`task.created`, `escalation.created`).
3. Worker processes event (SMS/push/integration).
4. Worker logs delivery attempts and status.

### Migration Steps
1. Add Redis + worker framework.
2. Add job event table + idempotency keys.
3. Move notification delivery out of request thread.
4. Add retry with exponential backoff.
5. Add dead-letter queue + replay command.

### Async Failure Mitigations
- Idempotent handlers prevent duplicate sends.
- Max-retry and dead-letter prevent endless loops.
- Alert on queue lag and dead-letter growth.
- Feature-flag async path for controlled rollout/rollback.

---

## 7) Database Migration Plan

1. Initialize Alembic baseline from current schema.
2. Additive migration first (new nullable columns/tables).
3. Backfill data in batches (`clinic_id`, auth references).
4. Enforce constraints only after backfill success.
5. Deploy app in compatibility mode during transition.
6. Remove legacy code after full validation.

### Migration Safety Rules
- Every migration tested in staging snapshot first.
- Every prod migration has rollback plan + backup.
- Prefer forward-only migration policy in production.

---

## 8) Decision Note: TTS Provider for Pilot

- **For 1 clinic pilot:** current Google Cloud TTS is acceptable.
- Move to ElevenLabs only if:
  - measured voice quality lift is clear in pilot feedback,
  - cost is justified,
  - compliance/BAA policy is satisfied for your PHI flow.
- Practical approach: keep Google as baseline and A/B test ElevenLabs on a subset before committing spend.
