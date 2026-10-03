"use client";

import React, { useEffect, useState } from "react";

export default function CockpitPage() {
  const [assessment, setAssessment] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = typeof window !== "undefined" ? (sessionStorage.getItem("aicriss_jwt") || localStorage.getItem("aicriss_jwt")) : null;
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const primaryInst = typeof window !== "undefined" ? (localStorage.getItem("aicriss_primary_inst") || sessionStorage.getItem("aicriss_primary_inst")) : null;

    async function loadCockpit() {
      try {
        let instId = primaryInst;
        if (!instId) {
          const instRes = await fetch("/api/v1/institutions", { headers }).catch(() => null);
          if (instRes && instRes.ok) {
            const list = await instRes.json();
            if (Array.isArray(list) && list.length > 0) {
              instId = list[0].id;
            }
          }
        }
        if (instId) {
          const res = await fetch(`/api/v1/institutions/${encodeURIComponent(instId)}/evaluate`, { headers }).catch(() => null);
          if (res && res.ok) {
            setAssessment(await res.json());
          }
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadCockpit();
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <header className="flex justify-between items-center mb-8 border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-black">AI CRISS Intelligence Cockpit</h1>
          <p className="text-sm text-slate-400">Institutional Early Warning & Crisis Detection</p>
        </div>
        <span className="px-3 py-1 rounded bg-red-950 text-red-400 border border-red-800 text-xs font-mono">
          {assessment?.risk_level || "MONITORING"}
        </span>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          <h2 className="text-xs uppercase text-slate-400 font-semibold mb-2">Composite Risk Index</h2>
          <div className="text-4xl font-mono font-bold text-white">
            {assessment ? assessment.composite_risk_index.toFixed(3) : "---"}
          </div>
          <p className="text-xs text-slate-400 mt-2">Primary threat: {assessment?.primary_driving_signal || "None"}</p>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 col-span-2">
          <h2 className="text-xs uppercase text-slate-400 font-semibold mb-2">Flagged Anomalies</h2>
          <div className="space-y-2 max-h-48 overflow-y-auto text-xs">
            {assessment?.anomalies_detected?.map((a: any, i: number) => (
              <div key={i} className="p-2 bg-slate-950 rounded border-l-2 border-amber-500">
                <span className="font-semibold">{a.signal_name} ({a.academic_year}): </span>
                {a.description}
              </div>
            )) || <p className="text-slate-500">No anomalies detected.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
