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
    <div className="min-h-screen bg-gray-50">
      {/* Topbar */}
      <nav className="bg-white border-b border-gray-100 px-6 py-4 flex items-center justify-between">
        <span className="font-semibold text-brand-600">InterviewAI</span>
        <Link href="/interview" className="btn-primary flex items-center gap-2 text-sm">
          <Plus size={16} /> New interview
        </Link>
      </nav>

      <div className="max-w-4xl mx-auto px-6 py-10">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Good morning 👋</h1>
        <p className="text-gray-500 mb-8">Ready to practice today?</p>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4 mb-10">
          {[
            { label: "Interviews done",  value: mockStats.totalInterviews, icon: Mic },
            { label: "Avg confidence",   value: `${mockStats.avgConfidence}%`, icon: TrendingUp },
            { label: "Day streak",       value: mockStats.streak, icon: FileText },
          ].map(({ label, value, icon: Icon }) => (
            <div key={label} className="card flex flex-col gap-2">
              <div className="flex items-center gap-2 text-gray-400">
                <Icon size={15} />
                <span className="text-xs">{label}</span>
              </div>
              <span className="text-2xl font-bold text-gray-900">{value}</span>
            </div>
          ))}
        </div>

        {/* CTA / empty state */}
        <div className="card flex flex-col items-center text-center py-12 gap-4">
          <div className="w-14 h-14 bg-brand-50 rounded-2xl flex items-center justify-center">
            <Mic size={28} className="text-brand-600" />
          </div>
          <h2 className="text-lg font-semibold text-gray-900">Start your first mock interview</h2>
          <p className="text-gray-500 text-sm max-w-xs">
            Upload your resume, pick a role, and our AI will interview you based on your actual projects.
          </p>
          <Link href="/interview" className="btn-primary">
            Start interview
          </Link>
        </div>
      </div>
    </div>
  );
}
