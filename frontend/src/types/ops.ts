export type Role = "staff" | "doctor";

export type TaskItem = {
  id: number;
  call_sid: string | null;
  patient_name: string | null;
  callback_number: string | null;
  request_type: string;
  assigned_role: string;
  priority: string;
  details: string | null;
  status: "pending" | "in_progress" | "completed" | string;
  created_at: string;
};

export type TranscriptItem = {
  id: number;
  call_sid: string;
  text: string;
  created_at: string;
};

export type Metrics = {
  calls_today: number;
  escalation_rate: number | null;
  avg_call_duration_sec: number | null;
};

export type EscalationItem = {
  id: number;
  call_sid: string;
  reason: string;
  details: string | null;
  created_at: string;
};

export type AppNotification = {
  id: number;
  role: "staff" | "doctor";
  title: string;
  message: string;
  kind: string;
  is_urgent: boolean;
  is_read: boolean;
  call_sid: string | null;
  task_id: number | null;
  escalation_id: number | null;
  created_at: string;
};
