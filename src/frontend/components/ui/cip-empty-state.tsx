import React from "react";

export function CipEmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-[26px] border border-dashed border-[#D6CECA] bg-white p-10 text-center">
      <div className="mx-auto grid size-12 place-items-center rounded-[16px] bg-[#F3F0ED] text-[#7A2438]">
        —
      </div>

      <h3 className="mt-4 text-[17px] font-semibold text-[#252124]">
        {title}
      </h3>

      <p className="mx-auto mt-2 max-w-md text-[13px] leading-5 text-[#9A9295]">
        {description}
      </p>

      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
