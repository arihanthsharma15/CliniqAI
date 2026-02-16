import { useEffect, useMemo, useRef, useState } from "react";
import PageMeta from "../../components/common/PageMeta";
import {
  getAllTasksForRole,
  getEscalations,
  getMetrics,
  getTranscript,
  updateTaskStatus,
} from "../../services/opsApi";
import type { EscalationItem, Metrics, Role, TaskItem } from "../../types/ops";
import { useSearch } from "../../context/SearchContext";

function badge(status: string): string {
  if (status === "pending") return "bg-warning-100 text-warning-700";
  if (status === "in_progress") return "bg-blue-light-100 text-blue-light-700";
  if (status === "completed") return "bg-success-100 text-success-700";
  if (status === "mixed") return "bg-gray-200 text-gray-800";
  return "bg-gray-100 text-gray-700";
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

function cleanPatientName(name: string | null): string {
  if (!name) return "To verify";
  const bad = new Set(["no thank you", "and the callback", "in the evening", "my name", "thanks for"]);
  return bad.has(name.toLowerCase().trim()) ? "To verify" : name;
}

function humanizeEscalationReason(reason: string): string {
  const normalized = reason.trim().toLowerCase();
  const map: Record<string, string> = {
    requested_human: "Requested Human Support",
    medical_emergency_keyword: "Possible Medical Emergency",
    failed_understanding_3_turns: "Repeated Understanding Failure",
    ai_service_instability: "AI Service Instability",
  };
  if (map[normalized]) return map[normalized];
  return reason
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function shortCallRef(value: string | null | undefined): string {
  if (!value) return "Not available";
  const compact = value.trim();
  if (compact.length <= 7) return compact;
  return `${compact.slice(0, 3)}...${compact.slice(-3)}`;
}

function callRefMatches(callRef: string | null | undefined, query: string): boolean {
  const raw = (callRef || "").trim().toLowerCase();
  if (!raw) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const masked = shortCallRef(raw).toLowerCase(); // ex: ca9...61c
  const suffix3 = raw.slice(-3);
  return (
    raw.includes(q) ||
    raw.endsWith(q) ||
    masked.includes(q) ||
    suffix3 === q ||
    (q.length <= 3 && suffix3.includes(q))
  );
}

export default function OperationsDashboard({ role }: { role: Role }) {
  const isStaffView = role === "staff";
  const { query, field } = useSearch();
  const [allTasks, setAllTasks] = useState<TaskItem[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [escalations, setEscalations] = useState<EscalationItem[]>([]);
  const [transcript, setTranscript] = useState<string>("");
  const [selectedSid, setSelectedSid] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [taskView, setTaskView] = useState<"all" | "single" | "combined">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending" | "in_progress" | "completed">("all");
  const [autoRefreshSec, setAutoRefreshSec] = useState<number>(0);
  const [highlightedCallSid, setHighlightedCallSid] = useState<string>("");
  const taskCardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [allData, metricData, escalationData] = await Promise.all([
        getAllTasksForRole(role),
        isStaffView ? getMetrics() : Promise.resolve(null),
        getEscalations(role),
      ]);
      setAllTasks(allData);
      setMetrics(metricData);
      setEscalations(escalationData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    if (!autoRefreshSec) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, autoRefreshSec * 1000);
    return () => clearInterval(id);
  }, [role, autoRefreshSec]);

  async function onGroupStatus(taskIds: number[], status: "pending" | "in_progress" | "completed") {
    try {
      await Promise.all(taskIds.map((id) => updateTaskStatus(id, status)));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Status update failed.");
    }
  }

  async function onTranscript(callSid: string | null) {
    if (!callSid) return;
    setSelectedSid(callSid);
    try {
      const data = await getTranscript(callSid);
      setTranscript(data.text || "No transcript found.");
    } catch {
      setTranscript("Transcript not found for this call.");
    }
  }

  function jumpToUrgent(callSid: string | null) {
    if (!callSid) return;
    setStatusFilter("all");
    setTaskView("all");
    setHighlightedCallSid(callSid);
    void onTranscript(callSid);
    setTimeout(() => {
      const target = taskCardRefs.current[callSid];
      if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
    setTimeout(() => setHighlightedCallSid(""), 1500);
  }

  const pending = allTasks.filter((t) => t.status === "pending").length;
  const inProgress = allTasks.filter((t) => t.status === "in_progress").length;
  const completed = allTasks.filter((t) => t.status === "completed").length;
  const filteredTasks = useMemo(() => {
    const statusFiltered = statusFilter === "all" ? allTasks : allTasks.filter((t) => t.status === statusFilter);
    const q = query.trim().toLowerCase();
    if (!q) return statusFiltered;
    return statusFiltered.filter((t) => {
      const callRef = (t.call_sid || "").toLowerCase();
      const fields = {
        all: [String(t.id), t.patient_name || "", t.callback_number || "", t.request_type || "", t.details || "", t.status || "", t.call_sid || ""]
          .join(" ")
          .toLowerCase(),
        patient: (t.patient_name || "").toLowerCase(),
        task_id: String(t.id).toLowerCase(),
        callback: (t.callback_number || "").toLowerCase(),
        request: `${t.request_type || ""} ${t.details || ""}`.toLowerCase(),
        status: (t.status || "").toLowerCase(),
        call_ref: callRef,
      };
      if (field === "call_ref") {
        return callRefMatches(callRef, q);
      }
      return fields[field].includes(q);
    });
  }, [allTasks, statusFilter, query, field]);
  const urgentEscalations = escalations.slice(0, 3);
  const escalationLoadFailed = !!error && error.toLowerCase().includes("fetch");
  const groupedByCall = useMemo(() => {
    const map = new Map<string, TaskItem[]>();
    for (const task of filteredTasks) {
      const patient = (task.patient_name || "").trim().toLowerCase();
      const callback = (task.callback_number || "").trim().toLowerCase();
      const key = task.call_sid?.trim().toLowerCase() || `${patient}|${callback}`;
      const bucket = map.get(key) || [];
      bucket.push(task);
      map.set(key, bucket);
    }
    return Array.from(map.entries()).map(([key, tasks]) => {
      const sorted = [...tasks].sort((a, b) => b.id - a.id);
      const primary = sorted[0];
      const statuses = Array.from(new Set(sorted.map((t) => t.status)));
      const groupStatus = statuses.length === 1 ? statuses[0] : "mixed";
      const requestTypes = Array.from(new Set(sorted.map((t) => t.request_type)));
      return { key, tasks: sorted, primary, groupStatus, requestTypes };
    }).sort((a, b) => b.primary.id - a.primary.id);
  }, [filteredTasks]);
  const singleGroups = useMemo(() => groupedByCall.filter((g) => g.tasks.length === 1), [groupedByCall]);
  const combinedGroups = useMemo(() => groupedByCall.filter((g) => g.tasks.length > 1), [groupedByCall]);
  const isCombinedView = taskView === "combined";
  const isSingleView = taskView === "single";
  const visibleGroups = useMemo(() => {
    if (taskView === "single") return singleGroups;
    if (taskView === "combined") return combinedGroups;
    return groupedByCall;
  }, [taskView, singleGroups, combinedGroups, groupedByCall]);

  return (
    <>
      <PageMeta title={`CliniqAI ${role} dashboard`} description={`CliniqAI ${role} operations dashboard`} />
      <div className="grid gap-6">
        <div className="rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-black">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-800 capitalize dark:text-white">{role} Dashboard</h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">Tasks created from live voice calls. Verify details on callback.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => void load()}
                className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-900 dark:border-gray-600 dark:bg-white dark:text-black"
              >
                Refresh
              </button>
              <select
                value={String(autoRefreshSec)}
                onChange={(e) => setAutoRefreshSec(Number(e.target.value))}
                className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-black dark:text-white"
              >
                <option value="0">Auto Off</option>
                <option value="30">Auto 30s</option>
                <option value="60">Auto 60s</option>
              </select>
            </div>
          </div>
        </div>

        {isStaffView ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black"><p className="text-xs text-gray-500 dark:text-gray-300">Pending</p><p className="text-3xl font-semibold text-gray-900 dark:text-white">{pending}</p></div>
            <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black"><p className="text-xs text-gray-500 dark:text-gray-300">In Progress</p><p className="text-3xl font-semibold text-gray-900 dark:text-white">{inProgress}</p></div>
            <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black"><p className="text-xs text-gray-500 dark:text-gray-300">Completed</p><p className="text-3xl font-semibold text-gray-900 dark:text-white">{completed}</p></div>
            <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black"><p className="text-xs text-gray-500 dark:text-gray-300">Escalation Rate</p><p className="text-3xl font-semibold text-gray-900 dark:text-white">{metrics?.escalation_rate == null ? "N/A" : `${Math.round(metrics.escalation_rate * 100)}%`}</p></div>
          </div>
        ) : null}

        {urgentEscalations.length > 0 ? (
          <div className="rounded-2xl border border-error-200 bg-error-50 p-4 dark:border-error-900 dark:bg-error-950/30">
            <h3 className="text-sm font-semibold text-error-700 dark:text-error-300">Urgent Escalations (Action Required)</h3>
            <div className="mt-2 grid gap-2">
              {urgentEscalations.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  onClick={() => jumpToUrgent(e.call_sid)}
                  className="w-full rounded-lg border border-error-200 bg-white/70 p-2.5 text-left text-xs dark:border-error-900 dark:bg-black/50"
                >
                  <p className="font-medium text-gray-900 dark:text-white">{humanizeEscalationReason(e.reason)}</p>
                  <p className="text-gray-700 dark:text-gray-200">Call Ref: {shortCallRef(e.call_sid)}</p>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Escalation Alert Center</h3>
            {escalationLoadFailed ? (
              <p className="mt-2 text-xs text-error-500">Could not load escalations. Check backend API connection.</p>
            ) : (
              <p className="mt-2 text-xs text-gray-600 dark:text-gray-300">
                No urgent escalations right now. New escalations will appear here automatically.
              </p>
            )}
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
          <div className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 rounded-lg border border-gray-200 p-1 dark:border-gray-800">
                <button
                  onClick={() => setTaskView("all")}
                  className={`rounded-lg px-3 py-2 text-sm ${taskView === "all" ? "bg-brand-500 text-white" : "bg-gray-100 dark:bg-gray-800 dark:text-white"}`}
                >
                  All Tasks ({groupedByCall.length})
                </button>
                <button
                  onClick={() => setTaskView("combined")}
                  className={`rounded-lg px-3 py-2 text-sm ${taskView === "combined" ? "bg-brand-500 text-white" : "bg-gray-100 dark:bg-gray-800 dark:text-white"}`}
                >
                  Combined Tasks ({combinedGroups.length})
                </button>
                <button
                  onClick={() => setTaskView("single")}
                  className={`rounded-lg px-3 py-2 text-sm ${taskView === "single" ? "bg-brand-500 text-white" : "bg-gray-100 dark:bg-gray-800 dark:text-white"}`}
                >
                  Single Tasks ({singleGroups.length})
                </button>
              </div>
              <div className="ml-auto flex items-center gap-2 rounded-lg border border-gray-200 p-1 dark:border-gray-800">
                <span className="px-2 text-xs font-semibold text-gray-500 dark:text-gray-400">Status</span>
                {(["all", "pending", "in_progress", "completed"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => setStatusFilter(s)}
                      className={`rounded-md px-4 py-2 text-sm font-semibold ${statusFilter === s ? "bg-gray-900 text-white dark:bg-white dark:text-black" : "bg-gray-100 dark:bg-gray-800 dark:text-white"}`}
                    >
                      {s === "all" ? "All" : statusLabel(s)}
                    </button>
                  ))}
              </div>
            </div>
            <p className="mb-3 text-xs text-gray-500 dark:text-gray-300">
              {isCombinedView
                ? `Showing ${combinedGroups.length} combined groups (merged by call).`
                : isSingleView
                ? `Showing ${singleGroups.length} single-task calls.`
                : `Showing ${visibleGroups.length} merged calls (single + combined).`}
            </p>
            {query.trim() ? (
              <p className="mb-3 text-xs text-gray-500 dark:text-gray-300">
                Filtered by <span className="font-medium uppercase">{field.replace("_", " ")}</span>: <span className="font-medium">{query.trim()}</span>
              </p>
            ) : null}

            {error ? <p className="mb-3 text-sm text-error-500">{error}</p> : null}
            {loading ? <p className="mb-3 text-sm text-gray-500">Loading...</p> : null}

            <div className="space-y-3">
              {visibleGroups.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500 dark:border-gray-700">
                  No tasks found for current view.
                </div>
              ) : (
                visibleGroups.map((group) => (
                  <div
                    key={`group-${group.key}`}
                    ref={(el) => {
                      if (group.primary.call_sid) taskCardRefs.current[group.primary.call_sid] = el;
                    }}
                    className={`rounded-xl border p-4 ${
                      group.requestTypes.includes("escalation")
                        ? "border-error-300 bg-error-50/60 dark:border-error-800 dark:bg-error-950/25"
                        : "border-gray-200 dark:border-gray-800"
                    } ${highlightedCallSid && group.primary.call_sid === highlightedCallSid ? "ring-2 ring-brand-500" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="text-gray-900 dark:text-white">
                        <p className="font-semibold capitalize">
                          {group.tasks.length > 1 ? "Combined Request" : group.primary.request_type.replace(/_/g, " ")}
                        </p>
                        <p className="text-xs text-gray-600 dark:text-gray-300">
                          {group.tasks.length > 1 ? `Contains ${group.tasks.length} tasks` : `Task #${group.primary.id}`}
                        </p>
                        {group.tasks.length > 1 ? (
                          <>
                            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
                              Tasks: {group.tasks.map((t) => `#${t.id}`).join(", ")}
                            </p>
                            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
                              Types: {group.requestTypes.map((t) => t.replace(/_/g, " ")).join(", ")}
                            </p>
                          </>
                        ) : null}
                      </div>
                      <span className={`rounded-full px-4 py-2 text-sm font-semibold ${badge(group.groupStatus)}`}>{statusLabel(group.groupStatus)}</span>
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-gray-900 dark:text-white sm:grid-cols-2">
                      <p><span className="text-gray-600 dark:text-gray-300">Patient:</span> {cleanPatientName(group.primary.patient_name)}</p>
                      <p><span className="text-gray-600 dark:text-gray-300">Callback:</span> {group.primary.callback_number || "N/A"}</p>
                      <p className="sm:col-span-2"><span className="text-gray-600 dark:text-gray-300">Call Ref:</span> {shortCallRef(group.primary.call_sid)}</p>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-3">
                      <button onClick={() => void onTranscript(group.primary.call_sid)} className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-900 dark:border-gray-300 dark:bg-white dark:text-black">Transcript</button>
                      <button onClick={() => void onGroupStatus(group.tasks.map((t) => t.id), "pending")} className="rounded-lg bg-warning-100 px-4 py-2 text-sm font-semibold text-warning-700">Pending</button>
                      <button onClick={() => void onGroupStatus(group.tasks.map((t) => t.id), "in_progress")} className="rounded-lg bg-blue-light-100 px-4 py-2 text-sm font-semibold text-blue-light-700">In Progress</button>
                      <button onClick={() => void onGroupStatus(group.tasks.map((t) => t.id), "completed")} className="rounded-lg bg-success-100 px-4 py-2 text-sm font-semibold text-success-700">Complete</button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="space-y-6 xl:sticky xl:top-24 xl:self-start">
            <div className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Transcript</h3>
                  <p className="mt-1 text-xs text-gray-500">{selectedSid ? `Call Ref: ${shortCallRef(selectedSid)}` : "Select a task to view transcript."}</p>
                </div>
                {selectedSid ? (
                  <button
                    onClick={() => {
                      setSelectedSid("");
                      setTranscript("");
                    }}
                    className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-900"
                    aria-label="Close transcript"
                    title="Close transcript"
                  >
                    X
                  </button>
                ) : null}
              </div>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-xs text-gray-900 dark:bg-black dark:text-white">{transcript || "No transcript loaded."}</pre>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-black">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Recent Escalations</h3>
              <div className="mt-3 space-y-2">
                {escalations.slice(0, 6).map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => jumpToUrgent(e.call_sid)}
                    className="w-full rounded-lg border border-error-200 bg-error-50 p-2.5 text-left text-xs dark:border-error-900 dark:bg-error-950/30"
                  >
                    <p className="font-medium text-gray-900 dark:text-white">{humanizeEscalationReason(e.reason)}</p>
                    <p className="text-gray-700 dark:text-gray-200">Call Ref: {shortCallRef(e.call_sid)}</p>
                  </button>
                ))}
                {escalations.length === 0 ? <p className="text-xs text-gray-500">No escalations yet.</p> : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
