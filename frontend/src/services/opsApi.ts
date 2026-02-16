import type { AppNotification, EscalationItem, Metrics, TaskItem, TranscriptItem } from "../types/ops";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `API request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export function getRoleQueue(role: "staff" | "doctor"): Promise<TaskItem[]> {
  const path = role === "doctor" ? "/api/tasks/queue/doctor" : "/api/tasks/queue/staff";
  return api<TaskItem[]>(path);
}

export function getAllTasks(): Promise<TaskItem[]> {
  return api<TaskItem[]>("/api/tasks/");
}

export function getAllTasksForRole(role: "staff" | "doctor"): Promise<TaskItem[]> {
  return api<TaskItem[]>(`/api/tasks/?role=${encodeURIComponent(role)}`);
}

export function updateTaskStatus(taskId: number, status: "pending" | "in_progress" | "completed"): Promise<TaskItem> {
  return api<TaskItem>(`/api/tasks/${taskId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

export function getTranscript(callSid: string): Promise<TranscriptItem> {
  return api<TranscriptItem>(`/api/transcripts/${callSid}`);
}

export function getMetrics(): Promise<Metrics> {
  return api<Metrics>("/api/analytics/");
}

export function getEscalations(role?: "staff" | "doctor"): Promise<EscalationItem[]> {
  const suffix = role ? `?role=${encodeURIComponent(role)}` : "";
  return api<EscalationItem[]>(`/api/escalations/${suffix}`);
}

export function getNotifications(role: "staff" | "doctor", unreadOnly = false): Promise<AppNotification[]> {
  return api<AppNotification[]>(
    `/api/notifications/?role=${encodeURIComponent(role)}&unread_only=${unreadOnly ? "true" : "false"}`,
  );
}

export function markNotificationRead(notificationId: number): Promise<AppNotification> {
  return api<AppNotification>(`/api/notifications/${notificationId}/read`, { method: "PATCH" });
}

export function markAllNotificationsRead(role: "staff" | "doctor"): Promise<{ updated: number }> {
  return api<{ updated: number }>(`/api/notifications/read-all?role=${encodeURIComponent(role)}`, {
    method: "PATCH",
  });
}
