"use client";

import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────
interface QuestionFeedback {
  question: string;
  // v8 fields (direct score + critique)
  score?: number;
  critique?: string;
  ideal_answer_snippet?: string;
  // v6/v7 fallback fields
  answer_quality?: "strong" | "good" | "average" | "weak" | "blank";
  answer_summary?: string;
  comment?: string;
  what_you_should_have_said?: string;
  ideal_points?: string[];
  study_topic?: string | null;
}

interface LearningTopic { topic: string; reason: string; priority: "High" | "Medium" | "high" | "medium"; }
interface BehavioralFlag { type: string; question: string; note: string; }
interface SpeechPerQ { question: string; word_count: number; filler_count: number; fluency_score: number; fluency_label: string; issues: string[]; }
interface SpeechAnalysis { total_words_spoken: number; total_filler_count: number; top_filler_words: { word: string; count: number }[]; avg_fluency_score: number; fluency_label: string; too_short_answers: number; too_long_answers: number; per_question: SpeechPerQ[]; }

interface Feedback {
  overall_score: number;
  technical_depth_score: number;
  confidence_score: number;
  communication_score: number;
  behaviour_score: number;
  speech_clarity_score: number;
  // v8
  executive_summary?: string;
  learning_path?: LearningTopic[];
  confidence_calibration?: string;
  confidence_note?: string;
  // v6 fallbacks
  summary?: string;
  speech_summary?: string;
  recommended_topics?: LearningTopic[];
  strengths: string[];
  improvements: string[];
  behavioral_flags?: BehavioralFlag[];
  question_feedback: QuestionFeedback[];
  speech_analysis?: SpeechAnalysis;
  score_inputs?: { quality_scores: number[]; avg_fluency: number; strikes: number; idk_ratio: number; avg_word_count: number; };
}

interface ReportData {
  session_id: string;
  role: string;
  level: string;
  questions_answered: number;
  misbehavior_count: number;
  terminated: boolean;
  termination_message: string;
  misbehavior_log: { question: string; reason: string; strike: number }[];
  speech_metrics_log: { question: string; metrics: object }[];
  feedback: Feedback;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const QUALITY_SCORE: Record<string, number> = { strong: 90, good: 75, average: 55, weak: 30, blank: 10 };

function getQScore(q: QuestionFeedback): number {
  if (q.score !== undefined) return q.score;
  return QUALITY_SCORE[q.answer_quality ?? "average"] ?? 55;
}

function getQLabel(q: QuestionFeedback): string {
  if (q.answer_quality) return q.answer_quality;
  const s = getQScore(q);
  if (s >= 85) return "strong";
  if (s >= 65) return "good";
  if (s >= 40) return "average";
  if (s >= 20) return "weak";
  return "blank";
}

const scoreColor = (s: number) =>
  s >= 75 ? "#34d399" : s >= 55 ? "#fbbf24" : s >= 35 ? "#f97316" : "#f87171";

const scoreGlow = (s: number) =>
  s >= 75 ? "rgba(52,211,153,0.15)" : s >= 55 ? "rgba(251,191,36,0.15)" : s >= 35 ? "rgba(249,115,22,0.15)" : "rgba(248,113,113,0.15)";

const qualityColor = (q: string) =>
  ({ strong: "#34d399", good: "#86efac", average: "#fbbf24", weak: "#f97316", blank: "#f87171" }[q] ?? "#888");

const qualityBg = (q: string) =>
  ({ strong: "rgba(52,211,153,0.07)", good: "rgba(134,239,172,0.05)", average: "rgba(251,191,36,0.07)", weak: "rgba(249,115,22,0.08)", blank: "rgba(248,113,113,0.08)" }[q] ?? "rgba(255,255,255,0.03)");

const scoreLabel = (s: number) =>
  s >= 85 ? "Exceptional" : s >= 70 ? "Proficient" : s >= 55 ? "Developing" : s >= 35 ? "Needs Work" : "Critical Gap";

// ── Score Ring ────────────────────────────────────────────────────────────────
function ScoreRing({ score, size = 130, stroke = 11, label }: { score: number; size?: number; stroke?: number; label?: string }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (score / 100) * circ;
  const col = scoreColor(score);
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth={stroke} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={col} strokeWidth={stroke}
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          style={{ filter: `drop-shadow(0 0 6px ${col})`, transition: "stroke-dasharray 1.2s cubic-bezier(0.4,0,0.2,1)" }} />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: size * 0.24, fontWeight: 800, color: col, lineHeight: 1, letterSpacing: "-0.02em" }}>{score}</span>
        {label && <span style={{ fontSize: 10, color: "rgba(255,255,255,0.35)", marginTop: 3, textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>}
      </div>
    </div>
  );
}

// ── Radar Chart ───────────────────────────────────────────────────────────────
function RadarChart({ scores }: { scores: { label: string; value: number }[] }) {
  const cx = 140, cy = 140, r = 100;
  const n = scores.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, pct: number) => ({ x: cx + r * pct * Math.cos(angle(i)), y: cy + r * pct * Math.sin(angle(i)) });
  const polyPoints = scores.map((s, i) => { const p = pt(i, s.value / 100); return `${p.x},${p.y}`; }).join(" ");
  const avgScore = Math.round(scores.reduce((a, b) => a + b.value, 0) / scores.length);
  return (
    <svg width="280" height="280" viewBox="0 0 280 280">
      <defs>
        <radialGradient id="radarFill" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={scoreColor(avgScore)} stopOpacity="0.3" />
          <stop offset="100%" stopColor={scoreColor(avgScore)} stopOpacity="0.05" />
        </radialGradient>
      </defs>
      {[0.25, 0.5, 0.75, 1.0].map(lvl => (
        <polygon key={lvl}
          points={Array.from({ length: n }, (_, i) => { const p = pt(i, lvl); return `${p.x},${p.y}`; }).join(" ")}
          fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
      ))}
      {scores.map((_, i) => { const o = pt(i, 1); return <line key={i} x1={cx} y1={cy} x2={o.x} y2={o.y} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />; })}
      <polygon points={polyPoints} fill="url(#radarFill)" stroke={scoreColor(avgScore)} strokeWidth="2" />
      {scores.map((s, i) => { const p = pt(i, s.value / 100); return <circle key={i} cx={p.x} cy={p.y} r="4" fill={scoreColor(s.value)} style={{ filter: `drop-shadow(0 0 4px ${scoreColor(s.value)})` }} />; })}
      {scores.map((s, i) => { const p = pt(i, 1.28); return (
        <text key={i} x={p.x} y={p.y} textAnchor="middle" dominantBaseline="middle"
          fill="rgba(255,255,255,0.5)" fontSize="11" fontFamily="'DM Mono', monospace">
          {s.label}
        </text>
      ); })}
    </svg>
  );
}

// ── Score Bar ─────────────────────────────────────────────────────────────────
function ScoreBar({ label, value, sublabel }: { label: string; value: number; sublabel?: string }) {
  const col = scoreColor(value);
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div>
          <span style={{ fontSize: 13, color: "rgba(255,255,255,0.7)", fontWeight: 500 }}>{label}</span>
          {sublabel && <span style={{ fontSize: 11, color: "rgba(255,255,255,0.28)", marginLeft: 8 }}>{sublabel}</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 10, color: col, textTransform: "uppercase", letterSpacing: "0.06em" }}>{scoreLabel(value)}</span>
          <span style={{ fontSize: 14, fontWeight: 700, color: col, minWidth: 28, textAlign: "right" }}>{value}</span>
        </div>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: "rgba(255,255,255,0.05)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${value}%`, background: `linear-gradient(90deg, ${col}aa, ${col})`, borderRadius: 3, transition: "width 1s cubic-bezier(0.4,0,0.2,1)", boxShadow: `0 0 8px ${col}50` }} />
      </div>
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────
function Section({ title, children, accent, badge }: { title: string; children: React.ReactNode; accent?: string; badge?: string }) {
  return (
    <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 18, padding: "26px 30px", marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22 }}>
        <h3 style={{ fontSize: 11, fontWeight: 700, color: accent ?? "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.14em" }}>{title}</h3>
        {badge && <span style={{ fontSize: 10, padding: "2px 8px", background: "rgba(255,255,255,0.06)", borderRadius: 20, color: "rgba(255,255,255,0.3)", letterSpacing: "0.06em" }}>{badge}</span>}
      </div>
      {children}
    </div>
  );
}

// ── Q Score Tile ──────────────────────────────────────────────────────────────
function QTile({ q, idx, onClick, active }: { q: QuestionFeedback; idx: number; onClick: () => void; active: boolean }) {
  const score = getQScore(q);
  const label = getQLabel(q);
  const col = qualityColor(label);
  return (
    <button onClick={onClick} style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
      padding: "12px 10px", minWidth: 62,
      background: active ? qualityBg(label) : "rgba(255,255,255,0.03)",
      border: `1px solid ${active ? col + "60" : "rgba(255,255,255,0.07)"}`,
      borderRadius: 12, cursor: "pointer", transition: "all 0.18s ease",
      boxShadow: active ? `0 0 16px ${scoreGlow(score)}` : "none",
    }}>
      <span style={{ fontSize: 18, fontWeight: 800, color: col, letterSpacing: "-0.02em" }}>{score}</span>
      <span style={{ fontSize: 9, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Q{idx + 1}</span>
      <span style={{ fontSize: 9, color: col, textTransform: "capitalize" }}>{label}</span>
    </button>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function ReportPage() {
  const { session_id } = useParams<{ session_id: string }>();
  const router = useRouter();
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openQs, setOpenQs] = useState<Set<number>>(new Set());
  const [allExpanded, setAllExpanded] = useState(false);
  const breakdownRef = useRef<HTMLDivElement>(null);

  const toggleQ = (i: number) => {
    setOpenQs(prev => { const next = new Set(prev); next.has(i) ? next.delete(i) : next.add(i); return next; });
  };
  const toggleAll = () => {
    if (allExpanded) { setOpenQs(new Set()); setAllExpanded(false); }
    else { setOpenQs(new Set(fb?.question_feedback?.map((_: QuestionFeedback, i: number) => i) ?? [])); setAllExpanded(true); }
  };
  const openFromTile = (i: number) => {
    setOpenQs(prev => { const next = new Set(prev); next.add(i); return next; });
    setTimeout(() => breakdownRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };

  useEffect(() => {
    fetch(`${API}/api/interview/report/${session_id}`)
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [session_id]);

  if (loading) return (
    <div style={{ minHeight: "100vh", background: "#080b12", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 16 }}>
      <div style={{ width: 36, height: 36, borderRadius: "50%", border: "2px solid rgba(255,255,255,0.07)", borderTopColor: "#34d399", animation: "spin 0.9s linear infinite" }} />
      <p style={{ color: "rgba(255,255,255,0.25)", fontSize: 13, letterSpacing: "0.06em", textTransform: "uppercase" }}>Compiling assessment…</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );

  if (error || !data) return (
    <div style={{ minHeight: "100vh", background: "#080b12", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center" }}>
        <p style={{ color: "rgba(255,255,255,0.35)", fontSize: 14, marginBottom: 16 }}>Failed to load report: {error}</p>
        <button onClick={() => router.push("/interview")} style={{ color: "#34d399", background: "none", border: "1px solid rgba(52,211,153,0.3)", padding: "8px 20px", borderRadius: 8, cursor: "pointer", fontSize: 13 }}>← Go back</button>
      </div>
    </div>
  );

  const fb = data.feedback;
  const summary = fb.executive_summary ?? fb.summary ?? "";
  const learningTopics = fb.learning_path ?? fb.recommended_topics ?? [];

  const radarScores = [
    { label: "Technical", value: fb.technical_depth_score },
    { label: "Confidence", value: fb.confidence_score },
    { label: "Comms", value: fb.communication_score },
    { label: "Behaviour", value: fb.behaviour_score },
    { label: "Speech", value: fb.speech_clarity_score },
  ];

  const qScores = fb.question_feedback?.map(q => getQScore(q)) ?? [];
  const avgQ = qScores.length ? Math.round(qScores.reduce((a, b) => a + b, 0) / qScores.length) : 0;

  // Score distribution summary
  const dist: Record<string, number> = {};
  fb.question_feedback?.forEach(q => {
    const l = getQLabel(q);
    dist[l] = (dist[l] ?? 0) + 1;
  });

  return (
    <div style={{ minHeight: "100vh", background: "#080b12", color: "white", fontFamily: "'DM Sans', system-ui, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap');
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 2px; }
        button:hover { opacity: 0.88; }
      `}</style>

      {/* Ambient bg */}
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
        background: `radial-gradient(ellipse 60% 40% at 20% 0%, rgba(52,211,153,0.04) 0%, transparent 60%),
                     radial-gradient(ellipse 50% 30% at 80% 100%, rgba(99,102,241,0.04) 0%, transparent 60%)` }} />

      {/* Header */}
      <header style={{ position: "sticky", top: 0, zIndex: 50, borderBottom: "1px solid rgba(255,255,255,0.05)", background: "rgba(8,11,18,0.9)", backdropFilter: "blur(16px)", padding: "0 32px" }}>
        <div style={{ maxWidth: 980, margin: "0 auto", height: 60, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "rgba(255,255,255,0.2)", fontFamily: "'DM Mono', monospace" }}>Assessment Report</span>
            <span style={{ width: 1, height: 14, background: "rgba(255,255,255,0.1)" }} />
            <h1 style={{ fontSize: 15, fontWeight: 600, color: "rgba(255,255,255,0.85)" }}>{data.role}</h1>
            <span style={{ fontSize: 11, padding: "2px 10px", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 20, color: "rgba(255,255,255,0.35)" }}>{data.level}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {data.terminated && (
              <span style={{ fontSize: 10, padding: "3px 10px", background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.25)", borderRadius: 20, color: "#fca5a5", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em" }}>Terminated</span>
            )}
            <span style={{ fontSize: 12, color: "rgba(255,255,255,0.22)", fontFamily: "'DM Mono', monospace" }}>{data.questions_answered}Q</span>
            <button onClick={() => router.push("/interview")}
              style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", padding: "6px 14px", borderRadius: 8, cursor: "pointer" }}>
              New Interview
            </button>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: 980, margin: "0 auto", padding: "36px 32px 100px", position: "relative", zIndex: 1, animation: "fadeUp 0.5s ease both" }}>

        {/* Termination banner */}
        {data.terminated && (
          <div style={{ marginBottom: 24, padding: "16px 22px", background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 14 }}>
            <p style={{ fontSize: 13, fontWeight: 700, color: "#fca5a5", marginBottom: 4 }}>Interview Terminated</p>
            <p style={{ fontSize: 13, color: "rgba(255,255,255,0.4)", fontStyle: "italic" }}>"{data.termination_message}"</p>
          </div>
        )}

        {/* ── HERO SCORES ─────────────────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 20, marginBottom: 20 }}>

          {/* Left: ring + radar */}
          <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 18, padding: "32px 24px", display: "flex", flexDirection: "column", alignItems: "center", gap: 20 }}>
            <div style={{ textAlign: "center" }}>
              <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "rgba(255,255,255,0.25)", marginBottom: 16 }}>Overall Score</p>
              <ScoreRing score={fb.overall_score} size={140} stroke={12} />
              <p style={{ fontSize: 13, color: scoreColor(fb.overall_score), fontWeight: 600, marginTop: 12 }}>{scoreLabel(fb.overall_score)}</p>
            </div>
            <div style={{ width: "100%", height: 1, background: "rgba(255,255,255,0.06)" }} />
            {/* Confidence calibration */}
            {fb.confidence_calibration && (
              <div style={{ textAlign: "center" }}>
                <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "rgba(255,255,255,0.22)", marginBottom: 6 }}>Confidence</p>
                <span style={{
                  fontSize: 11, fontWeight: 600, padding: "4px 12px", borderRadius: 20,
                  textTransform: "capitalize",
                  background: fb.confidence_calibration === "balanced" ? "rgba(52,211,153,0.1)" : "rgba(251,191,36,0.1)",
                  color: fb.confidence_calibration === "balanced" ? "#34d399" : "#fbbf24",
                  border: `1px solid ${fb.confidence_calibration === "balanced" ? "rgba(52,211,153,0.25)" : "rgba(251,191,36,0.25)"}`,
                }}>{fb.confidence_calibration}</span>
                {fb.confidence_note && <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginTop: 8, lineHeight: 1.5 }}>{fb.confidence_note}</p>}
              </div>
            )}
            <RadarChart scores={radarScores} />
          </div>

          {/* Right: dimension bars + score input debug */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 18, padding: "26px 28px", flex: 1 }}>
              <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", marginBottom: 20 }}>Performance Dimensions</p>
              <ScoreBar label="Technical Depth" value={fb.technical_depth_score} sublabel={`mean of ${qScores.length} answers`} />
              <ScoreBar label="Confidence" value={fb.confidence_score} sublabel={fb.score_inputs ? `IDK rate ${Math.round(fb.score_inputs.idk_ratio * 100)}%` : undefined} />
              <ScoreBar label="Communication" value={fb.communication_score} />
              <ScoreBar label="Behaviour" value={fb.behaviour_score} sublabel={data.misbehavior_count > 0 ? `${data.misbehavior_count} strike${data.misbehavior_count > 1 ? "s" : ""}` : "No violations"} />
              <ScoreBar label="Speech Clarity" value={fb.speech_clarity_score} sublabel={fb.speech_analysis ? `fluency ${fb.speech_analysis.avg_fluency_score}/100` : undefined} />
            </div>

            {/* Formula breakdown */}
            <div style={{ background: "rgba(99,102,241,0.04)", border: "1px solid rgba(99,102,241,0.15)", borderRadius: 14, padding: "18px 22px" }}>
              <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "rgba(99,102,241,0.6)", marginBottom: 14 }}>Scoring Formula</p>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {[
                  { l: "Technical", w: "30%", v: fb.technical_depth_score },
                  { l: "Confidence", w: "20%", v: fb.confidence_score },
                  { l: "Comms", w: "20%", v: fb.communication_score },
                  { l: "Behaviour", w: "15%", v: fb.behaviour_score },
                  { l: "Speech", w: "15%", v: fb.speech_clarity_score },
                ].map((item, i, arr) => (
                  <>
                    <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, padding: "8px 12px", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, minWidth: 72 }}>
                      <span style={{ fontSize: 17, fontWeight: 800, color: scoreColor(item.v), letterSpacing: "-0.02em" }}>{item.v}</span>
                      <span style={{ fontSize: 9, color: "rgba(255,255,255,0.4)", textAlign: "center" }}>{item.l}</span>
                      <span style={{ fontSize: 9, color: "rgba(255,255,255,0.22)", fontFamily: "'DM Mono', monospace" }}>×{item.w}</span>
                    </div>
                    {i < arr.length - 1 && <span style={{ color: "rgba(255,255,255,0.18)", fontSize: 16 }}>+</span>}
                  </>
                ))}
                <span style={{ color: "rgba(255,255,255,0.18)", fontSize: 16 }}>=</span>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, padding: "8px 16px", background: "rgba(99,102,241,0.12)", border: "1px solid rgba(99,102,241,0.3)", borderRadius: 10 }}>
                  <span style={{ fontSize: 22, fontWeight: 800, color: scoreColor(fb.overall_score), letterSpacing: "-0.02em" }}>{fb.overall_score}</span>
                  <span style={{ fontSize: 9, color: "rgba(255,255,255,0.3)" }}>Overall</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── EXECUTIVE SUMMARY ────────────────────────────────────────────── */}
        <Section title="Executive Assessment">
          <p style={{ fontSize: 15, color: "rgba(255,255,255,0.72)", lineHeight: 1.8, fontWeight: 400 }}>{summary}</p>
          {fb.speech_summary && (
            <p style={{ fontSize: 13, color: "rgba(255,255,255,0.4)", lineHeight: 1.7, marginTop: 14, paddingTop: 14, borderTop: "1px solid rgba(255,255,255,0.05)" }}>{fb.speech_summary}</p>
          )}
        </Section>

        {/* ── STRENGTHS & IMPROVEMENTS ─────────────────────────────────────── */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginBottom: 18 }}>
          <div style={{ background: "rgba(52,211,153,0.04)", border: "1px solid rgba(52,211,153,0.12)", borderRadius: 18, padding: "26px 28px" }}>
            <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "#34d399", marginBottom: 20, fontWeight: 700 }}>Demonstrated Strengths</p>
            {fb.strengths?.map((s, i) => (
              <div key={i} style={{ display: "flex", gap: 12, marginBottom: 14, alignItems: "flex-start" }}>
                <div style={{ width: 20, height: 20, borderRadius: "50%", background: "rgba(52,211,153,0.12)", border: "1px solid rgba(52,211,153,0.3)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1 }}>
                  <span style={{ fontSize: 10, color: "#34d399" }}>✓</span>
                </div>
                <p style={{ fontSize: 14, color: "rgba(255,255,255,0.65)", lineHeight: 1.6 }}>{s}</p>
              </div>
            ))}
          </div>
          <div style={{ background: "rgba(249,115,22,0.04)", border: "1px solid rgba(249,115,22,0.12)", borderRadius: 18, padding: "26px 28px" }}>
            <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.14em", color: "#f97316", marginBottom: 20, fontWeight: 700 }}>Priority Improvements</p>
            {fb.improvements?.map((imp, i) => (
              <div key={i} style={{ display: "flex", gap: 12, marginBottom: 14, alignItems: "flex-start" }}>
                <div style={{ width: 20, height: 20, borderRadius: "50%", background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.25)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1 }}>
                  <span style={{ fontSize: 10, color: "#f97316" }}>!</span>
                </div>
                <p style={{ fontSize: 14, color: "rgba(255,255,255,0.65)", lineHeight: 1.6 }}>{imp}</p>
              </div>
            ))}
          </div>
        </div>

        {/* ── QUESTION PERFORMANCE OVERVIEW ────────────────────────────────── */}
        <Section title="Answer Quality Overview" badge={`${qScores.length} questions · avg ${avgQ}/100`}>
          {/* Distribution bar */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", gap: 2 }}>
              {["strong", "good", "average", "weak", "blank"].map(label => {
                const count = dist[label] ?? 0;
                const pct = qScores.length ? (count / qScores.length) * 100 : 0;
                if (!count) return null;
                return <div key={label} style={{ width: `${pct}%`, background: qualityColor(label), transition: "width 0.8s ease" }} title={`${label}: ${count}`} />;
              })}
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 10 }}>
              {["strong", "good", "average", "weak", "blank"].map(label => {
                const count = dist[label] ?? 0;
                if (!count) return null;
                return (
                  <div key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: qualityColor(label) }} />
                    <span style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", textTransform: "capitalize" }}>{label} ({count})</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Q tiles */}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
            {fb.question_feedback?.map((q, i) => (
              <QTile key={i} q={q} idx={i} active={openQs.has(i)} onClick={() => openFromTile(i)} />
            ))}
            {/* Avg tile */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 4, padding: "12px 14px", background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.2)", borderRadius: 12, minWidth: 72 }}>
              <span style={{ fontSize: 20, fontWeight: 800, color: scoreColor(avgQ), letterSpacing: "-0.02em" }}>{avgQ}</span>
              <span style={{ fontSize: 9, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Avg</span>
              <span style={{ fontSize: 9, color: "rgba(99,102,241,0.7)" }}>Technical</span>
            </div>
          </div>
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.2)", fontFamily: "'DM Mono', monospace" }}>
            Click a question to expand detailed feedback below ↓
          </p>
        </Section>

        {/* ── QUESTION-BY-QUESTION BREAKDOWN ───────────────────────────────── */}
        <div ref={breakdownRef}>
        <Section title="Detailed Question Analysis" badge={`${fb.question_feedback?.length ?? 0} questions`}>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16, marginTop: -8 }}>
            <button onClick={toggleAll} style={{ fontSize: 11, padding: "5px 14px", background: allExpanded ? "rgba(255,255,255,0.06)" : "rgba(52,211,153,0.08)", border: `1px solid ${allExpanded ? "rgba(255,255,255,0.1)" : "rgba(52,211,153,0.25)"}`, borderRadius: 8, color: allExpanded ? "rgba(255,255,255,0.4)" : "#34d399", cursor: "pointer", fontWeight: 600, letterSpacing: "0.04em" }}>
              {allExpanded ? "Collapse All ▲" : "Expand All ▼"}
            </button>
          </div>
          {fb.question_feedback?.map((q, i) => {
            const score = getQScore(q);
            const label = getQLabel(q);
            const col = qualityColor(label);
            const isOpen = openQs.has(i);
            const critique = q.critique ?? q.comment ?? "";
            const idealSnippet = q.ideal_answer_snippet ?? q.what_you_should_have_said ?? "";
            const idealPoints = q.ideal_points ?? [];
            const answerSummary = q.answer_summary ?? "";

            return (
              <div key={i} style={{ marginBottom: 10, borderRadius: 14, overflow: "hidden", border: `1px solid ${isOpen ? col + "40" : "rgba(255,255,255,0.06)"}`, transition: "border-color 0.2s ease" }}>
                {/* Question header — always visible */}
                <button onClick={() => toggleQ(i)} style={{
                  width: "100%", padding: "16px 20px", display: "flex", alignItems: "flex-start", gap: 14,
                  background: isOpen ? qualityBg(label) : "rgba(255,255,255,0.02)",
                  border: "none", cursor: "pointer", textAlign: "left",
                  transition: "background 0.2s ease",
                }}>
                  {/* Score badge */}
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, minWidth: 52, flexShrink: 0, paddingTop: 2 }}>
                    <span style={{ fontSize: 20, fontWeight: 800, color: col, letterSpacing: "-0.02em", lineHeight: 1 }}>{score}</span>
                    <span style={{ fontSize: 9, color: col, textTransform: "capitalize", opacity: 0.8 }}>{label}</span>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: 14, color: "rgba(255,255,255,0.82)", fontWeight: 500, lineHeight: 1.5, marginBottom: answerSummary ? 6 : 0 }}>
                      <span style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", fontFamily: "'DM Mono', monospace", marginRight: 8 }}>Q{i + 1}</span>
                      {q.question}
                    </p>
                    {answerSummary && (
                      <p style={{ fontSize: 12, color: "rgba(255,255,255,0.32)", lineHeight: 1.5, fontStyle: "italic" }}>"{answerSummary}"</p>
                    )}
                  </div>
                  {/* Fluency mini bar */}
                  {fb.speech_analysis?.per_question?.[i] && (
                    <div style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 3 }}>
                      <span style={{ fontSize: 9, color: "rgba(255,255,255,0.25)", textTransform: "uppercase", letterSpacing: "0.06em" }}>fluency</span>
                      <span style={{ fontSize: 12, fontWeight: 600, color: scoreColor(fb.speech_analysis.per_question[i].fluency_score) }}>
                        {fb.speech_analysis.per_question[i].fluency_score}
                      </span>
                    </div>
                  )}
                  <span style={{ fontSize: 11, color: "rgba(255,255,255,0.2)", flexShrink: 0, marginTop: 4 }}>{isOpen ? "▲" : "▼"}</span>
                </button>

                {/* Expanded detail */}
                {isOpen && (
                  <div style={{ background: "rgba(0,0,0,0.25)", padding: "22px 22px 22px 86px", display: "flex", flexDirection: "column", gap: 18 }}>

                    {/* Critique */}
                    {critique && (
                      <div>
                        <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.12em", color: "rgba(255,255,255,0.25)", marginBottom: 8, fontFamily: "'DM Mono', monospace" }}>Evaluator Critique</p>
                        <p style={{ fontSize: 14, color: "rgba(255,255,255,0.68)", lineHeight: 1.75 }}>{critique}</p>
                      </div>
                    )}

                    {/* What you should have said */}
                    {idealSnippet && (
                      <div style={{ padding: "16px 18px", background: "rgba(99,102,241,0.07)", border: "1px solid rgba(99,102,241,0.18)", borderRadius: 12 }}>
                        <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.12em", color: "#818cf8", marginBottom: 10, fontFamily: "'DM Mono', monospace" }}>Model Answer Approach</p>
                        <p style={{ fontSize: 14, color: "rgba(255,255,255,0.62)", lineHeight: 1.72 }}>{idealSnippet}</p>
                      </div>
                    )}

                    {/* Ideal points */}
                    {idealPoints.length > 0 && (
                      <div>
                        <p style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.12em", color: "rgba(255,255,255,0.25)", marginBottom: 10, fontFamily: "'DM Mono', monospace" }}>Key Points to Cover</p>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                          {idealPoints.map((pt, j) => (
                            <span key={j} style={{ fontSize: 12, padding: "5px 12px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.09)", borderRadius: 20, color: "rgba(255,255,255,0.5)" }}>
                              {pt}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Speech stats for this Q */}
                    {fb.speech_analysis?.per_question?.[i] && (
                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                        {[
                          { l: "Words", v: fb.speech_analysis.per_question[i].word_count },
                          { l: "Fillers", v: fb.speech_analysis.per_question[i].filler_count },
                          { l: "Fluency", v: `${fb.speech_analysis.per_question[i].fluency_score}/100` },
                          { l: "Rating", v: fb.speech_analysis.per_question[i].fluency_label },
                        ].map((stat, j) => (
                          <div key={j} style={{ padding: "8px 14px", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 10, textAlign: "center" }}>
                            <p style={{ fontSize: 13, fontWeight: 700, color: "rgba(255,255,255,0.7)" }}>{stat.v}</p>
                            <p style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", marginTop: 2 }}>{stat.l}</p>
                          </div>
                        ))}
                        {fb.speech_analysis.per_question[i].issues.map((issue, j) => (
                          <div key={j} style={{ padding: "8px 14px", background: "rgba(249,115,22,0.06)", border: "1px solid rgba(249,115,22,0.18)", borderRadius: 10, display: "flex", alignItems: "center", gap: 6 }}>
                            <span style={{ fontSize: 11, color: "#f97316" }}>⚠ {issue}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Study topic */}
                    {q.study_topic && (
                      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: "rgba(251,191,36,0.06)", border: "1px solid rgba(251,191,36,0.18)", borderRadius: 10 }}>
                        <span style={{ fontSize: 15 }}>📚</span>
                        <div>
                          <p style={{ fontSize: 10, color: "rgba(251,191,36,0.6)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2 }}>Recommended Study</p>
                          <p style={{ fontSize: 13, color: "#fbbf24" }}>{q.study_topic}</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </Section>

        {/* ── LEARNING ROADMAP ─────────────────────────────────────────────── */}
        {learningTopics.length > 0 && (
          <Section title="Learning Roadmap" accent="#818cf8">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {learningTopics.map((t, i) => {
                const isHigh = t.priority?.toLowerCase() === "high";
                return (
                  <div key={i} style={{ padding: "16px 18px", background: isHigh ? "rgba(249,115,22,0.05)" : "rgba(255,255,255,0.02)", border: `1px solid ${isHigh ? "rgba(249,115,22,0.18)" : "rgba(255,255,255,0.07)"}`, borderRadius: 14 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                      <p style={{ fontSize: 14, fontWeight: 600, color: isHigh ? "#fb923c" : "rgba(255,255,255,0.72)" }}>{t.topic}</p>
                      <span style={{ fontSize: 9, textTransform: "uppercase", padding: "2px 8px", borderRadius: 10, fontWeight: 700, letterSpacing: "0.06em", background: isHigh ? "rgba(249,115,22,0.12)" : "rgba(255,255,255,0.05)", color: isHigh ? "#fdba74" : "rgba(255,255,255,0.3)" }}>{t.priority}</span>
                    </div>
                    <p style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", lineHeight: 1.6 }}>{t.reason}</p>
                  </div>
                );
              })}
            </div>
          </Section>
        )}


        </div>

                {/* ── SPEECH ANALYSIS ──────────────────────────────────────────────── */}
        {fb.speech_analysis && (
          <Section title="Speech & Fluency">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 22 }}>
              {[
                { l: "Words Spoken", v: fb.speech_analysis.total_words_spoken },
                { l: "Filler Count", v: fb.speech_analysis.total_filler_count },
                { l: "Avg Fluency", v: `${fb.speech_analysis.avg_fluency_score}/100` },
                { l: "Brief Answers", v: fb.speech_analysis.too_short_answers },
              ].map((stat, i) => (
                <div key={i} style={{ textAlign: "center", padding: "16px 10px", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 12 }}>
                  <p style={{ fontSize: 22, fontWeight: 800, color: "rgba(255,255,255,0.85)", letterSpacing: "-0.02em" }}>{stat.v}</p>
                  <p style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", marginTop: 5, textTransform: "uppercase", letterSpacing: "0.08em" }}>{stat.l}</p>
                </div>
              ))}
            </div>
            {fb.speech_analysis.top_filler_words.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginBottom: 10 }}>Most-used fillers:</p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {fb.speech_analysis.top_filler_words.map((fw, i) => (
                    <span key={i} style={{ fontSize: 12, padding: "5px 14px", background: "rgba(249,115,22,0.08)", border: "1px solid rgba(249,115,22,0.2)", borderRadius: 20, color: "#f97316", fontFamily: "'DM Mono', monospace" }}>
                      "{fw.word}" ×{fw.count}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {fb.speech_analysis.per_question.map((sq, i) => (
              <div key={i} style={{ marginBottom: 10, padding: "12px 16px", background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 8 }}>
                  <p style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", lineHeight: 1.4, flex: 1 }}>
                    <span style={{ color: "rgba(255,255,255,0.2)", fontFamily: "'DM Mono', monospace", marginRight: 8, fontSize: 11 }}>Q{i + 1}</span>
                    {sq.question}
                  </p>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <span style={{ fontSize: 10, padding: "2px 8px", background: "rgba(255,255,255,0.04)", borderRadius: 8, color: "rgba(255,255,255,0.3)", fontFamily: "'DM Mono', monospace" }}>{sq.word_count}w</span>
                    <span style={{ fontSize: 10, padding: "2px 8px", background: `${scoreColor(sq.fluency_score)}15`, borderRadius: 8, color: scoreColor(sq.fluency_score) }}>{sq.fluency_label}</span>
                  </div>
                </div>
                <div style={{ height: 3, borderRadius: 2, background: "rgba(255,255,255,0.05)" }}>
                  <div style={{ height: "100%", width: `${sq.fluency_score}%`, background: scoreColor(sq.fluency_score), borderRadius: 2 }} />
                </div>
              </div>
            ))}
          </Section>
        )}

        {/* ── BEHAVIORAL FLAGS ─────────────────────────────────────────────── */}
        {(fb.behavioral_flags?.length ?? 0) > 0 && (
          <Section title="Behavioral Flags" accent="#f87171">
            {fb.behavioral_flags!.map((flag, i) => (
              <div key={i} style={{ padding: "14px 16px", background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.18)", borderRadius: 12, marginBottom: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 9, padding: "2px 9px", background: "rgba(248,113,113,0.12)", borderRadius: 20, color: "#fca5a5", textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.08em" }}>{flag.type}</span>
                </div>
                <p style={{ fontSize: 12, color: "rgba(255,255,255,0.4)", marginBottom: 6, fontStyle: "italic" }}>"{flag.question}"</p>
                <p style={{ fontSize: 13, color: "rgba(255,255,255,0.6)", lineHeight: 1.6 }}>{flag.note}</p>
              </div>
            ))}
          </Section>
        )}

        {/* ── STRIKE LOG ───────────────────────────────────────────────────── */}
        {data.misbehavior_log?.length > 0 && (
          <Section title="Strike Log" accent="#f87171">
            {data.misbehavior_log.map((m, i) => (
              <div key={i} style={{ display: "flex", gap: 14, alignItems: "flex-start", marginBottom: 12 }}>
                <div style={{ width: 26, height: 26, borderRadius: "50%", background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: "#f87171" }}>{m.strike}</span>
                </div>
                <div>
                  <p style={{ fontSize: 13, color: "#fca5a5", fontWeight: 600 }}>{m.reason}</p>
                  <p style={{ fontSize: 12, color: "rgba(255,255,255,0.3)", marginTop: 3 }}>{m.question}</p>
                </div>
              </div>
            ))}
          </Section>
        )}

        {/* Footer */}
        <div style={{ textAlign: "center", marginTop: 48 }}>
          <button onClick={() => router.push("/interview")}
            style={{ padding: "14px 36px", background: "rgba(52,211,153,0.08)", border: "1px solid rgba(52,211,153,0.25)", borderRadius: 12, color: "#34d399", fontSize: 14, fontWeight: 600, cursor: "pointer", letterSpacing: "0.02em" }}>
            Start New Interview →
          </button>
        </div>

      </main>
    </div>
  );
}