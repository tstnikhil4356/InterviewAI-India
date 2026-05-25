const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const api = {
  async startInterview(formData: FormData) {
    const res = await fetch(`${BASE}/api/interview/start`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async nextQuestion(sessionId: string, transcript: string) {
    const res = await fetch(`${BASE}/api/interview/next-question`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, transcript }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async endInterview(sessionId: string) {
    const res = await fetch(`${BASE}/api/interview/end`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getReport(sessionId: string) {
    const res = await fetch(`${BASE}/api/interview/report/${sessionId}`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
};
