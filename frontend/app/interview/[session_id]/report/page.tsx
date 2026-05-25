"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

// ─── Types ────────────────────────────────────────────────────────────────────
interface SpeechMetricsPerQ {
  question: string;
  word_count: number;
  filler_count: number;
  fluency_score: number;
  fluency_label: string;
  issues: string[];
}

interface SpeechAnalysis {
  total_words_spoken: number;
  total_filler_count: number;
  top_filler_words: { word: string; count: number }[];
  repeated_words: string[];
  avg_fluency_score: number;
  fluency_label: string;
  too_short_answers: number;
  too_long_answers: number;
  per_question: SpeechMetricsPerQ[];
}

interface QuestionFeedback {
  question: string;
  answer_quality: "strong" | "good" | "average" | "weak" | "blank";
  answer_summary: string;
  comment: string;
  what_you_should_have_said: string;
  ideal_points: string[];
  study_topic: string | null;
}

interface BehavioralFlag {
  type: "rude" | "dismissive" | "over-confident" | "evasive" | "idk";
  question: string;
  note: string;
}

interface RecommendedTopic {
  topic: string;
  reason: string;
  priority: "high" | "medium";
}

interface FeedbackReport {
  overall_score: number;
  confidence_score: number;
  clarity_score: number;
  technical_depth_score: number;
  communication_score: number;
  behaviour_score: number;
  speech_clarity_score: number;
  summary: string;
  speech_summary: string;
  confidence_calibration: "under-confident" | "balanced" | "over-confident";
  confidence_note: string;
  strengths: string[];
  improvements: string[];
  behavioral_flags: BehavioralFlag[];
  question_feedback: QuestionFeedback[];
  recommended_topics: RecommendedTopic[];
  speech_analysis?: SpeechAnalysis;
}

interface MisbehaviorEntry {
  question: string;
  reason: string;
  strike: number;
}

interface ReportResponse {
  session_id: string;
  role: string;
  level: string;
  questions_answered: number;
  misbehavior_count: number;
  terminated: boolean;
  termination_message: string;
  misbehavior_log: MisbehaviorEntry[];
  speech_metrics_log: { question: string; metrics: Record<string, unknown> }[];
  feedback: FeedbackReport;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ─── Helpers ──────────────────────────────────────────────────────────────────
function scoreColor(score: number) {
  if (score >= 75) return "#10b981";
  if (score >= 55) return "#f59e0b";
  return "#ef4444";
}
function scoreLabel(score: number) {
  if (score >= 80) return "Exceptional";
  if (score >= 70) return "Strong";
  if (score >= 55) return "Developing";
  if (score >= 40) return "Needs Work";
  return "Critical Gap";
}
function qualityConfig(q: QuestionFeedback["answer_quality"]) {
  return ({
    strong:  { label: "Strong",    bg: "rgba(16,185,129,.12)",  border: "rgba(16,185,129,.3)",  text: "#10b981" },
    good:    { label: "Good",      bg: "rgba(16,185,129,.07)",  border: "rgba(16,185,129,.2)",  text: "#6ee7b7" },
    average: { label: "Average",   bg: "rgba(245,158,11,.10)",  border: "rgba(245,158,11,.3)",  text: "#f59e0b" },
    weak:    { label: "Weak",      bg: "rgba(239,68,68,.10)",   border: "rgba(239,68,68,.3)",   text: "#ef4444" },
    blank:   { label: "No Answer", bg: "rgba(239,68,68,.15)",   border: "rgba(239,68,68,.4)",   text: "#fca5a5" },
  } as const)[q] ?? { label: q, bg: "rgba(255,255,255,.05)", border: "rgba(255,255,255,.1)", text: "#9ca3af" };
}
function flagConfig(type: BehavioralFlag["type"]) {
  return ({
    rude:              { label: "Rude",           icon: "⚠️", color: "#ef4444" },
    dismissive:        { label: "Dismissive",     icon: "🚫", color: "#f97316" },
    "over-confident":  { label: "Over-confident", icon: "📢", color: "#f59e0b" },
    evasive:           { label: "Evasive",        icon: "↩️", color: "#a78bfa" },
    idk:               { label: "Knowledge Gap",  icon: "📚", color: "#60a5fa" },
  } as const)[type] ?? { label: type, icon: "•", color: "#9ca3af" };
}

// ─── Animated counter ─────────────────────────────────────────────────────────
function AnimatedScore({ score }: { score: number }) {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    let frame = 0;
    const total = 60;
    const step = () => {
      frame++;
      setDisplay(Math.round((frame / total) * score));
      if (frame < total) requestAnimationFrame(step);
    };
    const t = setTimeout(() => requestAnimationFrame(step), 300);
    return () => clearTimeout(t);
  }, [score]);
  return <>{display}</>;
}

// ─── Score Ring ───────────────────────────────────────────────────────────────
function ScoreRing({ score, size = 140 }: { score: number; size?: number }) {
  const r     = size / 2 - 12;
  const circ  = 2 * Math.PI * r;
  const [dash, setDash] = useState(0);
  const color = scoreColor(score);
  useEffect(() => {
    const t = setTimeout(() => setDash((score / 100) * circ), 400);
    return () => clearTimeout(t);
  }, [score, circ]);
  return (
    <div style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
      <svg style={{ position: "absolute", inset: 0, transform: "rotate(-90deg)" }} width={size} height={size}>
        <circle cx={size/2} cy={size/2} r={r} stroke="rgba(255,255,255,0.06)" strokeWidth="10" fill="none" />
        <circle cx={size/2} cy={size/2} r={r} stroke={color} strokeWidth="10" fill="none"
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          style={{ transition: "stroke-dasharray 1.2s cubic-bezier(0.4,0,0.2,1)" }} />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: size > 120 ? 30 : 22, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>
          <AnimatedScore score={score} />
        </span>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.35)" }}>/ 100</span>
      </div>
    </div>
  );
}

// ─── Score Bar ────────────────────────────────────────────────────────────────
function ScoreBar({ label, score, delay = 0 }: { label: string; score: number; delay?: number }) {
  const [width, setWidth] = useState(0);
  const color = scoreColor(score);
  useEffect(() => { const t = setTimeout(() => setWidth(score), 500 + delay); return () => clearTimeout(t); }, [score, delay]);
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5, alignItems: "center" }}>
        <span style={{ fontSize: 13, color: "rgba(255,255,255,0.6)", fontWeight: 500 }}>{label}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color, background: `${color}20`, border: `1px solid ${color}40`, borderRadius: 4, padding: "2px 6px" }}>
            {scoreLabel(score)}
          </span>
          <span style={{ fontSize: 13, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{score}</span>
        </div>
      </div>
      <div style={{ height: 6, background: "rgba(255,255,255,0.07)", borderRadius: 99, overflow: "hidden" }}>
        <div style={{ height: "100%", borderRadius: 99, background: color, width: `${width}%`,
          transition: `width 0.9s cubic-bezier(0.4,0,0.2,1) ${delay}ms`, boxShadow: `0 0 8px ${color}60` }} />
      </div>
    </div>
  );
}

// ─── Radar / Hexagon chart (pure SVG, no deps) ────────────────────────────────
function RadarChart({ scores }: { scores: { label: string; value: number }[] }) {
  const cx = 160; const cy = 160; const r = 120;
  const n  = scores.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, pct: number) => ({
    x: cx + r * pct * Math.cos(angle(i)),
    y: cy + r * pct * Math.sin(angle(i)),
  });
  const rings = [0.25, 0.5, 0.75, 1];
  const ringPath = (pct: number) =>
    scores.map((_, i) => `${i === 0 ? "M" : "L"} ${pt(i, pct).x} ${pt(i, pct).y}`).join(" ") + " Z";
  const dataPath = scores
    .map((s, i) => `${i === 0 ? "M" : "L"} ${pt(i, s.value / 100).x} ${pt(i, s.value / 100).y}`)
    .join(" ") + " Z";

  return (
    <svg width={320} height={320} style={{ overflow: "visible" }}>
      {rings.map((pct) => (
        <path key={pct} d={ringPath(pct)} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
      ))}
      {scores.map((_, i) => (
        <line key={i} x1={cx} y1={cy} x2={pt(i, 1).x} y2={pt(i, 1).y} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
      ))}
      <path d={dataPath} fill="rgba(99,102,241,0.18)" stroke="#6366f1" strokeWidth="2" strokeLinejoin="round" />
      {scores.map((s, i) => (
        <circle key={i} cx={pt(i, s.value / 100).x} cy={pt(i, s.value / 100).y} r={4}
          fill="#6366f1" stroke="rgba(0,0,0,0.5)" strokeWidth="1.5" />
      ))}
      {scores.map((s, i) => {
        const { x, y } = pt(i, 1.18);
        return (
          <text key={i} x={x} y={y} textAnchor="middle" dominantBaseline="middle"
            fontSize="11" fill="rgba(255,255,255,0.5)" fontFamily="system-ui">
            {s.label}
          </text>
        );
      })}
    </svg>
  );
}

// ─── Speech Fluency Bar ───────────────────────────────────────────────────────
function FluencyBar({ score, question }: { score: number; question: string }) {
  const color = scoreColor(score);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.45)", maxWidth: "75%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {question}
        </span>
        <span style={{ fontSize: 11, fontWeight: 700, color }}>{score}</span>
      </div>
      <div style={{ height: 4, background: "rgba(255,255,255,0.06)", borderRadius: 99, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${score}%`, background: color, borderRadius: 99 }} />
      </div>
    </div>
  );
}

// ─── Confidence Meter ─────────────────────────────────────────────────────────
function ConfidenceMeter({ calibration, score }: { calibration: string; score: number }) {
  const positions: Record<string, number> = { "under-confident": 15, "balanced": 50, "over-confident": 85 };
  const pos      = positions[calibration] ?? 50;
  const isIdeal  = calibration === "balanced";
  return (
    <div style={{ padding: "20px 24px", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, marginBottom: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em" }}>Confidence Calibration</span>
        <span style={{
          fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 6,
          background: isIdeal ? "rgba(16,185,129,0.15)" : "rgba(245,158,11,0.15)",
          color: isIdeal ? "#10b981" : "#f59e0b",
          border: isIdeal ? "1px solid rgba(16,185,129,0.3)" : "1px solid rgba(245,158,11,0.3)",
        }}>{calibration}</span>
      </div>
      <div style={{ position: "relative", height: 8, background: "linear-gradient(to right, rgba(99,102,241,0.4), rgba(16,185,129,0.8), rgba(239,68,68,0.4))", borderRadius: 99, marginBottom: 8 }}>
        <div style={{
          position: "absolute", top: "50%", transform: "translate(-50%, -50%)",
          left: `${pos}%`, width: 16, height: 16, borderRadius: "50%",
          background: isIdeal ? "#10b981" : "#f59e0b", border: "2px solid rgba(0,0,0,0.6)",
          boxShadow: `0 0 10px ${isIdeal ? "#10b981" : "#f59e0b"}`,
        }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 10, color: "rgba(255,255,255,0.25)" }}>Under-confident</span>
        <span style={{ fontSize: 10, color: "rgba(255,255,255,0.4)" }}>Balanced ✓</span>
        <span style={{ fontSize: 10, color: "rgba(255,255,255,0.25)" }}>Over-confident</span>
      </div>
    </div>
  );
}

// ─── Filler word bubble chart (visual) ───────────────────────────────────────
function FillerBubbles({ fillers }: { fillers: { word: string; count: number }[] }) {
  if (!fillers?.length) return <p style={{ fontSize: 12, color: "rgba(255,255,255,0.25)", fontStyle: "italic" }}>No significant filler words detected.</p>;
  const max = Math.max(...fillers.map((f) => f.count), 1);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {fillers.map((f) => {
        const pct  = f.count / max;
        const size = Math.round(28 + pct * 24);
        const bg   = `rgba(239,68,68,${0.1 + pct * 0.25})`;
        const bdr  = `rgba(239,68,68,${0.2 + pct * 0.4})`;
        return (
          <div key={f.word} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <div style={{
              width: size + 20, height: size + 20, borderRadius: "50%", background: bg, border: `1px solid ${bdr}`,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <span style={{ fontSize: 11, color: "#fca5a5", fontWeight: 600 }}>"{f.word}"</span>
            </div>
            <span style={{ fontSize: 10, color: "rgba(255,255,255,0.25)" }}>×{f.count}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Question Card ─────────────────────────────────────────────────────────────
function QuestionCard({ qf, index, speechData }: { qf: QuestionFeedback; index: number; speechData?: SpeechMetricsPerQ }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = qualityConfig(qf.answer_quality);
  return (
    <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 16, overflow: "hidden" }}>
      <button onClick={() => setExpanded((e) => !e)} style={{
        width: "100%", padding: "18px 20px", display: "flex", alignItems: "flex-start",
        gap: 14, background: "none", border: "none", cursor: "pointer", textAlign: "left",
      }}>
        <span style={{
          minWidth: 26, height: 26, borderRadius: "50%", background: "rgba(255,255,255,0.06)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 11, color: "rgba(255,255,255,0.4)", fontWeight: 600, flexShrink: 0, marginTop: 1,
        }}>{index + 1}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 14, color: "rgba(255,255,255,0.85)", fontWeight: 500, lineHeight: 1.5, marginBottom: 4 }}>
            {qf.question}
          </p>
          {qf.answer_summary && (
            <p style={{ fontSize: 12, color: "rgba(255,255,255,0.35)", fontStyle: "italic" }}>
              You said: "{qf.answer_summary}"
            </p>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {speechData && (
            <span style={{ fontSize: 10, color: scoreColor(speechData.fluency_score), background: `${scoreColor(speechData.fluency_score)}15`, border: `1px solid ${scoreColor(speechData.fluency_score)}30`, borderRadius: 4, padding: "2px 6px" }}>
              fluency {speechData.fluency_score}
            </span>
          )}
          <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, background: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.text }}>
            {cfg.label}
          </span>
          <span style={{ color: "rgba(255,255,255,0.25)", fontSize: 12, transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▾</span>
        </div>
      </button>

      {expanded && (
        <div style={{ padding: "0 20px 20px 60px", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
          {/* Speech metrics for this Q */}
          {speechData && (speechData.filler_count > 0 || speechData.issues.length > 0) && (
            <div style={{ marginTop: 14, background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.15)", borderRadius: 10, padding: "10px 14px", marginBottom: 14 }}>
              <p style={{ fontSize: 11, color: "rgba(239,68,68,0.7)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8, fontWeight: 600 }}>🗣 Speech Issues</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {speechData.issues.map((issue, i) => (
                  <span key={i} style={{ fontSize: 11, color: "#fca5a5", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 6, padding: "2px 8px" }}>
                    {issue}
                  </span>
                ))}
              </div>
              <p style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", marginTop: 8 }}>
                Words spoken: {speechData.word_count} · Fluency: {speechData.fluency_score}/100
              </p>
            </div>
          )}

          <div style={{ marginTop: 16, marginBottom: 14 }}>
            <p style={{ fontSize: 12, color: "rgba(255,255,255,0.3)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.08em" }}>Feedback</p>
            <p style={{ fontSize: 13, color: "rgba(255,255,255,0.65)", lineHeight: 1.6 }}>{qf.comment}</p>
          </div>

          {qf.what_you_should_have_said && (
            <div style={{ background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.2)", borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
              <p style={{ fontSize: 11, color: "rgba(99,102,241,0.8)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 600 }}>
                💡 What you should have said
              </p>
              <p style={{ fontSize: 13, color: "rgba(255,255,255,0.7)", lineHeight: 1.6 }}>{qf.what_you_should_have_said}</p>
            </div>
          )}

          {qf.ideal_points?.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <p style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.08em" }}>Key points to cover</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {qf.ideal_points.map((pt, j) => (
                  <div key={j} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <span style={{ color: "#6366f1", marginTop: 2, flexShrink: 0 }}>→</span>
                    <span style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", lineHeight: 1.5 }}>{pt}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {qf.study_topic && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 8, padding: "6px 12px" }}>
              <span style={{ fontSize: 13 }}>📚</span>
              <span style={{ fontSize: 12, color: "#fca5a5", fontWeight: 500 }}>Study: {qf.study_topic}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Section heading ──────────────────────────────────────────────────────────
function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 14, fontWeight: 600 }}>
      {children}
    </p>
  );
}

// ─── Main Report Page ─────────────────────────────────────────────────────────
export default function ReportPage() {
  const { session_id } = useParams<{ session_id: string }>();
  const router = useRouter();
  const [report, setReport]   = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    async function fetchReport() {
      try {
        const res = await fetch(`${API}/api/interview/report/${session_id}`);
        if (!res.ok) {
          const msg = await res.text();
          throw new Error(msg || `HTTP ${res.status}`);
        }
        const data: ReportResponse = await res.json();
        setReport(data);
      } catch (e: unknown) {
        setError((e as Error).message || "Could not load report");
      } finally {
        setLoading(false);
      }
    }
    fetchReport();
  }, [session_id]);

  if (loading) return (
    <div style={{ minHeight: "100vh", background: "#09090f", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 16 }}>
      <div style={{ width: 40, height: 40, border: "2px solid rgba(255,255,255,0.1)", borderTopColor: "#6366f1", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
      <p style={{ color: "rgba(255,255,255,0.4)", fontSize: 14 }}>Generating your report…</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );

  if (error || !report) return (
    <div style={{ minHeight: "100vh", background: "#09090f", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 40, marginBottom: 8 }}>⚠️</div>
      <p style={{ color: "rgba(255,255,255,0.6)", fontSize: 15, fontWeight: 600 }}>Could not load report</p>
      <p style={{ color: "rgba(255,255,255,0.25)", fontSize: 12, maxWidth: "320px", textAlign: "center" }}>{error}</p>
      <div style={{ display: "flex", gap: 12, marginTop: 16 }}>
        <button onClick={() => window.location.reload()} style={{ padding: "10px 20px", background: "#6366f1", color: "white", fontWeight: 600, fontSize: 13, borderRadius: 10, border: "none", cursor: "pointer" }}>Retry</button>
        <button onClick={() => router.push("/interview")} style={{ padding: "10px 20px", background: "rgba(255,255,255,0.06)", color: "rgba(255,255,255,0.5)", fontSize: 13, borderRadius: 10, border: "1px solid rgba(255,255,255,0.1)", cursor: "pointer" }}>New interview</button>
      </div>
    </div>
  );

  const fb     = report.feedback;
  const sa     = fb.speech_analysis;
  const scores = [
    { label: "Confidence",      score: fb.confidence_score,      delay: 0   },
    { label: "Clarity",         score: fb.clarity_score,         delay: 100 },
    { label: "Technical Depth", score: fb.technical_depth_score, delay: 200 },
    { label: "Communication",   score: fb.communication_score,   delay: 300 },
    { label: "Behaviour",       score: fb.behaviour_score ?? 70, delay: 400 },
    { label: "Speech Clarity",  score: fb.speech_clarity_score ?? (sa?.avg_fluency_score ?? 70), delay: 500 },
  ];

  const radarScores = scores.map((s) => ({ label: s.label.split(" ")[0], value: s.score }));

  const highTopics = fb.recommended_topics?.filter((t) => t.priority === "high") ?? [];
  const medTopics  = fb.recommended_topics?.filter((t) => t.priority !== "high") ?? [];

  // Match speech per-question data to question feedback
  const speechPerQ = sa?.per_question ?? [];
  const getSpeechForQ = (question: string) =>
    speechPerQ.find((s) => s.question.startsWith(question.slice(0, 50)));

  const card = { background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 20, padding: 24, marginBottom: 20 };

  return (
    <div style={{ minHeight: "100vh", background: "#09090f", color: "white", fontFamily: "'Inter', system-ui, sans-serif" }}>
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
        .fade { animation: fadeUp 0.5s ease forwards; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      {/* Sticky header */}
      <header style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", padding: "16px 24px", display: "flex", alignItems: "center", justifyContent: "space-between", position: "sticky", top: 0, background: "rgba(9,9,15,0.92)", backdropFilter: "blur(12px)", zIndex: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: "0.15em", color: "rgba(255,255,255,0.35)", textTransform: "uppercase" }}>
          Performance Report
        </span>
        <button onClick={() => router.push("/interview")} style={{ fontSize: 13, color: "#6366f1", background: "none", border: "none", cursor: "pointer" }}>
          ← Practice again
        </button>
      </header>

      <main style={{ maxWidth: 800, margin: "0 auto", padding: "40px 20px 80px" }}>

        {/* Title + tags */}
        <div className="fade" style={{ marginBottom: 32 }}>
          <h1 style={{ fontSize: 26, fontWeight: 700, marginBottom: 8 }}>{report.role}</h1>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontSize: 13, color: "rgba(255,255,255,0.4)" }}>{report.level}</span>
            <span style={{ color: "rgba(255,255,255,0.2)" }}>·</span>
            <span style={{ fontSize: 13, color: "rgba(255,255,255,0.4)" }}>{report.questions_answered} questions</span>
            {report.terminated && (
              <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 10px", borderRadius: 20, background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
                🚫 Interview Terminated
              </span>
            )}
            {report.misbehavior_count > 0 && !report.terminated && (
              <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 10px", borderRadius: 20, background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.25)" }}>
                ⚠ {report.misbehavior_count} strike{report.misbehavior_count > 1 ? "s" : ""}
              </span>
            )}
          </div>
        </div>

        {/* Termination notice */}
        {report.terminated && (
          <div className="fade" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 16, padding: "18px 22px", marginBottom: 20 }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: "#ef4444", marginBottom: 6 }}>🚫 Why your interview was terminated</p>
            <p style={{ fontSize: 13, color: "rgba(255,255,255,0.6)", fontStyle: "italic" }}>"{report.termination_message}"</p>
            {report.misbehavior_log?.map((m, i) => (
              <p key={i} style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginTop: 6 }}>
                Strike {m.strike}: {m.reason} — "{m.question.slice(0, 70)}…"
              </p>
            ))}
          </div>
        )}

        {/* ── Overall score + radar ────────────────────────────────────────── */}
        <div className="fade" style={{ ...card, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            <ScoreRing score={fb.overall_score} />
            <span style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em" }}>Overall</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: scoreColor(fb.overall_score) }}>{scoreLabel(fb.overall_score)}</span>
          </div>
          <div style={{ flex: 1, minWidth: 240 }}>
            {scores.map((s) => <ScoreBar key={s.label} {...s} />)}
          </div>
        </div>

        {/* Radar chart */}
        <div className="fade" style={{ ...card, display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <SectionHeading>Score Radar</SectionHeading>
          <RadarChart scores={radarScores} />
        </div>

        {/* Confidence calibration */}
        <div className="fade" style={{ marginBottom: 20 }}>
          <ConfidenceMeter calibration={fb.confidence_calibration ?? "balanced"} score={fb.confidence_score} />
          {fb.confidence_note && (
            <p style={{ fontSize: 12, color: "rgba(255,255,255,0.35)", marginTop: 8, fontStyle: "italic", paddingLeft: 4 }}>
              {fb.confidence_note}
            </p>
          )}
        </div>

        {/* Summary */}
        <div className="fade" style={card}>
          <SectionHeading>Overall Summary</SectionHeading>
          <p style={{ fontSize: 14, color: "rgba(255,255,255,0.75)", lineHeight: 1.7 }}>{fb.summary}</p>
        </div>

        {/* ── Speech analysis ──────────────────────────────────────────────── */}
        {sa && (
          <div className="fade" style={{ ...card, borderColor: "rgba(99,102,241,0.2)" }}>
            <SectionHeading>🗣 Speech & Communication Analysis</SectionHeading>

            {fb.speech_summary && (
              <p style={{ fontSize: 13, color: "rgba(255,255,255,0.6)", lineHeight: 1.6, marginBottom: 18, fontStyle: "italic" }}>
                {fb.speech_summary}
              </p>
            )}

            {/* Stat pills */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20 }}>
              {[
                { label: "Total words spoken", value: sa.total_words_spoken, good: true },
                { label: "Filler words used",  value: sa.total_filler_count,  good: sa.total_filler_count < 5 },
                { label: "Avg fluency score",  value: `${sa.avg_fluency_score}/100`, good: sa.avg_fluency_score >= 65 },
                { label: "Too-short answers",  value: sa.too_short_answers,  good: sa.too_short_answers === 0 },
                { label: "Too-long answers",   value: sa.too_long_answers,   good: sa.too_long_answers === 0 },
              ].map(({ label, value, good }) => (
                <div key={label} style={{
                  padding: "8px 14px", borderRadius: 10,
                  background: good ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)",
                  border: `1px solid ${good ? "rgba(16,185,129,0.2)" : "rgba(239,68,68,0.2)"}`,
                }}>
                  <p style={{ fontSize: 10, color: "rgba(255,255,255,0.35)", marginBottom: 2 }}>{label}</p>
                  <p style={{ fontSize: 16, fontWeight: 700, color: good ? "#10b981" : "#ef4444" }}>{String(value)}</p>
                </div>
              ))}
            </div>

            {/* Filler words bubble chart */}
            {sa.top_filler_words?.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>Filler Word Usage</p>
                <FillerBubbles fillers={sa.top_filler_words} />
              </div>
            )}

            {/* Stutter / repeats */}
            {sa.repeated_words?.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 10 }}>Repeated / Stuttered Words</p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {sa.repeated_words.map((w, i) => (
                    <span key={i} style={{ fontSize: 12, color: "#fca5a5", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 6, padding: "3px 10px" }}>
                      {w}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Per-question fluency bars */}
            {sa.per_question?.length > 0 && (
              <div>
                <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>Fluency Per Question</p>
                {sa.per_question.map((pq, i) => (
                  <FluencyBar key={i} score={pq.fluency_score} question={`Q${i + 1}: ${pq.question}`} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Behavioral flags */}
        {fb.behavioral_flags?.length > 0 && (
          <div className="fade" style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 16, padding: "22px 24px", marginBottom: 20 }}>
            <SectionHeading>⚠ Behavioural Concerns</SectionHeading>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {fb.behavioral_flags.map((flag, i) => {
                const cfg = flagConfig(flag.type);
                return (
                  <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                    <span style={{ fontSize: 16, flexShrink: 0, marginTop: 1 }}>{cfg.icon}</span>
                    <div>
                      <span style={{ fontSize: 11, fontWeight: 600, color: cfg.color, marginRight: 8 }}>{cfg.label}</span>
                      <p style={{ fontSize: 12, color: "rgba(255,255,255,0.45)", marginTop: 2, lineHeight: 1.5 }}>{flag.note}</p>
                      {flag.question && (
                        <p style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", marginTop: 4, fontStyle: "italic" }}>
                          When asked: "{flag.question.slice(0, 80)}…"
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Strengths + Improvements */}
        <div className="fade" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
          <div style={{ background: "rgba(16,185,129,0.05)", border: "1px solid rgba(16,185,129,0.18)", borderRadius: 16, padding: "20px 22px" }}>
            <p style={{ fontSize: 11, fontWeight: 600, color: "#10b981", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>✓ Strengths</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {fb.strengths?.map((s, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                  <span style={{ color: "#10b981", flexShrink: 0, marginTop: 2 }}>•</span>
                  <span style={{ fontSize: 13, color: "rgba(255,255,255,0.65)", lineHeight: 1.5 }}>{s}</span>
                </div>
              ))}
            </div>
          </div>
          <div style={{ background: "rgba(245,158,11,0.05)", border: "1px solid rgba(245,158,11,0.18)", borderRadius: 16, padding: "20px 22px" }}>
            <p style={{ fontSize: 11, fontWeight: 600, color: "#f59e0b", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 12 }}>↑ Areas to Improve</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {fb.improvements?.map((s, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                  <span style={{ color: "#f59e0b", flexShrink: 0, marginTop: 2 }}>•</span>
                  <span style={{ fontSize: 13, color: "rgba(255,255,255,0.65)", lineHeight: 1.5 }}>{s}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Question-by-question */}
        <div className="fade" style={{ marginBottom: 20 }}>
          <SectionHeading>Question-by-Question Breakdown</SectionHeading>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {fb.question_feedback?.map((qf, i) => (
              <QuestionCard key={i} qf={qf} index={i} speechData={getSpeechForQ(qf.question)} />
            ))}
          </div>
        </div>

        {/* Study plan */}
        {fb.recommended_topics?.length > 0 && (
          <div className="fade" style={{ background: "rgba(99,102,241,0.05)", border: "1px solid rgba(99,102,241,0.2)", borderRadius: 16, padding: "22px 24px", marginBottom: 20 }}>
            <SectionHeading>📚 Study Plan</SectionHeading>

            {highTopics.length > 0 && (
              <>
                <p style={{ fontSize: 11, color: "rgba(239,68,68,0.7)", marginBottom: 10, fontWeight: 500 }}>🔴 High Priority — you didn't know these</p>
                <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
                  {highTopics.map((t, i) => (
                    <div key={i} style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 10, padding: "10px 14px" }}>
                      <p style={{ fontSize: 13, fontWeight: 600, color: "#fca5a5", marginBottom: 4 }}>{t.topic}</p>
                      <p style={{ fontSize: 11, color: "rgba(255,255,255,0.35)" }}>{t.reason}</p>
                    </div>
                  ))}
                </div>
              </>
            )}

            {medTopics.length > 0 && (
              <>
                <p style={{ fontSize: 11, color: "rgba(245,158,11,0.7)", marginBottom: 10, fontWeight: 500 }}>🟡 Medium Priority — deepen your knowledge</p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {medTopics.map((t, i) => (
                    <div key={i} style={{ background: "rgba(99,102,241,0.1)", border: "1px solid rgba(99,102,241,0.25)", borderRadius: 8, padding: "6px 12px" }}>
                      <p style={{ fontSize: 12, color: "#a5b4fc", fontWeight: 500 }}>{t.topic}</p>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* CTA */}
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 20 }}>
          <button onClick={() => router.push("/interview")}
            style={{ padding: "14px 32px", background: "#6366f1", color: "white", fontWeight: 600, fontSize: 14, borderRadius: 12, border: "none", cursor: "pointer" }}>
            Practice another interview →
          </button>
        </div>

      </main>
    </div>
  );
}