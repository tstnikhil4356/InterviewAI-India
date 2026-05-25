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
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="card w-full max-w-lg">
        <h1 className="text-xl font-bold text-gray-900 mb-1">Set up your interview</h1>
        <p className="text-gray-500 text-sm mb-8">
          Takes 30 seconds. The AI will tailor questions to your resume.
        </p>

        {/* Resume upload */}
        <div className="mb-6">
          <label className="text-sm font-medium text-gray-700 mb-2 block">Resume (PDF)</label>
          <label className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-8 cursor-pointer transition-colors
            ${resume ? "border-brand-400 bg-brand-50" : "border-gray-200 hover:border-brand-300 hover:bg-gray-50"}`}>
            <Upload size={22} className={resume ? "text-brand-600" : "text-gray-400"} />
            <span className={`mt-2 text-sm font-medium ${resume ? "text-brand-600" : "text-gray-500"}`}>
              {resume ? resume.name : "Click to upload your resume"}
            </span>
            {!resume && <span className="text-xs text-gray-400 mt-1">PDF only · max 5MB</span>}
            <input type="file" accept=".pdf" className="hidden" onChange={handleFile} />
          </label>
        </div>

        {/* Role */}
        <div className="mb-4">
          <label className="text-sm font-medium text-gray-700 mb-2 block">Target role</label>
          <div className="flex flex-wrap gap-2">
            {ROLES.map((r) => (
              <button
                key={r}
                onClick={() => setRole(r)}
                className={`px-3 py-1.5 rounded-lg text-sm border transition-colors
                  ${role === r
                    ? "bg-brand-600 text-white border-brand-600"
                    : "bg-white text-gray-600 border-gray-200 hover:border-brand-300"}`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* Experience */}
        <div className="mb-8">
          <label className="text-sm font-medium text-gray-700 mb-2 block">Experience level</label>
          <div className="flex gap-2">
            {EXPERIENCE_LEVELS.map((l) => (
              <button
                key={l}
                onClick={() => setLevel(l)}
                className={`flex-1 py-2 rounded-lg text-sm border transition-colors
                  ${level === l
                    ? "bg-brand-600 text-white border-brand-600"
                    : "bg-white text-gray-600 border-gray-200 hover:border-brand-300"}`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleStart}
          disabled={loading || !resume || !role || !level}
          className="btn-primary w-full flex items-center justify-center gap-2"
        >
          {loading ? "Preparing interview..." : <>Start interview <ChevronRight size={16} /></>}
        </button>
      </div>
    </div>
  );
}