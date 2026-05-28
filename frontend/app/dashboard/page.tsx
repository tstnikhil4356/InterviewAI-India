import Link from "next/link";
import { Mic, FileText, TrendingUp, Plus } from "lucide-react";

// TODO: fetch real data from Supabase
const mockStats = {
  totalInterviews: 0,
  avgConfidence: 0,
  streak: 0,
};

export default function DashboardPage() {
  return (
    <main className="min-h-screen bg-[#080C14] text-white overflow-hidden">
      <div className="pointer-events-none fixed inset-0 z-0 opacity-[0.03]"
        style={{
          backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          backgroundSize: "200px",
        }}
      />
      <div className="pointer-events-none fixed top-0 left-1/2 -translate-x-1/2 w-[700px] h-[400px] rounded-full bg-indigo-600/10 blur-[120px] z-0" />

      <nav className="relative z-10 border-b border-white/[0.06] bg-[#0B0F18]/90 backdrop-blur-xl px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-2xl bg-indigo-500 flex items-center justify-center text-white">
              <Mic size={16} />
            </div>
            <span className="text-lg font-semibold tracking-tight text-white">
              interview<span className="text-indigo-400">ai</span>
            </span>
          </div>
          <Link href="/interview" className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition-all duration-200 hover:bg-indigo-400 hover:scale-105 active:scale-95 shadow-lg shadow-indigo-500/25">
            <Plus size={16} /> New interview
          </Link>
        </div>
      </nav>

      <section className="relative z-10 max-w-6xl mx-auto px-6 py-14">
        <div className="grid gap-12 lg:grid-cols-[1.2fr_minmax(380px,1fr)] items-start">
          <div className="space-y-6">
            <div className="inline-flex items-center gap-3 rounded-full border border-white/[0.08] bg-white/[0.04] px-4 py-2 text-sm text-indigo-200">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-300">
                <Mic size={18} />
              </span>
              Dashboard overview
            </div>
            <div className="space-y-4">
              <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-white">
                Your placement prep, all in one place.
              </h1>
              <p className="max-w-2xl text-gray-400 text-base sm:text-lg leading-8">
                Track interviews, confidence, and streaks while staying ready for the next opportunity.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-sm text-indigo-300 font-semibold">Resume-aware coaching</p>
                <p className="mt-3 text-gray-500 text-sm">Your practice sessions improve with each real resume-based interview.</p>
              </div>
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-sm text-indigo-300 font-semibold">Actionable progress</p>
                <p className="mt-3 text-gray-500 text-sm">See your metrics and focus on the areas that matter most.</p>
              </div>
            </div>
          </div>

          <div className="rounded-[2rem] border border-white/[0.08] bg-[#0D1117]/70 p-8 shadow-2xl shadow-black/40 backdrop-blur-xl">
            <div className="flex items-center justify-between gap-3 mb-6">
              <div>
                <p className="text-sm uppercase tracking-[0.24em] text-indigo-300/80">Quick stats</p>
                <h2 className="mt-3 text-2xl font-semibold text-white">Session summary</h2>
              </div>
              <div className="inline-flex rounded-3xl bg-white/[0.05] px-3 py-2 text-xs text-indigo-200">
                Live metrics
              </div>
            </div>
            <div className="grid gap-4">
              {[
                { label: "Interviews done", value: mockStats.totalInterviews, icon: Mic },
                { label: "Avg confidence", value: `${mockStats.avgConfidence}%`, icon: TrendingUp },
                { label: "Day streak", value: mockStats.streak, icon: FileText },
              ].map(({ label, value, icon: Icon }) => (
                <div key={label} className="rounded-3xl border border-white/[0.06] bg-white/[0.02] p-5">
                  <div className="flex items-center justify-between gap-3 text-sm text-gray-400">
                    <span>{label}</span>
                    <Icon size={16} />
                  </div>
                  <p className="mt-4 text-3xl font-bold text-white">{value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-12 rounded-[2rem] border border-white/[0.08] bg-[#0D1117]/80 p-10 shadow-2xl shadow-black/35">
          <div className="grid gap-8 lg:grid-cols-[1fr_320px] items-center">
            <div>
              <p className="text-sm uppercase tracking-[0.24em] text-indigo-300/80">Ready to practice</p>
              <h2 className="mt-4 text-3xl font-semibold text-white">Start your next mock interview now.</h2>
              <p className="mt-3 text-gray-400 max-w-xl">
                Upload your resume, choose a role, and get an interview flow built around your real experience.
              </p>
            </div>
            <Link href="/interview" className="inline-flex items-center justify-center rounded-3xl bg-indigo-500 px-6 py-4 text-sm font-semibold text-white transition-all duration-200 hover:bg-indigo-400 hover:scale-105 active:scale-95 shadow-2xl shadow-indigo-500/30">
              <Plus size={18} /> Start interview
            </Link>
          </div>
        </div>
      </section>
    </main>
  );
}
