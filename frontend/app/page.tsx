"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Mic, FileText, TrendingUp, Zap, ArrowRight, ChevronRight } from "lucide-react";

function useReveal(threshold = 0.15) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setVisible(true); obs.disconnect(); } },
      { threshold }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  return { ref, visible };
}

function Counter({ target, suffix = "" }: { target: number; suffix?: string }) {
  const [count, setCount] = useState(0);
  const { ref, visible } = useReveal();
  useEffect(() => {
    if (!visible) return;
    let v = 0;
    const step = Math.ceil(target / 60);
    const t = setInterval(() => {
      v += step;
      if (v >= target) { setCount(target); clearInterval(t); }
      else setCount(v);
    }, 16);
    return () => clearInterval(t);
  }, [visible, target]);
  return <span ref={ref}>{count.toLocaleString()}{suffix}</span>;
}

const features = [
  {
    icon: FileText,
    title: "Resume-aware questions",
    desc: "The AI reads your actual resume and probes your real projects — not generic prep questions.",
    tag: "Personalised",
  },
  {
    icon: Mic,
    title: "Voice-first interviews",
    desc: "Speak naturally. Get transcribed, scored, and critiqued — exactly like the real thing.",
    tag: "Immersive",
  },
  {
    icon: Zap,
    title: "Instant scoring",
    desc: "Confidence, depth, and clarity scored per answer with actionable improvement notes.",
    tag: "Feedback",
  },
  {
    icon: TrendingUp,
    title: "Progress analytics",
    desc: "A personal dashboard tracking your growth across every session you've done.",
    tag: "Analytics",
  },
];

const stats = [
  { value: 12000, suffix: "+", label: "Students prepared" },
  { value: 95,    suffix: "%", label: "Satisfaction rate"  },
  { value: 500,   suffix: "+", label: "Companies covered"  },
];

const steps = [
  { num: "01", title: "Upload your resume", desc: "Drop your PDF. The AI parses every project, role, and skill in seconds." },
  { num: "02", title: "Pick a role & level",  desc: "SDE, Data Analyst, PM — junior to senior. Fully contextualised." },
  { num: "03", title: "Interview live",        desc: "Real-time voice interview with follow-up questions and silence detection." },
  { num: "04", title: "Review & improve",      desc: "Get a full scorecard. Retry weak answers. Ship a better version of yourself." },
];

export default function HomePage() {
  const [scrolled, setScrolled] = useState(false);
  const heroRef = useRef<HTMLDivElement>(null);
  const stepsReveal = useReveal();
  const statsReveal = useReveal();

  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 30);
    window.addEventListener("scroll", fn);
    return () => window.removeEventListener("scroll", fn);
  }, []);

  return (
    <main className="min-h-screen bg-[#080C14] text-white overflow-x-hidden">

      {/* ── Noise texture overlay ─────────────────────────────── */}
      <div className="pointer-events-none fixed inset-0 z-0 opacity-[0.03]"
        style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")", backgroundSize: "200px" }} />

      {/* ── Ambient glow ──────────────────────────────────────── */}
      <div className="pointer-events-none fixed top-0 left-1/2 -translate-x-1/2 w-[800px] h-[500px] rounded-full bg-indigo-600/10 blur-[120px] z-0" />

      {/* ── Navbar ───────────────────────────────────────────── */}
      <nav className={`fixed top-0 inset-x-0 z-50 transition-all duration-500 ${
        scrolled ? "bg-[#080C14]/90 backdrop-blur-xl border-b border-white/[0.06]" : "bg-transparent"
      }`}>
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center">
              <Mic size={14} className="text-white" />
            </div>
            <span className="font-semibold text-white text-lg tracking-tight">
              interview<span className="text-indigo-400">ai</span>
            </span>
          </div>

          <div className="hidden sm:flex items-center gap-3">
            <Link
              href="/auth/login"
              className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors duration-150"
            >
              Sign in
            </Link>
            <Link
              href="/auth/signup"
              className="group flex items-center gap-1.5 px-4 py-2 bg-indigo-500 hover:bg-indigo-400 text-white text-sm font-medium rounded-lg transition-all duration-200 hover:scale-105 active:scale-95 shadow-lg shadow-indigo-500/25"
            >
              Get started
              <ChevronRight size={14} className="group-hover:translate-x-0.5 transition-transform" />
            </Link>
          </div>
        </div>
      </nav>

      {/* ── Hero ─────────────────────────────────────────────── */}
      <section ref={heroRef} className="relative min-h-screen flex flex-col items-center justify-center text-center px-6 pt-24 pb-32">

        {/* Grid background */}
        <div className="absolute inset-0 z-0"
          style={{
            backgroundImage: "linear-gradient(rgba(255,255,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,0.03) 1px,transparent 1px)",
            backgroundSize: "80px 80px",
          }} />

        <div className="relative z-10 max-w-4xl mx-auto">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 text-xs font-medium px-3 py-1.5 rounded-full mb-8 animate-fade-in">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
            Now in beta — free for Indian students
          </div>

          {/* Headline */}
          <h1 className="text-5xl sm:text-7xl font-bold leading-[1.05] tracking-tight mb-6 animate-hero-up">
            <span className="text-white">Ace every</span>
            <br />
            <span className="bg-gradient-to-r from-indigo-400 via-violet-400 to-indigo-300 bg-clip-text text-transparent">
              placement interview.
            </span>
          </h1>

          <p className="text-gray-400 text-lg sm:text-xl max-w-2xl mx-auto leading-relaxed mb-10 animate-hero-up" style={{ animationDelay: "0.08s" }}>
            AI-powered mock interviews that read your resume, ask real follow-ups,
            and score you instantly — built for Indian placement seasons.
          </p>

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 animate-hero-up" style={{ animationDelay: "0.16s" }}>
            <Link
              href="/auth/signup"
              className="group flex items-center gap-2 px-6 py-3.5 bg-indigo-500 hover:bg-indigo-400 text-white font-semibold rounded-xl transition-all duration-200 hover:scale-105 active:scale-95 shadow-2xl shadow-indigo-500/30 text-sm"
            >
              Start free interview
              <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
            </Link>
            <Link
              href="/auth/login"
              className="flex items-center gap-2 px-6 py-3.5 border border-white/10 hover:border-white/25 text-gray-300 hover:text-white font-medium rounded-xl transition-all duration-200 text-sm"
            >
              Sign in
            </Link>
          </div>

          {/* Social proof avatars */}
          <div className="flex items-center justify-center gap-3 mt-10 animate-hero-up" style={{ animationDelay: "0.24s" }}>
            <div className="flex -space-x-2">
              {["A","R","P","S","K"].map((l, i) => (
                <div key={i} className="w-8 h-8 rounded-full border-2 border-[#080C14] flex items-center justify-center text-xs font-semibold"
                  style={{ background: ["#6366f1","#8b5cf6","#06b6d4","#10b981","#f59e0b"][i] }}>
                  {l}
                </div>
              ))}
            </div>
            <p className="text-gray-500 text-sm">
              <span className="text-white font-medium">2,400+</span> interviews this week
            </p>
          </div>
        </div>

        {/* Hero mock terminal card */}
        <div className="relative z-10 mt-20 w-full max-w-2xl mx-auto animate-hero-up" style={{ animationDelay: "0.32s" }}>
          <div className="bg-[#0D1117] border border-white/[0.08] rounded-2xl overflow-hidden shadow-2xl shadow-black/60">
            {/* Terminal bar */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06] bg-white/[0.02]">
              <div className="w-3 h-3 rounded-full bg-red-500/70" />
              <div className="w-3 h-3 rounded-full bg-yellow-500/70" />
              <div className="w-3 h-3 rounded-full bg-green-500/70" />
              <span className="ml-2 text-xs text-gray-500 font-mono">interview session — sde-intern</span>
            </div>
            <div className="p-6 font-mono text-sm space-y-3">
              <div className="flex gap-3">
                <span className="text-indigo-400 shrink-0">AI</span>
                <span className="text-gray-300">You mentioned building a recommendation system in your resume. Walk me through your approach to handling the cold start problem.</span>
              </div>
              <div className="flex gap-3">
                <span className="text-emerald-400 shrink-0">You</span>
                <span className="text-gray-400">For new users I used a hybrid approach — content-based filtering for the first few interactions, then gradually shifting to collaborative filtering as I collected enough...</span>
              </div>
              <div className="flex items-center gap-2 pt-1">
                <div className="w-2 h-4 bg-indigo-400 rounded-sm animate-pulse" />
                <span className="text-gray-600 text-xs">AI is formulating follow-up...</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Stats ─────────────────────────────────────────────── */}
      <section ref={statsReveal.ref} className="relative z-10 border-y border-white/[0.06] bg-white/[0.02]">
        <div className="max-w-4xl mx-auto px-6 py-12 grid grid-cols-1 sm:grid-cols-3 gap-8 text-center">
          {stats.map(({ value, suffix, label }, i) => (
            <div key={label}
              className={`transition-all duration-700 ${statsReveal.visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"}`}
              style={{ transitionDelay: `${i * 100}ms` }}>
              <div className="text-4xl font-bold text-white tabular-nums mb-1">
                <Counter target={value} suffix={suffix} />
              </div>
              <p className="text-sm text-gray-500">{label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────── */}
      <section className="relative z-10 py-28 px-6">
        <FeaturesSection />
      </section>

      {/* ── How it works ──────────────────────────────────────── */}
      <section ref={stepsReveal.ref} className="relative z-10 py-28 px-6 border-t border-white/[0.06]">
        <div className="max-w-5xl mx-auto">
          <div className={`text-center mb-16 transition-all duration-700 ${stepsReveal.visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"}`}>
            <span className="text-xs font-semibold text-indigo-400 uppercase tracking-widest mb-3 block">How it works</span>
            <h2 className="text-3xl sm:text-4xl font-bold text-white">From zero to offer-ready in 4 steps</h2>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {steps.map(({ num, title, desc }, i) => (
              <div key={num}
                className={`relative transition-all duration-700 ${stepsReveal.visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"}`}
                style={{ transitionDelay: `${i * 120}ms` }}>
                <div className="bg-[#0D1117] border border-white/[0.07] rounded-2xl p-6 h-full hover:border-indigo-500/30 hover:-translate-y-1 transition-all duration-300 group">
                  <span className="text-4xl font-bold text-white/5 group-hover:text-indigo-500/20 transition-colors font-mono">{num}</span>
                  <h3 className="text-white font-semibold mt-2 mb-2 text-sm">{title}</h3>
                  <p className="text-gray-500 text-xs leading-relaxed">{desc}</p>
                </div>
                {i < steps.length - 1 && (
                  <div className="hidden lg:block absolute top-1/2 -right-3 -translate-y-1/2 z-10">
                    <ChevronRight size={16} className="text-white/10" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ───────────────────────────────────────────────── */}
      <CTASection />

      {/* ── Footer ────────────────────────────────────────────── */}
      <footer className="relative z-10 border-t border-white/[0.06] py-10 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-1.5">
            <div className="w-6 h-6 rounded-md bg-indigo-500 flex items-center justify-center">
              <Mic size={12} className="text-white" />
            </div>
            <span className="text-white font-medium text-sm">interviewai</span>
          </div>
          <p className="text-gray-600 text-xs">© {new Date().getFullYear()} InterviewAI. Built for India&apos;s placement season.</p>
          <div className="flex gap-4 text-xs text-gray-600">
            <Link href="#" className="hover:text-gray-400 transition-colors">Privacy</Link>
            <Link href="#" className="hover:text-gray-400 transition-colors">Terms</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}

// ── Features section ──────────────────────────────────────────────
function FeaturesSection() {
  const { ref, visible } = useReveal();
  return (
    <div ref={ref} className="max-w-6xl mx-auto">
      <div className={`text-center mb-16 transition-all duration-700 ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-6"}`}>
        <span className="text-xs font-semibold text-indigo-400 uppercase tracking-widest mb-3 block">Features</span>
        <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
          Built different. Built for results.
        </h2>
        <p className="text-gray-500 max-w-lg mx-auto">
          Not another question bank. A full interview simulator that knows who you are.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {features.map(({ icon: Icon, title, desc, tag }, i) => (
          <div key={title}
            className={`group bg-[#0D1117] border border-white/[0.07] rounded-2xl p-7 hover:border-indigo-500/25 hover:bg-indigo-500/[0.03] transition-all duration-300 cursor-default ${
              visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"
            }`}
            style={{ transitionDelay: `${i * 100}ms` }}>
            <div className="flex items-start justify-between mb-5">
              <div className="w-11 h-11 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center group-hover:bg-indigo-500/20 group-hover:border-indigo-500/40 transition-all duration-300">
                <Icon size={20} className="text-indigo-400" />
              </div>
              <span className="text-xs text-indigo-400/70 border border-indigo-500/20 bg-indigo-500/5 px-2.5 py-1 rounded-full font-medium">
                {tag}
              </span>
            </div>
            <h3 className="text-white font-semibold text-base mb-2">{title}</h3>
            <p className="text-gray-500 text-sm leading-relaxed">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── CTA section ───────────────────────────────────────────────────
function CTASection() {
  const { ref, visible } = useReveal();
  return (
    <section ref={ref} className="relative z-10 py-28 px-6">
      <div className={`max-w-3xl mx-auto text-center transition-all duration-700 ${visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"}`}>
        <div className="relative bg-[#0D1117] border border-white/[0.08] rounded-3xl px-8 py-16 overflow-hidden">
          {/* Glow */}
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-64 h-32 bg-indigo-500/20 blur-[60px] rounded-full" />
          <div className="relative z-10">
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">
              Your next interview is closer<br />than you think.
            </h2>
            <p className="text-gray-500 mb-8 text-base">
              Start practicing in under 60 seconds. No credit card required.
            </p>
            <Link
              href="/auth/signup"
              className="group inline-flex items-center gap-2 px-7 py-3.5 bg-indigo-500 hover:bg-indigo-400 text-white font-semibold rounded-xl transition-all duration-200 hover:scale-105 active:scale-95 shadow-2xl shadow-indigo-500/30 text-sm"
            >
              Create free account
              <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}