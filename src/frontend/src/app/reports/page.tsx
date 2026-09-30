"use client";

import React, { useState } from "react";

export default function ReportsPage() {
  const [institutionId, setInstitutionId] = useState("INST_DEMO_CAMPUS");
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = async () => {
    setLoading(true);
    setError(null);
    try {
      const token = typeof window !== "undefined" ? sessionStorage.getItem("aicriss_jwt") : null;
      const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await fetch(`/api/v1/institutions/${institutionId}/report`, { headers });
      if (!res.ok) {
        throw new Error(`Failed to load report (Status ${res.status})`);
      }
      const data = await res.json();
      setReport(data);
    } catch (err: any) {
      setError(err.message || "Failed to load report");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-4xl mx-auto space-y-6">
        <header className="flex justify-between items-center border-b border-slate-800 pb-4">
          <div>
            <h1 className="text-2xl font-black">Executive Intelligence & Audit Reports</h1>
            <p className="text-sm text-slate-400">
              Evidence-grounded narrative dossiers and audit-grade binary PDF generation
            </p>
          </div>
          <a
            href={`/api/v1/institutions/${institutionId}/report/pdf`}
            target="_blank"
            rel="noopener noreferrer"
            className="px-4 py-2 bg-rose-600 hover:bg-rose-500 rounded-lg text-xs font-semibold text-white transition flex items-center space-x-2"
          >
            <span>📄 Export Binary PDF</span>
          </a>
        </header>

        <div className="flex space-x-3">
          <input
            type="text"
            value={institutionId}
            onChange={(e) => setInstitutionId(e.target.value)}
            className="px-4 py-2 bg-slate-900 border border-slate-800 rounded-lg text-sm font-mono text-white flex-1"
            placeholder="Enter Institution ID"
          />
          <button
            onClick={fetchReport}
            disabled={loading}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg text-xs font-semibold text-white transition"
          >
            {loading ? "Generating..." : "Load Executive Dossier"}
          </button>
        </div>

        {error && (
          <div className="p-4 bg-red-950 border border-red-800 rounded-xl text-red-300 text-xs">
            {error}
          </div>
        )}

        {report && (
          <div className="space-y-6">
            <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-xs uppercase text-slate-400 font-semibold">
                  Risk Classification
                </span>
                <span className="px-3 py-1 bg-red-950 text-red-400 border border-red-800 rounded text-xs font-mono">
                  {report.risk_level} (CRI: {report.composite_risk_index.toFixed(3)})
                </span>
              </div>
              <h2 className="text-lg font-bold text-white">Executive Summary</h2>
              <p className="text-sm text-slate-300 leading-relaxed">
                {report.executive_summary}
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl space-y-3">
                <h3 className="text-sm font-semibold uppercase text-slate-400">
                  Identified Root Causes
                </h3>
                <ul className="space-y-2 text-xs text-slate-300 list-disc list-inside">
                  {report.root_causes?.map((rc: string, i: number) => (
                    <li key={i}>{rc}</li>
                  ))}
                </ul>
              </div>

              <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl space-y-3">
                <h3 className="text-sm font-semibold uppercase text-slate-400">
                  Prioritized Interventions
                </h3>
                <ul className="space-y-2 text-xs text-slate-300 list-disc list-inside">
                  {report.prioritized_actions?.map((act: string, i: number) => (
                    <li key={i}>{act}</li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="p-4 bg-slate-900/50 border border-slate-800 rounded-xl text-xs text-slate-400">
              <span className="font-semibold text-slate-300">Provenance Disclosure: </span>
              {report.audit_provenance_summary}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
