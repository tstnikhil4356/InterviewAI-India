"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import toast from "react-hot-toast";

// ── Types ─────────────────────────────────────────────────────────────────────
type Phase =
  | "loading"
  | "begin"
  | "ai_speaking"
  | "ready"
  | "countering"
  | "processing_counter"
  | "recording"
  | "processing"
  | "done"
  | "terminated";

interface Turn {
  reaction: string;
  question: string;
  number: number;
  audio_b64: string | null;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function log(step: string, detail?: unknown) {
  detail !== undefined
    ? console.log(`[AUDIO] ${step}`, detail)
    : console.log(`[AUDIO] ${step}`);
}
function logErr(step: string, err: unknown) {
  console.error(`[AUDIO ERROR] ${step}`, err);
}

export default function InterviewRoomPage() {
  const { session_id } = useParams<{ session_id: string }>();
  const router = useRouter();

  const [phase, setPhase]               = useState<Phase>("loading");
  const [current, setCurrent]           = useState<Turn | null>(null);
  const [transcript, setTranscript]     = useState("");
  const [seconds, setSeconds]           = useState(0);
  const [counterText, setCounterText]   = useState("");
  const [aiCounter, setAiCounter]       = useState("");
  const [strikeCount, setStrikeCount]   = useState(0);
  const [strikeMsg, setStrikeMsg]       = useState<string | null>(null);
  const [termMsg, setTermMsg]           = useState("");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef        = useRef<Blob[]>([]);
  const streamRef        = useRef<MediaStream | null>(null);
  const timerRef         = useRef<NodeJS.Timeout | null>(null);
  const firstQRef        = useRef("");
  const recognitionRef   = useRef<SpeechRecognition | null>(null);

  // ── Play base64 audio ──────────────────────────────────────────────────────
  const playAudio = useCallback(async (b64: string): Promise<void> => {
    return new Promise((resolve) => {
      try {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const blob  = new Blob([bytes], { type: "audio/mpeg" });
        const url   = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
        audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
        audio.play().catch(() => { URL.revokeObjectURL(url); resolve(); });
      } catch (err) { logErr("playAudio", err); resolve(); }
    });
  }, []);

  const fetchAndPlay = useCallback(async (text: string): Promise<void> => {
    try {
      const res = await fetch(`${API}/api/interview/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.audio_b64) await playAudio(data.audio_b64);
    } catch (err) { logErr("fetchAndPlay", err); }
  }, [playAudio]);

  const speakTurn = useCallback(async (turn: Turn) => {
    setCurrent(turn);
    setTranscript("");
    setAiCounter("");
    setCounterText("");
    setStrikeMsg(null);
    setPhase("ai_speaking");
    const fullText = turn.reaction ? `${turn.reaction} ${turn.question}` : turn.question;
    await fetchAndPlay(fullText);
    setPhase("ready");
  }, [fetchAndPlay]);

  // ── Load session (skip the /session GET — question is in /start response) ──
  useEffect(() => {
    async function init() {
      try {
        // Try to retrieve from sessionStorage (passed from /interview page)
        const stored = sessionStorage.getItem(`session_${session_id}`);
        if (stored) {
          const data = JSON.parse(stored);
          firstQRef.current = data.question;
          setPhase("begin");
          return;
        }
        // Fallback: hit the /session endpoint
        const res = await fetch(`${API}/api/interview/session/${session_id}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        firstQRef.current = data.current_question;
        setPhase("begin");
      } catch (err) {
        logErr("session load", err);
        toast.error("Could not load interview session.");
        router.push("/interview");
      }
    }
    init();
  }, [session_id, router]);

  const handleBegin = useCallback(async () => {
    const q = firstQRef.current;
    if (!q) return;
    await speakTurn({ reaction: "", question: q, number: 1, audio_b64: null });
  }, [speakTurn]);

  // ── Timer ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (phase === "recording" || phase === "countering") {
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [phase]);

  // ── Recording ─────────────────────────────────────────────────────────────
  const startRecording = useCallback(async (targetPhase: "recording" | "countering") => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];

      const SR = window.SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SR) {
        const rec = new SR();
        rec.continuous = true;
        rec.interimResults = true;
        rec.lang = "en-IN";
        let final = "";
        rec.onresult = (e: SpeechRecognitionEvent) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const t = e.results[i][0].transcript;
            if (e.results[i].isFinal) final += t + " ";
            else interim = t;
          }
          if (targetPhase === "countering") setCounterText(final + interim);
          else setTranscript(final + interim);
        };
        rec.start();
        recognitionRef.current = rec;
      }

      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      mr.start(250);
      setPhase(targetPhase);
    } catch (err) {
      logErr("getUserMedia", err);
      toast.error("Microphone access denied.");
    }
  }, []);

  const stopRecorder = useCallback((): Promise<Blob> => {
    return new Promise((resolve) => {
      const mr = mediaRecorderRef.current;
      recognitionRef.current?.stop();
      if (!mr || mr.state === "inactive") { resolve(new Blob([])); return; }
      mr.onstop = () => resolve(new Blob(chunksRef.current, { type: "audio/webm" }));
      mr.stop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
    });
  }, []);

  const transcribeBlob = useCallback(async (blob: Blob): Promise<string> => {
    const fd = new FormData();
    fd.append("audio", blob, "answer.webm");
    const res = await fetch(`${API}/api/interview/transcribe`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`Transcribe ${res.status}`);
    const { transcript: text } = await res.json();
    return text || "";
  }, []);

  // ── Submit answer ─────────────────────────────────────────────────────────
  const stopAndSubmit = useCallback(async () => {
    setPhase("processing");
    const blob = await stopRecorder();
    try {
      const text = await transcribeBlob(blob);
      setTranscript(text);
      const res = await fetch(`${API}/api/interview/next-question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, transcript: text || "[no response]" }),
      });
      if (!res.ok) throw new Error(`next-question ${res.status}`);
      const next = await res.json();
      log("next-question", { done: next.done, terminated: next.terminated });

      // ── Termination ───────────────────────────────────────────────────────
      if (next.terminated) {
        setTermMsg(next.termination_message || "Interview terminated.");
        if (next.audio_b64) await playAudio(next.audio_b64);
        setPhase("terminated");
        return;
      }

      if (next.done) { setPhase("done"); return; }

      // ── Strike warning ────────────────────────────────────────────────────
      if (next.strike_issued) {
        setStrikeCount(next.misbehavior_count ?? strikeCount + 1);
        setStrikeMsg(
          `⚠ Warning ${next.misbehavior_count}/3 — Your answer was flagged as ${next.answer_quality}.`
        );
        toast.error(`Strike ${next.misbehavior_count} issued — unacceptable answer!`, { duration: 4000 });
      }

      await speakTurn({
        reaction:  next.reaction ?? "",
        question:  next.question,
        number:    next.question_number,
        audio_b64: null,
      });
    } catch (err) {
      logErr("stopAndSubmit", err);
      toast.error("Something went wrong. Try again.");
      setPhase("ready");
    }
  }, [session_id, speakTurn, stopRecorder, transcribeBlob, playAudio, strikeCount]);

  // ── Submit counter ────────────────────────────────────────────────────────
  const stopAndCounter = useCallback(async () => {
    setPhase("processing_counter");
    const blob = await stopRecorder();
    try {
      const text = await transcribeBlob(blob);
      setCounterText(text);
      const res = await fetch(`${API}/api/interview/counter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, counter_text: text || "[no response]" }),
      });
      if (!res.ok) throw new Error(`counter ${res.status}`);
      const data = await res.json();
      setAiCounter(data.ai_response);
      if (data.audio_b64) await playAudio(data.audio_b64);
      setPhase("ready");
    } catch (err) {
      logErr("stopAndCounter", err);
      toast.error("Something went wrong.");
      setPhase("ready");
    }
  }, [session_id, playAudio, stopRecorder, transcribeBlob]);

  const fmt = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  const isActive = ["ai_speaking","ready","recording","countering","processing","processing_counter"].includes(phase);

  // ── Strike indicator component ────────────────────────────────────────────
  const StrikeIndicator = () => (
    <div style={{ position: "fixed", top: 16, right: 16, display: "flex", gap: 6, zIndex: 50 }}>
      {[1, 2, 3].map((n) => (
        <div key={n} style={{
          width: 10, height: 10, borderRadius: "50%",
          background: n <= strikeCount ? "#ef4444" : "rgba(255,255,255,0.1)",
          border: n <= strikeCount ? "none" : "1px solid rgba(255,255,255,0.2)",
          boxShadow: n <= strikeCount ? "0 0 8px #ef4444" : "none",
          transition: "all 0.3s",
        }} />
      ))}
    </div>
  );

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white flex flex-col">

      <StrikeIndicator />

      {/* Header */}
      <header className="px-6 py-4 flex items-center justify-between border-b border-white/[0.06]">
        <span className="text-xs font-semibold tracking-[0.15em] text-white/30 uppercase">Interview Room</span>
        {current && phase !== "begin" && (
          <span className="text-xs text-white/25 tabular-nums">Q{current.number}</span>
        )}
      </header>

      <main className="flex-1 flex flex-col items-center justify-center px-4 gap-8 py-10 max-w-2xl mx-auto w-full">

        {/* Strike warning banner */}
        {strikeMsg && (
          <div style={{
            width: "100%", padding: "12px 18px", background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.35)", borderRadius: 12,
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontSize: 16 }}>⚠️</span>
            <p style={{ fontSize: 13, color: "#fca5a5", fontWeight: 500 }}>{strikeMsg}</p>
          </div>
        )}

        {/* Loading */}
        {phase === "loading" && (
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-white/10 border-t-white/50 rounded-full animate-spin" />
            <p className="text-white/30 text-sm">Preparing your interview…</p>
          </div>
        )}

        {/* Begin */}
        {phase === "begin" && (
          <div className="flex flex-col items-center gap-8 text-center max-w-sm">
            <div className="w-20 h-20 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-4xl">🤖</div>
            <div>
              <h2 className="text-xl font-semibold text-white mb-2">Ready to begin?</h2>
              <p className="text-white/40 text-sm leading-relaxed">
                The interviewer is strict and direct.<br />
                Answers are flagged if evasive or incoherent.<br />
                3 strikes = interview terminated.
              </p>
            </div>
            <button onClick={handleBegin}
              className="px-8 py-3.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl transition-colors text-sm">
              Begin interview
            </button>
          </div>
        )}

        {/* Active interview */}
        {isActive && current && (
          <>
            {/* AI avatar */}
            <div className="flex flex-col items-center gap-4">
              <div className="relative">
                {phase === "ai_speaking" && (
                  <>
                    <span className="absolute inset-0 rounded-full bg-indigo-500/20 animate-ping" />
                    <span className="absolute inset-[-10px] rounded-full border border-indigo-400/15 animate-pulse" />
                  </>
                )}
                <div className={`w-20 h-20 rounded-full flex items-center justify-center text-3xl border transition-all duration-300
                  ${phase === "ai_speaking" ? "bg-indigo-500/20 border-indigo-400/40 scale-110" : "bg-white/5 border-white/10"}`}>
                  🤖
                </div>
              </div>
              <p className="text-xs tracking-widest uppercase text-white/25">
                {phase === "ai_speaking" ? "Speaking" : "Interviewer"}
              </p>
            </div>

            {/* Reaction */}
            {current.reaction && !aiCounter && (
              <p className="text-white/50 text-base italic text-center max-w-md">
                "{current.reaction}"
              </p>
            )}

            {/* AI counter-response */}
            {aiCounter && (
              <div className="w-full bg-white/5 border border-white/10 rounded-xl p-4 text-center">
                <p className="text-xs text-white/30 uppercase tracking-widest mb-2">Interviewer replied</p>
                <p className="text-white/80 text-sm leading-relaxed italic">"{aiCounter}"</p>
              </div>
            )}

            {/* Question */}
            <div className="w-full text-center">
              <p className="text-white text-xl leading-relaxed font-medium">{current.question}</p>
            </div>

            {/* Live transcript */}
            {(phase === "recording" || phase === "countering") && (
              <div className="w-full bg-white/[0.03] border border-white/10 rounded-xl p-4 min-h-[60px]">
                <p className="text-xs text-white/25 mb-1 uppercase tracking-widest">
                  {phase === "countering" ? "Your pushback" : "Your answer"}
                </p>
                <p className="text-white/60 text-sm leading-relaxed">
                  {phase === "countering" ? (counterText || "Listening...") : (transcript || "Listening...")}
                </p>
              </div>
            )}

            <div className="w-px h-6 bg-white/10" />

            {/* Controls */}
            <div className="flex flex-col items-center gap-4 w-full">

              {phase === "ready" && (
                <div className="flex flex-col items-center gap-5 w-full">
                  <div className="flex flex-col items-center gap-2">
                    <button onClick={() => startRecording("recording")}
                      className="w-16 h-16 rounded-full bg-white/5 hover:bg-indigo-500/20 border border-white/10 hover:border-indigo-400/40 flex items-center justify-center text-2xl transition-all duration-200 active:scale-95">
                      🎙️
                    </button>
                    <p className="text-white/30 text-xs">Tap to answer</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="h-px bg-white/10 w-16" />
                    <span className="text-white/20 text-xs">or</span>
                    <div className="h-px bg-white/10 w-16" />
                  </div>
                  <button onClick={() => startRecording("countering")}
                    className="px-5 py-2 rounded-xl bg-white/5 hover:bg-amber-500/10 border border-white/10 hover:border-amber-400/30 text-white/40 hover:text-amber-300 text-xs font-medium transition-all duration-200">
                    ↩ Push back on question/reaction
                  </button>
                </div>
              )}

              {phase === "recording" && (
                <div className="flex flex-col items-center gap-3">
                  <button onClick={stopAndSubmit}
                    className="w-16 h-16 rounded-full bg-red-500/20 hover:bg-red-500/30 border border-red-400/40 flex items-center justify-center text-2xl transition-all active:scale-95">
                    ⏹️
                  </button>
                  <p className="text-white/30 text-sm flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse inline-block" />
                    {fmt(seconds)} — tap to submit
                  </p>
                </div>
              )}

              {phase === "countering" && (
                <div className="flex flex-col items-center gap-3">
                  <button onClick={stopAndCounter}
                    className="w-16 h-16 rounded-full bg-amber-500/20 hover:bg-amber-500/30 border border-amber-400/40 flex items-center justify-center text-2xl transition-all active:scale-95">
                    ⏹️
                  </button>
                  <p className="text-amber-300/60 text-sm flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse inline-block" />
                    {fmt(seconds)} — tap to send
                  </p>
                </div>
              )}

              {(phase === "processing" || phase === "processing_counter" || phase === "ai_speaking") && (
                <div className="flex flex-col items-center gap-3">
                  {(phase === "processing" || phase === "processing_counter") && (
                    <div className="w-16 h-16 rounded-full bg-white/5 border border-white/10 flex items-center justify-center">
                      <div className="w-5 h-5 border-2 border-white/20 border-t-white/60 rounded-full animate-spin" />
                    </div>
                  )}
                  {phase === "ai_speaking" && (
                    <div className="w-16 h-16 rounded-full bg-white/5 border border-white/10 opacity-30 flex items-center justify-center text-2xl">🎙️</div>
                  )}
                  <p className="text-white/30 text-sm">
                    {phase === "processing" && "Processing your answer…"}
                    {phase === "processing_counter" && "Thinking…"}
                    {phase === "ai_speaking" && "Listen carefully"}
                  </p>
                </div>
              )}
            </div>
          </>
        )}

        {/* Terminated */}
        {phase === "terminated" && (
          <div className="flex flex-col items-center gap-6 text-center max-w-sm">
            <div className="text-5xl">🚫</div>
            <div>
              <h2 className="text-xl font-semibold text-red-400 mb-3">Interview Terminated</h2>
              <p className="text-white/60 text-sm leading-relaxed italic mb-2">"{termMsg}"</p>
              <p className="text-white/30 text-xs">Your responses were flagged {strikeCount} time{strikeCount !== 1 ? "s" : ""}.</p>
            </div>
            <div className="flex flex-col gap-3 w-full">
              <button onClick={() => router.push(`/interview/${session_id}/report`)}
                style={{ padding: "12px 24px", background: "#ef4444", color: "white", fontWeight: 600, fontSize: 14, borderRadius: 12, border: "none", cursor: "pointer" }}>
                View Report (Terminated) →
              </button>
              <button onClick={() => router.push("/interview")}
                className="text-white/30 text-sm hover:text-white/50">
                Try again
              </button>
            </div>
          </div>
        )}

        {/* Done */}
        {phase === "done" && (
          <div className="flex flex-col items-center gap-6 text-center max-w-sm">
            <div className="text-5xl">✓</div>
            <div>
              <h2 className="text-xl font-semibold text-white mb-2">Interview complete</h2>
              <p className="text-white/40 text-sm leading-relaxed">Your performance report is ready.</p>
            </div>
            {/* ✅ Fixed: correct route is /interview/[session_id]/report */}
            <button onClick={() => router.push(`/interview/${session_id}/report`)}
              className="px-7 py-3 bg-white text-black text-sm font-semibold rounded-xl hover:bg-white/90 transition-colors">
              View report →
            </button>
          </div>
        )}

      </main>
    </div>
  );
}