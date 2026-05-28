"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import toast from "react-hot-toast";

// ── Types ─────────────────────────────────────────────────────────────────────
type Phase =
  | "loading"
  | "begin"
  | "ai_speaking"
  | "recording"
  | "processing"
  | "processing_counter"
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
  const [aiCounter, setAiCounter]       = useState("");
  const [strikeCount, setStrikeCount]   = useState(0);
  const [strikeMsg, setStrikeMsg]       = useState<string | null>(null);
  const [termMsg, setTermMsg]           = useState("");

  // ── Anti-Cheat / Screen Lock State ──
  const [isLockedOut, setIsLockedOut]   = useState(false);
  const [cheatCount, setCheatCount]     = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef        = useRef<Blob[]>([]);
  const streamRef        = useRef<MediaStream | null>(null);
  const timerRef         = useRef<NodeJS.Timeout | null>(null);
  const firstQRef        = useRef("");
  const recognitionRef   = useRef<any>(null);

  // Audio references for forceful stopping
  const currentAudioRef  = useRef<HTMLAudioElement | null>(null);
  const audioResolveRef  = useRef<(() => void) | null>(null);
  const lastCheatTimeRef = useRef<number>(0);

  // ── Play base64 audio ──────────────────────────────────────────────────────
  const playAudio = useCallback(async (b64: string): Promise<void> => {
    return new Promise((resolve) => {
      try {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const blob  = new Blob([bytes], { type: "audio/mpeg" });
        const url   = URL.createObjectURL(blob);
        const audio = new Audio(url);

        currentAudioRef.current = audio;
        audioResolveRef.current = resolve;

        const cleanup = () => {
          URL.revokeObjectURL(url);
          if (currentAudioRef.current === audio) currentAudioRef.current = null;
          if (audioResolveRef.current === resolve) audioResolveRef.current = null;
          resolve();
        };

        audio.onended = cleanup;
        audio.onerror = cleanup;
        audio.play().catch(cleanup);
      } catch (err) { logErr("playAudio", err); resolve(); }
    });
  }, []);

  const stopAudio = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    if (audioResolveRef.current) {
      audioResolveRef.current(); // Resolve promise so the app doesn't hang
      audioResolveRef.current = null;
    }
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

  // ── Recording ─────────────────────────────────────────────────────────────
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];

      const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
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
          setTranscript(final + interim);
        };
        rec.start();
        recognitionRef.current = rec;
      }

      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      mr.start(250);
      setPhase("recording");
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

  // ── AI Speaking Flow ──────────────────────────────────────────────────────
  const speakTurn = useCallback(async (turn: Turn) => {
    setCurrent(turn);
    setTranscript("");
    setAiCounter("");
    setStrikeMsg(null);
    setPhase("ai_speaking");

    const fullText = turn.reaction ? `${turn.reaction} ${turn.question}` : turn.question;
    await fetchAndPlay(fullText);

    await startRecording();
  }, [fetchAndPlay, startRecording]);

  // ── Load session ─────────────────────────────────────────────────────────
  useEffect(() => {
    async function init() {
      try {
        const res = await fetch(`${API}/api/interview/session/${session_id}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();

        if (data.status !== "active") {
          toast.error("This interview has already ended.");
          router.push(`/interview/${session_id}/report`);
          return;
        }

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
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      }
    } catch (err) {
      console.warn("Fullscreen API not supported.", err);
    }

    const q = firstQRef.current;
    if (!q) return;
    await speakTurn({ reaction: "", question: q, number: 1, audio_b64: null });
  }, [speakTurn]);

  // ── Anti-Cheat Observers (Tab Switch & Escape) ───────────────────────────
  const triggerCheat = useCallback(() => {
    const now = Date.now();
    // Debounce by 1 second (prevents Alt+Tab firing visibility & fullscreen simultaneously)
    if (now - lastCheatTimeRef.current < 1000) return;
    lastCheatTimeRef.current = now;

    if (isLockedOut) return;

    // Immediately mute AI voice if they try to cheat while it's speaking
    stopAudio();

    setCheatCount((prev) => {
      const newCount = prev + 1;

      if (newCount >= 3) {
        const reasonStr = "Interview automatically terminated due to multiple tab switches or exiting full-screen.";
        setTermMsg(reasonStr);
        setPhase("terminated");
        if (document.fullscreenElement) document.exitFullscreen();

        // Notify backend to end the session early and provide the reason!
        fetch(`${API}/api/interview/end`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id,
            reason: reasonStr
          })
        }).catch(() => {});

      } else {
        setIsLockedOut(true);
      }
      return newCount;
    });
  }, [isLockedOut, session_id, stopAudio]);

  useEffect(() => {
    const isActivePhase = ["ai_speaking", "recording", "processing", "processing_counter"].includes(phase);
    if (!isActivePhase) return;

    const handleVisibilityChange = () => {
      if (document.hidden) triggerCheat();
    };

    const handleFullscreenChange = () => {
      if (!document.fullscreenElement && phase !== "terminated" && phase !== "done") {
        triggerCheat();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    document.addEventListener("fullscreenchange", handleFullscreenChange);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
    };
  }, [phase, triggerCheat]);

  const returnToInterview = async () => {
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      }
    } catch (err) {
      console.warn("Fullscreen request failed", err);
    }
    setIsLockedOut(false);
  };

  // ── Timer ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (phase === "recording") {
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [phase]);

  // ── Submit answer ─────────────────────────────────────────────────────────
  const stopAndSubmit = useCallback(async () => {
    if (isLockedOut) return;

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

      if (res.status === 400) {
        toast.error("Interview session is no longer active.");
        router.push(`/interview/${session_id}/report`);
        return;
      }

      if (!res.ok) throw new Error(`next-question ${res.status}`);
      const next = await res.json();

      if (next.terminated) {
        setTermMsg(next.termination_message || "Interview terminated.");
        if (next.audio_b64) await playAudio(next.audio_b64);
        setPhase("terminated");
        if (document.fullscreenElement) document.exitFullscreen();
        return;
      }

      if (next.done) {
        setPhase("done");
        if (document.fullscreenElement) document.exitFullscreen();
        return;
      }

      if (next.strike_issued) {
        setStrikeCount(next.misbehavior_count ?? strikeCount + 1);
        setStrikeMsg(`⚠ Warning ${next.misbehavior_count}/4 — Your answer was flagged as ${next.answer_quality}.`);
        toast.error(`Strike ${next.misbehavior_count} issued!`, { duration: 4000 });
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
      startRecording();
    }
  }, [session_id, speakTurn, stopRecorder, transcribeBlob, playAudio, strikeCount, startRecording, router, isLockedOut]);

  // ── Submit counter ────────────────────────────────────────────────────────
  const stopAndCounter = useCallback(async () => {
    if (isLockedOut) return;

    setPhase("processing_counter");
    const blob = await stopRecorder();
    try {
      const text = await transcribeBlob(blob);
      setTranscript(text);
      const res = await fetch(`${API}/api/interview/counter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, counter_text: text || "[no response]" }),
      });
      if (!res.ok) throw new Error(`counter ${res.status}`);
      const data = await res.json();
      setAiCounter(data.ai_response);

      if (data.audio_b64) await playAudio(data.audio_b64);

      await startRecording();
    } catch (err) {
      logErr("stopAndCounter", err);
      toast.error("Something went wrong.");
      startRecording();
    }
  }, [session_id, playAudio, stopRecorder, transcribeBlob, startRecording, isLockedOut]);

  // ── Keyboard Controls ─────────────────────────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (phase !== "recording" || isLockedOut) return;

      if (e.code === "Space" || e.code === "Enter") {
        e.preventDefault();
        stopAndSubmit();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [phase, stopAndSubmit, isLockedOut]);

  const fmt = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  const isActive = ["ai_speaking","recording","processing","processing_counter"].includes(phase);

  // ── Strike indicator component ────────────────────────────────────────────
  const StrikeIndicator = () => (
    <div className="fixed top-4 right-4 z-50 flex gap-2">
      {[1, 2, 3, 4].map((n) => {
        const active = n <= strikeCount;
        return (
          <div key={n} className={`w-2.5 h-2.5 rounded-full transition-all ${active ? "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.75)]" : "bg-white/10 border border-white/20"}`} />
        );
      })}
    </div>
  );

  return (
    <div className="min-h-screen overflow-hidden bg-[#080C14] text-white relative">

      {/* ANTI-CHEAT OVERLAY */}
      {isLockedOut && (
        <div className="fixed inset-0 z-[100] bg-black/90 backdrop-blur-md flex flex-col items-center justify-center p-6 text-center animate-fade-in">
          <div className="text-6xl mb-6 animate-pulse">⚠️</div>
          <h1 className="text-3xl font-bold text-red-500 mb-4">Interview Suspended</h1>
          <p className="text-white/70 max-w-md leading-relaxed mb-6 text-lg">
            Tab switching or exiting full-screen mode is strictly prohibited.
          </p>
          <div className="bg-red-500/10 border border-red-500/30 px-6 py-3 rounded-lg mb-8">
             <p className="text-red-400 font-bold tracking-wide">
               STRIKE {cheatCount} OF 3
             </p>
          </div>
          <button
            onClick={returnToInterview}
            className="px-8 py-4 bg-white text-black font-bold rounded-xl hover:bg-gray-200 transition-transform active:scale-95 shadow-lg shadow-white/10"
          >
            Return to Full Screen
          </button>
        </div>
      )}

      <div className="pointer-events-none fixed inset-0 -z-10 opacity-50">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(99,102,241,0.20),_transparent_25%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.06),_transparent_30%)]" />
        <div className="absolute top-1/2 left-1/2 h-[420px] w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-indigo-500/10 blur-3xl" />
      </div>

      <StrikeIndicator />

      {/* Header */}
      <header className="px-6 py-4 flex items-center justify-between border-b border-white/[0.06] backdrop-blur-sm bg-white/5">
        <span className="text-xs font-semibold tracking-[0.15em] text-white/30 uppercase">Interview Room</span>
        {current && phase !== "begin" && (
          <span className="text-xs text-white/25 tabular-nums">Q{current.number}</span>
        )}
      </header>

      <main className="flex-1 flex flex-col items-center justify-center px-4 gap-8 py-10 max-w-2xl mx-auto w-full">

        {/* Strike warning banner */}
        {strikeMsg && (
          <div className="w-full rounded-2xl border border-red-500/30 bg-red-500/10 px-5 py-3 flex items-center gap-3">
            <span className="text-lg">⚠️</span>
            <p className="text-sm font-medium text-red-100">{strikeMsg}</p>
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
              <p className="text-white/40 text-sm leading-relaxed mb-4">
                The interviewer is strict and direct.<br />
                Answers are flagged if evasive or incoherent.<br />
                4 strikes = interview terminated.
              </p>
              <p className="text-amber-400/80 text-xs font-medium bg-amber-500/10 px-3 py-2 rounded-lg border border-amber-500/20">
                🔒 This interview is proctored. You will be locked into full-screen mode.
              </p>
            </div>
            <button onClick={handleBegin}
              className="px-8 py-3.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl transition-colors text-sm shadow-lg shadow-indigo-500/20">
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
            {phase === "recording" && (
              <div className="w-full bg-white/[0.03] border border-white/10 rounded-xl p-4 min-h-[60px]">
                <p className="text-xs text-white/25 mb-1 uppercase tracking-widest">
                  Your Answer / Correction
                </p>
                <p className="text-white/60 text-sm leading-relaxed">
                  {transcript || "Listening..."}
                </p>
              </div>
            )}

            <div className="w-px h-6 bg-white/10" />

            {/* Controls */}
            <div className="flex flex-col items-center gap-4 w-full">

              {phase === "recording" && (
                <div className="flex flex-col items-center w-full animate-fade-in">

                  {/* Recording Status */}
                  <div className="flex items-center gap-2 text-red-400 mb-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" />
                    <span className="font-medium tracking-wide">Recording</span>
                    <span className="text-white/30 text-sm font-mono ml-2">{fmt(seconds)}</span>
                  </div>

                  {/* Main Submit */}
                  <button onClick={stopAndSubmit}
                    className="px-8 py-3 bg-white text-black font-bold rounded-full hover:bg-gray-200 transition-all active:scale-95 shadow-lg flex items-center gap-2 mt-2">
                    <span>Submit Answer</span>
                  </button>

                  {/* Keyboard Hint */}
                  <p className="text-white/40 text-xs mt-3">
                    Press <kbd className="px-1.5 py-0.5 bg-white/10 border border-white/20 rounded font-mono mx-0.5">Space</kbd> or <kbd className="px-1.5 py-0.5 bg-white/10 border border-white/20 rounded font-mono mx-0.5">Enter</kbd> to send
                  </p>

                  <div className="w-full max-w-[200px] h-px bg-white/10 my-5" />

                  {/* Counter/Pushback Option */}
                  <button onClick={stopAndCounter}
                    className="px-5 py-2 rounded-xl bg-white/5 hover:bg-amber-500/10 border border-white/10 hover:border-amber-400/30 text-white/50 hover:text-amber-300 text-xs font-medium transition-all">
                    ↩ Submit as Pushback / Correction
                  </button>
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
              {strikeCount > 0 && (
                 <p className="text-white/30 text-xs">Your responses were flagged {strikeCount} time{strikeCount !== 1 ? "s" : ""}.</p>
              )}
            </div>
            <div className="flex flex-col gap-3 w-full">
              <button onClick={() => router.push(`/interview/${session_id}/report`)}
                className="w-full rounded-xl bg-red-500 px-6 py-3 text-sm font-semibold text-white transition hover:bg-red-400">
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