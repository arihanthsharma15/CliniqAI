# 🏥 CliniqAI — Implementation Snapshot & Roadmap
> **Internal Documentation** | Last Updated: 28th February 2026

---

## 1️⃣ Current Implementation (Repository State)

### 🏗️ Backend Architecture
* **Framework:** `FastAPI`
* **Structure:** Modular router design with dependency injection.

### 🔌 API Modules
| Endpoint | Functionality | Status |
| :--- | :--- | :--- |
| `/api/calls` | Telephony entrypoint & conversation handling | ✅ Live |
| `/api/tasks` | Task lifecycle management | ✅ Live |
| `/api/transcripts` | Persistent conversation history | ✅ Live |
| `/api/escalations` | Safety & human handoff logic | ✅ Live |
| `/api/analytics` | Operational metrics endpoints | 🏗️ Beta |
| `/api/notifications` | Real-time staff awareness layer | ✅ Live |

### 🧠 Core Domain Models
- `calls` • `tasks` • `transcripts` • `escalations` • `notifications`

---

## 📞 Telephony & Logic Flow

### Twilio Lifecycle
1. **Inbound Webhook:** Initial request capture.
2. **Greeting:** Context-aware initialization.
3. **Speech Gathering:** Real-time stream collection.
4. **AI Generation:** Controlled LLM response.
5. **Loop:** State-aware conversational continuation.

### 🤖 Deterministic Orchestration
* **State-Machine:** Controlled dialogue flow preventing AI "hallucination loops."
* **Slot Collection:** Structured data gathering across appointment, refill, and callback flows.
* **Intent Locking:** Freezes state during critical slot-filling execution.
* **Receptionist Mode:** Post-task standby state with graceful exit handling.

### 📋 Supported Call Flows
| Flow | Slots Collected | Assigned To |
| :--- | :--- | :--- |
| Appointment Booking | Name, Type, Date, Time | Staff |
| Prescription Refill | Name | Doctor |
| Callback Request | Name, Preferred Time | Staff |
| Escalation | — | Staff / Doctor (by reason) |

---

## 🛡️ Safety & Escalation System

> [!IMPORTANT]
> **Core Principle:** Uncertainty defaults to escalation.

**Trigger Conditions:**
- [x] Emergency keyword detection (chest pain, bleeding, can't breathe).
- [x] Explicit human handoff requests.
- [x] Repeated misunderstanding — 3 consecutive unrecognised turns.
- [x] AI provider instability / latency fallback.

**Validated Metrics (28 Feb 2026 Test Run):**
- Escalation Rate: **11.8%** (target < 15%) ✅
- Missed Emergency Escalations: **0** ✅
- Avg Conversational Turns to Resolution: **3.9** ✅

---

## 🖥️ Frontend & Operations

### Role-Based Interfaces
* **Staff Dashboard:** Active Task Queue (`pending`, `in_progress`, `completed`).
* **Doctor Dashboard:** High-level clinical oversight + refill/escalation queue.
* **Transcript Viewer:** Deep-dive into specific call sessions.

### Operational Efficiency
* **Alert Center:** Centralized escalation monitoring.
* **Notifications:** Real-time "Mark as Read" / "Clear All" functionality.
* **Filtering:** Advanced search by task group and priority.

---

## 🚀 2. Next Iteration — The Roadmap

### 🔐 Security & Auth
- [ ] **Production Auth:** Replace demo auth with production-grade backend.
- [ ] **RBAC:** Implement full Role-Based Access Control across all roles.
- [ ] **Admin UI:** Global clinic configuration dashboard.

### 🏥 Clinical Workflow
- [ ] **Doctor Decisioning:** Approve / Reject / Request Follow-up workflow.
- [ ] **Multi-tenancy:** `clinics` and `users` tables for data isolation.
- [ ] **Call Metadata:** Store `recording_url`, `duration`, and `consent`.

### 🚢 Infrastructure
- [ ] **Railway Deployment:** Backend live on Railway via Docker.
- [ ] **Vercel Deployment:** Frontend live on Vercel.
- [ ] **Twilio Webhook:** Point to Railway production URL.

---

## ⚠️ 3. Risk Areas & Mitigation

> [!CAUTION]
> **Highest Risk:** Missed emergency escalation. Safety rules MUST execute before LLM generation.

| Risk Category | Mitigation Strategy |
| :--- | :--- |
| **Reliability** | Move context to **Redis** (TTL-backed persistence). |
| **Compliance** | Restrict PHI to BAA-supported vendors; enforce Server-side RBAC. |
| **Operational** | Adopt **Alembic** migrations and staged "Canary" deployments. |

---

## 🛠️ 4. Async Migration Plan

**Goal:** Reduce latency by moving side-effects (notifications/integrations) out of the request thread.

1. **Event Emission:** API commits DB transaction → Emits `task.created`.
2. **Worker:** Redis-backed worker consumes event.
3. **Resilience:** Exponential backoff + Dead-letter queue (DLQ) for failed alerts.

---

## 🔊 5. TTS Provider Strategy
* **Primary:** Google Cloud TTS (Baseline).
* **Evaluation:** Move to **ElevenLabs** only if A/B testing shows significant patient experience improvement vs. cost.

---