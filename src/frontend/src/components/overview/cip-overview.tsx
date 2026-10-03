"use client";

import React from "react";
import { motion } from "framer-motion";
import {
  ArrowRight,
  ChevronDown,
  CircleAlert,
  FileSearch,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

export type Finding = {
  id: string;
  title: string;
  summary: string;
  severity: "observation" | "emerging" | "high" | "critical";
  evidenceCount: number;
};

export type OverviewData = {
  institutionName: string;
  status: "stable" | "watch" | "elevated" | "critical";
  statusSummary: string;
  currentRisk: number | null;
  riskLabel: string;
  changedCount: number;
  findingCount: number;
  evidenceCount: number;
  findings: Finding[];
};

const STATUS_LABELS = {
  stable: "Stable",
  watch: "Watch",
  elevated: "Elevated",
  critical: "Critical",
};

const STATUS_STYLES = {
  stable: "bg-[#EAF3EE] text-[#4F8068]",
  watch: "bg-[#F8EFE3] text-[#B07A3F]",
  elevated: "bg-[#F7E9EB] text-[#A94A55]",
  critical: "bg-[#F3E2E6] text-[#7A2438]",
};

export function CipOverview({ data }: { data: OverviewData }) {
  return (
    <div className="space-y-6">
      {/* Hero status */}
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="rounded-[28px] border border-[#E5DFDC] bg-white p-6 shadow-[0_10px_30px_rgba(37,33,36,0.05)] sm:p-8"
      >
        <div className="flex flex-col justify-between gap-8 lg:flex-row lg:items-end">
          <div className="max-w-2xl">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.15em] text-[#9A9295]">
              Institutional status
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-[32px] font-semibold tracking-[-0.04em] text-[#252124] sm:text-[42px]">
                {STATUS_LABELS[data.status] || "Stable"}
              </h2>

              <span
                className={`rounded-full px-3 py-1.5 text-[11px] font-semibold ${STATUS_STYLES[data.status] || STATUS_STYLES.stable}`}
              >
                Current state
              </span>
            </div>

            <p className="mt-3 max-w-xl text-[14px] leading-6 text-[#6F686B]">
              {data.statusSummary}
            </p>
          </div>

          <div className="grid grid-cols-3 gap-2 sm:gap-3">
            <MiniMetric
              label="Changed"
              value={data.changedCount}
              icon={<TrendingDown size={15} />}
            />
            <MiniMetric
              label="Findings"
              value={data.findingCount}
              icon={<CircleAlert size={15} />}
            />
            <MiniMetric
              label="Evidence"
              value={data.evidenceCount}
              icon={<FileSearch size={15} />}
            />
          </div>
        </div>
      </motion.section>

      {/* What changed */}
      <section>
        <SectionHeader
          title="What changed"
          description="Meaningful developments detected across the institution."
        />

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {data.findings.slice(0, 4).map((finding, index) => (
            <motion.article
              key={finding.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                duration: 0.3,
                delay: index * 0.04,
              }}
              className="group rounded-[22px] border border-[#E5DFDC] bg-white p-5 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_14px_36px_rgba(37,33,36,0.07)]"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-2">
                  <SeverityDot severity={finding.severity} />
                  <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-[#9A9295]">
                    {finding.severity}
                  </span>
                </div>

                <span className="text-[11px] text-[#B8B2B4]">
                  {finding.evidenceCount} evidence
                </span>
              </div>

              <h3 className="mt-4 text-[16px] font-semibold tracking-[-0.015em] text-[#252124]">
                {finding.title}
              </h3>

              <p className="mt-2 text-[13px] leading-5 text-[#6F686B]">
                {finding.summary}
              </p>

              <button
                type="button"
                className="mt-5 inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#7A2438]"
              >
                View evidence
                <ArrowRight
                  size={14}
                  className="transition-transform group-hover:translate-x-0.5"
                />
              </button>
            </motion.article>
          ))}
        </div>
      </section>

      {/* Risk outlook */}
      <section className="rounded-[24px] border border-[#E5DFDC] bg-white p-5 sm:p-6">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
          <div>
            <SectionHeader
              title="Risk outlook"
              description="Projected institutional risk based on available evidence."
            />
          </div>

          <button
            type="button"
            className="inline-flex items-center gap-1 text-[12px] font-semibold text-[#7A2438]"
          >
            View forecast
            <ArrowRight size={14} />
          </button>
        </div>

        <RiskTimeline currentRisk={data.currentRisk} />
      </section>
    </div>
  );
}

function MiniMetric({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <div className="min-w-[82px] rounded-[17px] bg-[#F8F7F4] px-3 py-3">
      <div className="flex items-center gap-1.5 text-[#9A9295]">
        {icon}
        <span className="text-[10px] font-medium uppercase tracking-[0.08em]">
          {label}
        </span>
      </div>
      <div className="mt-1 text-[20px] font-semibold tracking-[-0.03em] text-[#252124]">
        {value}
      </div>
    </div>
  );
}

function SectionHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold tracking-[-0.025em] text-[#252124]">
        {title}
      </h2>
      <p className="mt-1 text-[12px] leading-5 text-[#9A9295]">
        {description}
      </p>
    </div>
  );
}

function SeverityDot({
  severity,
}: {
  severity: Finding["severity"];
}) {
  const style = {
    observation: "bg-[#737174]",
    emerging: "bg-[#B07A3F]",
    high: "bg-[#A94A55]",
    critical: "bg-[#7A2438]",
  }[severity] || "bg-[#737174]";

  return <span className={`size-2 rounded-full ${style}`} />;
}

function RiskTimeline({
  currentRisk,
}: {
  currentRisk: number | null;
}) {
  if (currentRisk === null) {
    return (
      <div className="mt-6 rounded-[18px] bg-[#F8F7F4] p-5 text-[13px] text-[#6F686B]">
        There is not enough institutional evidence to produce a reliable
        risk outlook yet.
      </div>
    );
  }

  const points = [
    { label: "Now", value: currentRisk },
    { label: "1Y", value: currentRisk },
    { label: "2Y", value: currentRisk },
    { label: "3Y", value: currentRisk },
  ];

  return (
    <div className="mt-7">
      <div className="grid grid-cols-4 gap-3">
        {points.map((point, index) => (
          <div key={point.label}>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-medium text-[#9A9295]">
                {point.label}
              </span>
              <span className="text-[12px] font-semibold text-[#252124]">
                {point.value.toFixed(2)}
              </span>
            </div>

            <div className="relative h-2 overflow-hidden rounded-full bg-[#EEE9E6]">
              <motion.div
                initial={{ width: 0 }}
                animate={{
                  width: `${Math.min(Math.max(point.value * 100, 0), 100)}%`,
                }}
                transition={{
                  duration: 0.55,
                  delay: index * 0.08,
                }}
                className="h-full rounded-full bg-[#7A2438]"
              />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-5 flex items-center gap-2 text-[12px] text-[#6F686B]">
        <ChevronDown size={14} className="text-[#9A9295]" />
        <span>
          Forecast interpretation is driven by the real backend prediction engine.
        </span>
      </div>
    </div>
  );
}
