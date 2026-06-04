"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, ChevronRight } from "lucide-react";
import toast from "react-hot-toast";

const ROLES = [
  "Software Engineer",
  "Frontend Developer",
  "Backend Developer",
  "Full Stack Developer",
  "Data Analyst",
  "Data Scientist",
  "Machine Learning Engineer",
  "Product Manager",
  "DevOps Engineer",
];

const EXPERIENCE_LEVELS = ["Fresher (0–1 yr)", "Junior (1–3 yrs)", "Mid (3–5 yrs)"];

export default function InterviewSetupPage() {
  const router = useRouter();
  const [resume, setResume]   = useState<File | null>(null);
  const [role, setRole]       = useState("");
  const [level, setLevel]     = useState("");
  const [loading, setLoading] = useState(false);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      toast.error("Please upload a PDF resume.");
      return;
    }
    setResume(file);
  }

  async function handleStart() {
    if (!resume) { toast.error("Please upload your resume."); return; }
    if (!role)   { toast.error("Please select a role."); return; }
    if (!level)  { toast.error("Please select experience level."); return; }

    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("resume", resume);
      formData.append("role", role);
      formData.append("level", level);

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/interview/start`,
        { method: "POST", body: formData }
      );

      if (!res.ok) throw new Error("Failed to start interview");
      const data = await res.json();
      const { session_id, question } = data;

      // ✅ Store the first question so the session page doesn't need to call /session
      // (prevents the 404 if the server-side in-memory dict was flushed)
      if (question) {
        sessionStorage.setItem(`session_${session_id}`, JSON.stringify({ question }));
      }

      router.push(`/interview/${session_id}`);
    } catch {
      toast.error("Something went wrong. Make sure the backend is running.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#080C14] text-white overflow-hidden">
      <div className="pointer-events-none fixed inset-0 z-0 opacity-[0.03]"
        style={{
          backgroundImage: "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          backgroundSize: "200px",
        }}
      />
      <div className="pointer-events-none fixed top-0 left-1/2 -translate-x-1/2 w-[700px] h-[400px] rounded-full bg-indigo-600/10 blur-[120px] z-0" />

      <div className="relative z-10 mx-auto max-w-6xl px-6 py-12">
        <div className="grid gap-10 lg:grid-cols-[1.2fr_minmax(420px,1fr)] items-center">
          <section className="space-y-6">
            <div className="inline-flex items-center gap-3 rounded-full border border-white/[0.08] bg-white/[0.04] px-4 py-2 text-sm text-indigo-200">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-300">
                <Upload size={18} />
              </span>
              Set up your AI interview
            </div>
            <div className="space-y-4">
              <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-white">
                Build a resume-aware mock interview in seconds.
              </h1>
              <p className="max-w-2xl text-gray-400 text-base sm:text-lg leading-8">
                Upload your resume, choose a role and level, and start a live mock interview tailored to your actual experience.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-xs uppercase tracking-[0.24em] text-indigo-300/80 mb-3">Personalised</p>
                <p className="text-gray-400 text-sm">Tailored questions based on your resume and chosen role.</p>
              </div>
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-xs uppercase tracking-[0.24em] text-indigo-300/80 mb-3">Fast setup</p>
                <p className="text-gray-400 text-sm">Start practicing in under a minute — no extra steps required.</p>
              </div>
            </div>
          </section>

          <section className="card p-8 shadow-2xl shadow-black/40">
            <div className="mb-8">
              <p className="text-xs uppercase tracking-[0.24em] text-indigo-300/80 mb-3">Interview setup</p>
              <h2 className="text-3xl font-semibold text-white">Ready for your first question?</h2>
              <p className="mt-2 text-gray-500 text-sm">This setup creates a session tailored to your resume, role, and experience level.</p>
            </div>

            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Resume (PDF)</label>
                <label className={`group flex cursor-pointer flex-col items-center justify-center rounded-3xl border-2 border-dashed p-8 text-center transition-all ${resume ? "border-indigo-400/40 bg-indigo-500/10" : "border-white/10 hover:border-indigo-400/30 hover:bg-white/5"}`}>
                  <Upload size={22} className={resume ? "text-indigo-300" : "text-white/40"} />
                  <span className={`mt-3 text-sm font-medium ${resume ? "text-white" : "text-gray-400"}`}>
                    {resume ? resume.name : "Click to upload your resume"}
                  </span>
                  {!resume && <span className="mt-2 text-xs text-gray-500">PDF only · max 5MB</span>}
                  <input type="file" accept=".pdf" className="hidden" onChange={handleFile} />
                </label>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Target role</label>
                <div className="flex flex-wrap gap-2">
                  {ROLES.map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setRole(r)}
                      className={`rounded-2xl px-4 py-2 text-sm font-medium transition-all ${role === r ? "bg-indigo-500 text-white border border-indigo-500/40" : "bg-white/5 text-gray-300 border border-white/10 hover:bg-white/10"}`}>
                      {r}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Experience level</label>
                <div className="flex flex-col gap-3 sm:flex-row">
                  {EXPERIENCE_LEVELS.map((l) => (
                    <button
                      key={l}
                      type="button"
                      onClick={() => setLevel(l)}
                      className={`flex-1 rounded-2xl px-4 py-3 text-sm font-medium transition-all ${level === l ? "bg-indigo-500 text-white border border-indigo-500/40" : "bg-white/5 text-gray-300 border border-white/10 hover:bg-white/10"}`}>
                      {l}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <button
              onClick={handleStart}
              disabled={loading || !resume || !role || !level}
              className="btn-primary w-full mt-6 flex items-center justify-center gap-2"
            >
              {loading ? "Preparing interview..." : <>Start interview <ChevronRight size={16} /></>}
            </button>
          </section>
        </div>
      </div>
    </main>
  );
}
