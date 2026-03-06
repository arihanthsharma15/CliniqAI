import { useState, useRef, useEffect } from "react";
import { Link } from "react-router";
import { useNavigate } from "react-router";
import { EyeCloseIcon, EyeIcon } from "../../icons";
import Label from "../form/Label";
import Input from "../form/input/InputField";
import Checkbox from "../form/input/Checkbox";
import { demoAccounts, useAuth } from "../../context/AuthContext";
import { sendMessage } from "../../services/simulateApi";
type Message = { role: "bot" | "patient"; text: string };

function generateCallSid(): string {
  return Math.random().toString(36).substring(2, 18).toUpperCase();
}

function speakText(text: string): void {
  if (!('speechSynthesis' in window)) {
    console.log("🔇 Speech synthesis not supported");
    return;
  }
  
  try {
    // Cancel any ongoing speech
    window.speechSynthesis.cancel();
    
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1;
    utterance.pitch = 1;
    utterance.volume = 1;
    
    console.log("🗣️  Speaking text:", text);
    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.error("❌ Speech synthesis failed:", err);
  }
}

async function playAudioUrl(url: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      console.log("📢 Starting audio playback:", url);
      const audio = new Audio(url);

      audio.volume = 1.0;
      audio.crossOrigin = "anonymous";

      audio.onplay = () => {
        console.log("▶️  Audio play() called successfully");
      };

      audio.onended = () => {
        console.log("✅ Audio playback completed");
        resolve();
      };

      audio.onerror = (e) => {
        console.error("❌ Audio failed to load:", url, e);
        resolve();
      };

      audio.play()
        .then(() => {
          console.log("▶️  Audio play() promise resolved");
        })
        .catch((err) => {
          console.error("❌ Audio play() failed:", err);
          resolve();
        });

      // Timeout safety - resolve after max duration
      setTimeout(() => {
        console.log("⏱️  Audio playback timeout (assumed complete)");
        resolve();
      }, 15000);
    } catch (err) {
      console.error("❌ Error creating audio:", err);
      resolve();
    }
  });
}



function SimulateModal({ onClose }: { onClose: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [callSid, setCallSid] = useState(() => generateCallSid());
  const [started, setStarted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [botTyping, setBotTyping] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, botTyping]);

  async function handleStart() {
  setMessages([]);
  setInput("");
  const newCallSid = generateCallSid();
  setCallSid(newCallSid);
  setLoading(true);
  try {
    setStarted(true);
    setBotTyping(true);

    await new Promise((r) => setTimeout(r, 800));

    // Send empty/start message to initialize context and get greeting with audio
    try {
      const data = await sendMessage(newCallSid, "");
      setBotTyping(false);
      
      const greetingText = data.reply || "Thank you for calling ClinIQ. How can I help you today?";
      setMessages([
        {
          role: "bot",
          text: greetingText
        }
      ]);

      // Play audio if available, otherwise use text-to-speech fallback
      if (data.audio_urls?.length > 0) {
        console.log("🎵 Playing audio URLs");
        for (const url of data.audio_urls) {
          await playAudioUrl(url);
        }
      } else {
        console.log("🔊 No audio URLs, using text-to-speech fallback");
        speakText(greetingText);
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch (audioErr) {
      console.error("Failed to initialize greeting:", audioErr);
      setBotTyping(false);
      setMessages([
        {
          role: "bot",
          text: "Thank you for calling ClinIQ. How can I help you today?"
        }
      ]);
      speakText("Thank you for calling ClinIQ. How can I help you today?");
    }
  } catch {
    alert("Could not connect to backend.");
  } finally {
    setLoading(false);
  }
}

async function handleSend() {
  if (!input.trim() || loading || botTyping) return;

  const userMsg = input.trim();

  setInput("");
  setMessages((prev) => [...prev, { role: "patient", text: userMsg }]);

  setBotTyping(true);
  setLoading(true);

  try {
    const data = await sendMessage(callSid, userMsg);

    await new Promise((r) => setTimeout(r, 600));

    setBotTyping(false);

    setMessages((prev) => [
      ...prev,
      {
        role: "bot",
        text: data.reply || "..."
      }
    ]);

    // Play audio if available, otherwise use text-to-speech fallback
    if (data.audio_urls?.length > 0) {
      console.log("🎵 Playing audio URLs");
      for (const url of data.audio_urls) {
        await playAudioUrl(url);
      }
    } else if (data.reply) {
      console.log("🔊 No audio URLs, using text-to-speech fallback");
      speakText(data.reply);
      await new Promise((r) => setTimeout(r, 1500));
    }

    if (data.ended) {
      setStarted(false);
      setCallSid(generateCallSid());
    }
  } catch {
    setBotTyping(false);

    setMessages((prev) => [
      ...prev,
      {
        role: "bot",
        text: "⚠️ Error reaching backend."
      }
    ]);
  } finally {
    setLoading(false);
  }
}

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-gray-950 border border-gray-800 rounded-2xl flex flex-col overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-emerald-400 text-xs tracking-widest uppercase font-mono">ClinIQ — Patient Simulator</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-lg leading-none">✕</button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-72 max-h-96 font-mono">
          {!started && (
            <div className="flex items-center justify-center h-full min-h-60">
              <div className="text-center">
                <div className="text-4xl mb-3">📞</div>
                <p className="text-gray-400 text-sm">Press "Start Call" to simulate a patient call</p>
                <p className="text-gray-600 text-xs mt-1">Audio plays through your browser</p>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "patient" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-xs px-3 py-2 rounded-xl text-xs ${
                msg.role === "patient"
                  ? "bg-blue-600 text-white rounded-br-sm"
                  : "bg-gray-800 text-gray-100 rounded-bl-sm"
              }`}>
                <div className="opacity-50 mb-1">{msg.role === "patient" ? "You (Patient)" : "🤖 ClinIQ Bot"}</div>
                {msg.text}
              </div>
            </div>
          ))}

          {botTyping && (
            <div className="flex justify-start">
              <div className="bg-gray-800 text-gray-400 px-3 py-2 rounded-xl rounded-bl-sm text-xs">
                <div className="opacity-50 mb-1">🤖 ClinIQ Bot</div>
                <span className="animate-pulse">● ● ●</span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="p-3 border-t border-gray-800">
          {!started ? (
            <button
              onClick={handleStart}
              disabled={loading}
              className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white py-2.5 rounded-xl text-sm transition-colors"
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
                className="flex-1 bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-500 px-3 py-2 rounded-xl text-sm focus:outline-none focus:border-emerald-500 transition-colors font-mono"
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
    </div>
  );
}

export default function SignInForm() {
  const [showPassword, setShowPassword] = useState(false);
  const [isChecked, setIsChecked] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [showSimulate, setShowSimulate] = useState(false);
  const navigate = useNavigate();
  const { signIn } = useAuth();

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const result = signIn(email, password);
    if (!result.ok) {
      setError(result.error || "Sign in failed.");
      return;
    }
    navigate("/");
  }

  return (
    <>
      {showSimulate && <SimulateModal onClose={() => setShowSimulate(false)} />}

      <div className="flex flex-col flex-1">
        <div className="w-full max-w-md pt-10 mx-auto" />
        <div className="flex flex-col justify-center flex-1 w-full max-w-md mx-auto">
          <div>
            <div className="mb-5 sm:mb-8">
              <h1 className="mb-2 font-semibold text-gray-800 text-title-sm dark:text-white/90 sm:text-title-md">
                Sign In to CliniqAI
              </h1>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Use one of the demo accounts below.
              </p>
            </div>
            <div>
              <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-800 dark:bg-black">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-300">Demo Accounts</p>
                <div className="space-y-2 text-sm">
                  {demoAccounts.map((acc) => (
                    <button
                      key={acc.email}
                      type="button"
                      onClick={() => {
                        setEmail(acc.email);
                        setPassword(acc.password);
                      }}
                      className="flex w-full items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-left hover:bg-white dark:border-gray-700 dark:hover:bg-gray-900"
                    >
                      <span className="font-medium text-gray-700 dark:text-white">{acc.role}</span>
                      <span className="text-gray-500 dark:text-gray-300">{acc.email}</span>
                    </button>
                  ))}
                </div>
              </div>

              <form onSubmit={onSubmit}>
                <div className="space-y-6">
                  <div>
                    <Label>
                      Email <span className="text-error-500">*</span>{" "}
                    </Label>
                    <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="staff@cliniqai.demo" />
                  </div>
                  <div>
                    <Label>
                      Password <span className="text-error-500">*</span>{" "}
                    </Label>
                    <div className="relative">
                      <Input
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        type={showPassword ? "text" : "password"}
                        placeholder="Enter your password"
                      />
                      <span
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute z-30 -translate-y-1/2 cursor-pointer right-4 top-1/2"
                      >
                        {showPassword ? (
                          <EyeIcon className="fill-gray-500 dark:fill-gray-400 size-5" />
                        ) : (
                          <EyeCloseIcon className="fill-gray-500 dark:fill-gray-400 size-5" />
                        )}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Checkbox checked={isChecked} onChange={setIsChecked} />
                      <span className="block font-normal text-gray-700 text-theme-sm dark:text-gray-400">
                        Keep me logged in
                      </span>
                    </div>
                    <Link
                      to="/reset-password"
                      className="text-sm text-brand-500 hover:text-brand-600 dark:text-brand-400"
                    >
                      Forgot password?
                    </Link>
                  </div>
                  <div>
                    <button
                      type="submit"
                      className="flex w-full items-center justify-center rounded-lg bg-brand-500 px-4 py-3 text-sm font-medium text-white shadow-theme-xs transition hover:bg-brand-600"
                    >
                      Sign in
                    </button>
                  </div>
                  {error ? <p className="text-sm text-error-500">{error}</p> : null}
                </div>
              </form>

              {/* Try Demo Button */}
              <div className="mt-4">
              <button
                type="button"
                onClick={() => setShowSimulate(true)}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-600 px-4 py-3 text-sm font-medium text-emerald-500 transition hover:bg-emerald-600/10"
              >
               📞 Try as Patient (Live Demo)
              </button>

              <p className="mt-2 text-center text-xs text-gray-500 dark:text-gray-400">
                 Live browser simulation for full patient interaction
                </p>
            </div>
              <div className="mt-5">
                <p className="text-sm font-normal text-center text-gray-700 dark:text-gray-400 sm:text-start">
                  Don&apos;t have an account? {""}
                  <Link
                    to="/signup"
                    className="text-brand-500 hover:text-brand-600 dark:text-brand-400"
                  >
                    Sign Up
                  </Link>
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}