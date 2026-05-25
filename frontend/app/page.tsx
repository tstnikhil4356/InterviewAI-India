import Link from "next/link";
import { Mic, FileText, TrendingUp, Zap } from "lucide-react";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col">
      {/* Navbar */}
      <nav className="border-b border-gray-100 bg-white px-6 py-4 flex items-center justify-between">
        <span className="font-semibold text-brand-600 text-lg">InterviewAI</span>
        <div className="flex items-center gap-3">
          <Link href="/auth/login" className="btn-secondary text-sm">Log in</Link>
          <Link href="/auth/signup" className="btn-primary text-sm">Get started free</Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="flex-1 flex flex-col items-center justify-center text-center px-6 py-20">
        <span className="inline-block bg-brand-50 text-brand-600 text-sm font-medium px-3 py-1 rounded-full mb-6">
          Built for Indian students 🇮🇳
        </span>
        <h1 className="text-4xl sm:text-5xl font-bold text-gray-900 max-w-2xl leading-tight mb-4">
          Your personal AI placement coach
        </h1>
        <p className="text-gray-500 text-lg max-w-xl mb-8">
          Upload your resume, pick a role, and practice with an AI interviewer that asks
          real follow-up questions based on your actual projects.
        </p>
        <Link href="/auth/signup" className="btn-primary text-base px-6 py-3">
          Start your first interview — free
        </Link>
      </section>

      {/* Features */}
      <section className="bg-white border-t border-gray-100 px-6 py-16">
        <div className="max-w-4xl mx-auto grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-6">
          {[
            { icon: FileText, title: "Resume-based questions", desc: "AI reads your projects and asks exactly what interviewers ask." },
            { icon: Mic,      title: "Real voice interviews", desc: "Speak your answers. Feel the real interview pressure." },
            { icon: Zap,      title: "Instant feedback",     desc: "Confidence, clarity, depth — scored after every answer." },
            { icon: TrendingUp, title: "Track progress",    desc: "See your week-by-week improvement across interviews." },
          ].map(({ icon: Icon, title, desc }) => (
            <div key={title} className="flex flex-col gap-3">
              <div className="w-10 h-10 bg-brand-50 rounded-lg flex items-center justify-center">
                <Icon size={20} className="text-brand-600" />
              </div>
              <h3 className="font-semibold text-gray-900 text-sm">{title}</h3>
              <p className="text-gray-500 text-sm leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
