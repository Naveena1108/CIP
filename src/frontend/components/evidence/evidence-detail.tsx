import React from "react";

export type EvidenceDetailData = {
  title: string;
  value?: string;
  period?: string;
  source: string;
  page?: string;
  excerpt?: string;
  supportingSignal?: string;
};

export function EvidenceDetail({
  evidence,
}: {
  evidence: EvidenceDetailData;
}) {
  return (
    <div className="rounded-[22px] border border-[#E5DFDC] bg-white p-5 shadow-[0_10px_30px_rgba(37,33,36,0.05)]">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#9A9295]">
            Evidence
          </div>

          <h3 className="mt-2 text-[17px] font-semibold text-[#252124]">
            {evidence.title}
          </h3>
        </div>

        {evidence.period && (
          <span className="rounded-full bg-[#F3F0ED] px-2.5 py-1 text-[10px] font-medium text-[#6F686B]">
            {evidence.period}
          </span>
        )}
      </div>

      {evidence.value && (
        <div className="mt-5 text-[28px] font-semibold tracking-[-0.04em] text-[#7A2438]">
          {evidence.value}
        </div>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <EvidenceMeta label="Source" value={evidence.source} />
        {evidence.page && (
          <EvidenceMeta label="Page" value={evidence.page} />
        )}
      </div>

      {evidence.excerpt && (
        <div className="mt-5 rounded-[16px] bg-[#F8F7F4] p-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#9A9295]">
            Supporting excerpt
          </div>

          <p className="mt-2 text-[13px] leading-5 text-[#6F686B]">
            {evidence.excerpt}
          </p>
        </div>
      )}
    </div>
  );
}

function EvidenceMeta({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#B8B2B4]">
        {label}
      </div>

      <div className="mt-1 text-[12px] text-[#6F686B]">
        {value}
      </div>
    </div>
  );
}
