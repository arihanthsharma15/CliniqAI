const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export async function sendMessage(
sessionId: string,
message: string
): Promise<{ reply: string; audio_urls: string[]; ended?: boolean }>{
const res = await fetch(`${API_BASE}/api/calls/web-chat`, {
method: "POST",
headers: {
"Content-Type": "application/json",
},
body: JSON.stringify({
session_id: sessionId,
message,
}),
});

return await res.json();
}
