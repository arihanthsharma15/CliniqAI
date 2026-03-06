const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export async function startCall(callSid: string): Promise<string> {
  const form = new URLSearchParams();
  form.append("CallSid", callSid);
  form.append("From", "+919999999999");
  form.append("To", "+911111111111");
  form.append("CallStatus", "ringing");

  const res = await fetch(`${API_BASE}/api/calls/webhook`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });

  const text = await res.text();
  return extractAudioUrl(text);
}

export async function sendMessage(callSid: string, message: string): Promise<string[]> {
  const form = new URLSearchParams();
  form.append("CallSid", callSid);
  form.append("SpeechResult", message);
  form.append("Confidence", "0.95");

  const res = await fetch(`${API_BASE}/api/calls/collect`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });

  const text = await res.text();
  return extractAllAudioUrls(text);
}

function extractAudioUrl(twiml: string): string {
  const match = twiml.match(/<Play>(.*?)<\/Play>/);
  return match ? match[1] : "";
}

function extractAllAudioUrls(twiml: string): string[] {
  const matches = [...twiml.matchAll(/<Play>(.*?)<\/Play>/g)];
  return matches.map((m) => m[1]);
}