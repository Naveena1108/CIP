import Link from "next/link";
import React from "react";

export default function HomePage() {
  return (
    <div className="min-h-screen flex flex-col justify-center items-center p-6 bg-slate-950 text-slate-100">
      <div className="max-w-xl w-full text-center space-y-6">
        <div className="inline-block px-3 py-1 rounded-full bg-blue-950 border border-blue-800 text-blue-400 text-xs font-mono uppercase tracking-widest">
          AI CRISS Intelligence Platform
        </div>
        <h1 className="text-4xl font-extrabold tracking-tight">
          Cross-Signal Early Warning & Crisis Detection
        </h1>
        <p className="text-slate-400 text-sm">
          Multi-signal statistical anomaly detection, deterministic Composite Risk Index (CRI) calculation, autoregressive forecasting, and audit-grade reporting.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4">
          <Link
            href="/cockpit"
            className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-blue-500 transition text-center"
          >
            <div className="text-lg font-bold text-white mb-1">Cockpit</div>
            <div className="text-xs text-slate-400">Real-time risk scoring and anomaly telemetry</div>
          </Link>

          <Link
            href="/simulator"
            className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-indigo-500 transition text-center"
          >
            <div className="text-lg font-bold text-white mb-1">Simulator</div>
            <div className="text-xs text-slate-400">Autoregressive forward policy intervention modeling</div>
          </Link>

          <Link
            href="/reports"
            className="p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-emerald-500 transition text-center"
          >
            <div className="text-lg font-bold text-white mb-1">Reports</div>
            <div className="text-xs text-slate-400">Audit-grade narrative dossiers & binary PDF export</div>
          </Link>
        </div>
      </div>
    </div>
  );
}
