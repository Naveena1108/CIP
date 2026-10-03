"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

type CipDisclosureProps = {
  title?: string;
  children: React.ReactNode;
};

export function CipDisclosure({
  title = "Technical details",
  children,
}: CipDisclosureProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-[18px] border border-[#EEE9E6] bg-[#F8F7F4]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="text-[12px] font-semibold text-[#6F686B]">
          {title}
        </span>

        <ChevronDown
          size={16}
          className={`text-[#9A9295] transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="border-t border-[#EEE9E6] px-4 py-4">
          {children}
        </div>
      )}
    </div>
  );
}
