import { useState, useRef, useEffect } from "react";
import { startCall, sendMessage } from "../../services/simulateApi";

type Message = {
  role: "bot" | "patient";
  text: string;
};

function generateCallSid(): string {
  return Math.random().toString(36).substring(2, 18).toUpperCase();
}

async function playAudioUrl(url: string): Promise<void> {
  return new Promise((resolve) => {
    const audio = new Audio(url);
    audio.onended = () => resolve();
    audio.onerror = () => resolve();
    audio.play().catch(() => resolve());
  });
}

export default function SimulatePage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [callSid] = useState(() => generateCallSid());
  const [started, setStarted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [botTyping, setBotTyping] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, botTyping]);

  async function handleStart() {
    setLoading(true);
    try {
      const audioUrl = await startCall(callSid);
      setStarted(true);
      setBotTyping(true);
      await new Promise((r) => setTimeout(r, 800));
      setBotTyping(false);
      setMessages([{ role: "bot", text: "Thank you for calling ClinIQ. How can I help you today?" }]);
      if (audioUrl) playAudioUrl(audioUrl);
    } catch {
      alert("Could not connect to backend. Make sure VITE_API_BASE is set correctly.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSend() {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "patient", text: userMsg }]);
    setBotTyping(true);
    setLoading(true);

    try {
      const audioUrls = await sendMessage(callSid, userMsg);
      await new Promise((r) => setTimeout(r, 600));
      setBotTyping(false);

      // Play audio and show a generic bot response label
      // (TTS audio carries the actual response)
      if (audioUrls.length > 0) {
        setMessages((prev) => [...prev, { role: "bot", text: "🔊 Bot is responding..." }]);
        for (const url of audioUrls) {
          await playAudioUrl(url);
        }
      } else {
        setMessages((prev) => [...prev, { role: "bot", text: "..." }]);
      }
    } catch {
      setBotTyping(false);
      setMessages((prev) => [...prev, { role: "bot", text: "⚠️ Error reaching backend." }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center p-4 font-mono">
      {/* Header */}
      <div className="w-full max-w-2xl mb-4">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-emerald-400 text-sm tracking-widest uppercase">ClinIQ AI — Demo Simulator</span>
        </div>
        <p className="text-gray-500 text-xs mt-1">
          Simulates a live patient call. Audio plays through browser. Data saves to production DB.
        </p>
      </div>

      {/* Chat window */}
      <div className="w-full max-w-2xl bg-gray-900 border border-gray-800 rounded-2xl flex flex-col overflow-hidden shadow-2xl">
        {/* Call info bar */}
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <span className="text-gray-400 text-xs">Call SID: <span className="text-gray-200">{callSid}</span></span>
          <span className={`text-xs px-2 py-0.5 rounded-full ${started ? "bg-emerald-900 text-emerald-400" : "bg-gray-800 text-gray-500"}`}>
            {started ? "● Connected" : "○ Not started"}
          </span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[400px] max-h-[500px]">
          {!started && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div className="text-4xl mb-4">📞</div>
                <p className="text-gray-400 text-sm">Press "Start Call" to begin a simulated patient call</p>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "patient" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-xs lg:max-w-md px-4 py-2 rounded-2xl text-sm ${
                msg.role === "patient"
                  ? "bg-blue-600 text-white rounded-br-sm"
                  : "bg-gray-800 text-gray-100 rounded-bl-sm"
              }`}>
                <div className="text-xs mb-1 opacity-60">
                  {msg.role === "patient" ? "You (Patient)" : "🤖 ClinIQ Bot"}
                </div>
                {msg.text}
              </div>
            </div>
          ))}

          {botTyping && (
            <div className="flex justify-start">
              <div className="bg-gray-800 text-gray-400 px-4 py-2 rounded-2xl rounded-bl-sm text-sm">
                <div className="text-xs mb-1 opacity-60">🤖 ClinIQ Bot</div>
                <span className="animate-pulse">● ● ●</span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input area */}
        <div className="p-4 border-t border-gray-800">
          {!started ? (
            <button
              onClick={handleStart}
              disabled={loading}
              className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white py-3 rounded-xl text-sm tracking-wide transition-colors"
            >
              {loading ? "Connecting..." : "📞 Start Call"}
            </button>
          ) : (
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSend()}
                placeholder="Type as patient..."
                disabled={loading}
                className="flex-1 bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-500 px-4 py-2 rounded-xl text-sm focus:outline-none focus:border-emerald-500 transition-colors"
              />
              <button
                onClick={handleSend}
                disabled={loading || !input.trim()}
                className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-4 py-2 rounded-xl text-sm transition-colors"
              >
                Send
              </button>
            </div>
          )}
        </div>
      </div>

      <p className="text-gray-600 text-xs mt-4">
        Audio plays via browser • Tasks appear live on{" "}
        <a href="/dashboard/staff" className="text-emerald-600 hover:underline">Staff Dashboard</a>
      </p>
    </div>
  );
}