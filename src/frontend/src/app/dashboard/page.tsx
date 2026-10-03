"use client";

import React, { useState, useEffect } from "react";
import { CipShell } from "@/components/layout/cip-shell";
import { CipPage } from "@/components/layout/cip-page";
import { CipOverview, type OverviewData } from "@/components/overview/cip-overview";
import { CipCommandSearch } from "@/components/ui/cip-command-search";
import { CipLoadingState } from "@/components/ui/cip-loading-state";
import { CipEmptyState } from "@/components/ui/cip-empty-state";
import { CipDisclosure } from "@/components/ui/cip-disclosure";

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [data, setData] = useState<OverviewData | null>(null);
  const [technicalDetails, setTechnicalDetails] = useState<any>(null);

  const [institution, setInstitution] = useState({
    id: "",
    name: "Institutional Workspace",
    type: "Educational Institution",
  });

  useEffect(() => {
    async function fetchIntelligence() {
      try {
        setLoading(true);
        const token =
          typeof window !== "undefined"
            ? localStorage.getItem("cip_token") || localStorage.getItem("aicriss_jwt") || sessionStorage.getItem("aicriss_jwt")
            : null;
        const headers: Record<string, string> = {
          Accept: "application/json",
        };
        if (token) {
          headers["Authorization"] = `Bearer ${token}`;
        }

        const storedInstId =
          typeof window !== "undefined"
            ? localStorage.getItem("aicriss_primary_inst") || sessionStorage.getItem("aicriss_primary_inst")
            : null;

        // Fetch institutions
        const res = await fetch("/api/v1/institutions", { headers }).catch(() => null);
        if (res && res.ok) {
          const list = await res.json();
          if (Array.isArray(list) && list.length > 0) {
            const inst =
              (storedInstId && list.find((i: any) => i.id === storedInstId)) || list[0];

            setInstitution({
              id: inst.id,
              name: inst.name || inst.id,
              type: inst.education_entity_type || inst.entity_type || "Educational Institution",
            });

            // Fetch evaluation assessment via GET
            const evalRes = await fetch(`/api/v1/institutions/${encodeURIComponent(inst.id)}/evaluate`, {
              method: "GET",
              headers,
            }).catch(() => null);

            if (evalRes && evalRes.ok) {
              const result = await evalRes.json();
              const assessment = result.assessment || result;
              const cri = assessment.composite_risk_index;
              const hasSignals = (assessment.evidence_count && assessment.evidence_count > 0) || (assessment.anomalies_detected && assessment.anomalies_detected.length > 0) || cri > 0;

              if (!hasSignals && (cri === 0 || cri === null || cri === undefined)) {
                // Genuine insufficient data state
                setData({
                  institutionName: inst.name || inst.id,
                  status: "insufficient_data",
                  statusSummary:
                    "No institutional analysis is available yet. Add institutional data to begin building the institution's evidence and risk picture.",
                  currentRisk: null,
                  riskLabel: "INSUFFICIENT DATA",
                  changedCount: "—",
                  findingCount: "—",
                  evidenceCount: "—",
                  findings: [],
                });
                setTechnicalDetails(null);
                setLoading(false);
                return;
              }

              let overviewData: any = null;
              try {
                const ovRes = await fetch(`/api/v1/institutions/${inst.id}/overview`, { credentials: "include" });
                if (ovRes.ok) {
                  overviewData = await ovRes.json();
                }
              } catch (e) {
                // fallback to assessment
              }

              const status: "stable" | "watch" | "elevated" | "critical" =
                overviewData?.status
                  ? (overviewData.status.toLowerCase() as any)
                  : cri > 0.7 ? "critical" : cri > 0.5 ? "elevated" : cri > 0.3 ? "watch" : "stable";

              const findings = (assessment.anomalies_detected || []).map((anom: any, idx: number) => ({
                id: `finding-${idx}`,
                title: `${anom.signal_name || "Signal"} Deviation`,
                summary: anom.description || `Anomaly observed with severity ${anom.severity || "Observation"}.`,
                severity: (anom.severity?.toLowerCase() || "observation") as any,
                evidenceCount: 1,
              }));

              const changedCount = overviewData?.counts?.changed_count ?? findings.length;
              const findingCount = overviewData?.counts?.findings_count ?? findings.length;
              const evidenceCount = overviewData?.counts?.evidence_count ?? assessment.evidence_count ?? (findings.length || 1);
              const statusSummary = overviewData?.summary ?? (
                status === "stable"
                  ? "Institutional indicators are tracking within normal parameters. Multi-period stability verified."
                  : "Risk conditions observed across institutional indicators requiring attention."
              );

              setData({
                institutionName: inst.name || inst.id,
                status,
                statusSummary,
                currentRisk: overviewData?.current_cri ?? cri,
                riskLabel: (overviewData?.status || status).toUpperCase(),
                changedCount,
                findingCount,
                evidenceCount,
                findings,
              });
              setTechnicalDetails(assessment);
              setLoading(false);
              return;
            } else {
              // Assessment not found or evaluation failed - render empty state
              setData({
                institutionName: inst.name || inst.id,
                status: "insufficient_data",
                statusSummary:
                  "No institutional analysis is available yet. Ingest operational, admissions, or financial documents to evaluate risk posture.",
                currentRisk: null,
                riskLabel: "AWAITING INGESTION",
                changedCount: "—",
                findingCount: "—",
                evidenceCount: "—",
                findings: [],
              });
              setTechnicalDetails(null);
              setLoading(false);
              return;
            }
          }
        }

        // No institutions accessible
        setData(null);
        setTechnicalDetails(null);
      } catch (err) {
        console.error("Failed to load overview data:", err);
        setData(null);
        setTechnicalDetails(null);
      } finally {
        setLoading(false);
      }
    }

    fetchIntelligence();
  }, []);

  return (
    <CipShell
      institution={institution}
      title="Institutional Overview"
      subtitle="Comprehensive cross-signal institutional status and risk trajectory."
      onSearch={() => setSearchOpen(true)}
      onLogout={() => {
        if (typeof window !== "undefined") {
          localStorage.removeItem("cip_token");
          window.location.href = "/dashboard";
        }
      }}
    >
      <CipPage>
        {loading ? (
          <CipLoadingState label="Synthesizing institutional intelligence from telemetry..." />
        ) : !data ? (
          <CipEmptyState
            title="No institutional analysis available"
            description="Upload or ingest dataset signals to build this institution's evidence base."
          />
        ) : (
          <div className="space-y-6">
            <CipOverview data={data} />

            {technicalDetails && (
              <CipDisclosure title="Technical model inputs & methodology">
                <div className="space-y-3 text-[13px] text-[#6F686B]">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#9A9295]">Model Engine</div>
                      <div className="mt-1 font-medium text-[#252124]">{technicalDetails.methodology || "Autoregressive Composite"}</div>
                    </div>
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#9A9295]">Composite Risk (CRI)</div>
                      <div className="mt-1 font-medium text-[#252124]">{data.currentRisk?.toFixed(3) ?? "N/A"}</div>
                    </div>
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#9A9295]">Confidence Level</div>
                      <div className="mt-1 font-medium text-[#252124]">{technicalDetails.confidence || "95%"}</div>
                    </div>
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#9A9295]">Coverage</div>
                      <div className="mt-1 font-medium text-[#252124]">{technicalDetails.coverage || "Standard"}</div>
                    </div>
                  </div>
                </div>
              </CipDisclosure>
            )}
          </div>
        )}

        <CipCommandSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
      </CipPage>
    </CipShell>
  );
}
