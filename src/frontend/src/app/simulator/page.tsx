"use client";

import React, { useState } from "react";

export default function SimulatorPage() {
  const [placementBoost, setPlacementBoost] = useState(5.0);
  const [vacancyReduction, setVacancyReduction] = useState(8.0);
  const [projection, setProjection] = useState<any>(null);

  const runSimulation = async () => {
    const token = typeof window !== "undefined" ? sessionStorage.getItem("aicriss_jwt") : null;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch("/api/v1/institutions/INST_DEMO_CAMPUS/simulate", {
      method: "POST",
      headers,
      body: JSON.stringify({
        years_forward: 3,
        intervention_effects: {
          placement_boost: placementBoost,
          vacancy_rate_reduction: vacancyReduction / 100.0,
        },
      }),
    });
    if (res.ok) {
      setProjection(await res.json());
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <h1 className="text-2xl font-black mb-6">Forward Trajectory Policy Simulator</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <div className="bg-slate-900 p-6 rounded-xl border border-slate-800 space-y-4">
          <h2 className="text-sm font-semibold text-slate-400 uppercase">Policy Levers</h2>
          <div>
            <label className="text-xs text-slate-300 block mb-1">Corporate Placement Boost: +{placementBoost}%</label>
            <input
              type="range"
              min="0"
              max="20"
              value={placementBoost}
              onChange={(e) => setPlacementBoost(parseFloat(e.target.value))}
              className="w-full accent-blue-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-300 block mb-1">Seat Matrix Quota Reduction: -{vacancyReduction}%</label>
            <input
              type="range"
              min="0"
              max="25"
              value={vacancyReduction}
              onChange={(e) => setVacancyReduction(parseFloat(e.target.value))}
              className="w-full accent-blue-500"
            />
          </div>
          <button onClick={runSimulation} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-semibold text-white">
            Simulate 3-Year Trajectory
          </button>
        </div>

        <div className="bg-slate-900 p-6 rounded-xl border border-slate-800">
          <h2 className="text-sm font-semibold text-slate-400 uppercase mb-4">Simulation Results</h2>
          {projection ? (
            <div className="space-y-3 text-xs">
              <p>Current CRI: <span className="font-mono font-bold text-white">{projection.current_cri.toFixed(3)}</span></p>
              <p className="text-emerald-400 font-semibold">Risk Reduction: -{projection.risk_reduction_achieved.toFixed(3)} CRI</p>
            </div>
          ) : (
            <p className="text-xs text-slate-500">Configure sliders and click simulate.</p>
          )}
        </div>
      </div>
    </div>
  );
}
