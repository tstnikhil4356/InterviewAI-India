"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Mic } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import toast from "react-hot-toast";

export default function LoginPage() {
  const router = useRouter();
  const supabase = createClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      toast.error(error.message);
    } else {
      router.push("/dashboard");
    }
    setLoading(false);
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

      <div className="relative z-10 min-h-screen flex items-center justify-center px-4 py-16">
        <div className="w-full max-w-5xl grid gap-12 lg:grid-cols-[1.1fr_minmax(380px,1fr)] items-center">
          <div className="space-y-6">
            <div className="inline-flex items-center gap-3 rounded-full border border-white/[0.06] bg-white/[0.04] px-4 py-2 text-sm text-indigo-200">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-300">
                <Mic size={18} />
              </span>
              Welcome back to InterviewAI
            </div>
            <div className="space-y-4">
              <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-white">
                Sign in and keep your placement prep moving.
              </h1>
              <p className="max-w-2xl text-gray-400 text-base sm:text-lg leading-8">
                Practice real interview scenarios, get feedback instantly, and track your progress with the same AI engine that builds your mock interview questions.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-sm text-indigo-300 font-semibold">Built for placements</p>
                <p className="mt-3 text-gray-500 text-sm">Designed for Indian internship and full-time interview seasons.</p>
              </div>
              <div className="rounded-3xl border border-white/[0.08] bg-[#0D1117]/80 p-5">
                <p className="text-sm text-indigo-300 font-semibold">Resume-aware prep</p>
                <p className="mt-3 text-gray-500 text-sm">Use your actual resume to generate personalized questions and feedback.</p>
              </div>
            </div>
          </div>

          <div className="card p-8 shadow-2xl shadow-black/40">
            <div className="mb-8">
              <div className="text-sm uppercase tracking-[0.24em] text-indigo-300/80 mb-3">Sign in</div>
              <h2 className="text-3xl font-semibold text-white">Welcome back.</h2>
              <p className="mt-2 text-sm text-gray-500">Enter your credentials to continue your InterviewAI journey.</p>
            </div>

            <form onSubmit={handleLogin} className="space-y-5">
              <label className="block text-sm font-medium text-gray-300">
                Email
                <input
                  type="email"
                  className="input mt-2"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </label>
              <label className="block text-sm font-medium text-gray-300">
                Password
                <input
                  type="password"
                  className="input mt-2"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              <button type="submit" className="btn-primary w-full" disabled={loading}>
                {loading ? "Logging in..." : "Log in"}
              </button>
            </form>

            <p className="mt-6 text-center text-sm text-gray-500">
              Don&apos;t have an account?{' '}
              <Link href="/auth/signup" className="text-indigo-300 font-medium hover:text-indigo-200">
                Create one
              </Link>
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
