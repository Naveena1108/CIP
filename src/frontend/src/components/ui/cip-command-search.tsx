"use client";

import { Search, X } from "lucide-react";

export function CipCommandSearch({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] bg-[#252124]/10 backdrop-blur-sm">
      <div className="mx-auto mt-[12vh] w-[min(680px,calc(100%-32px))]">
        <div className="overflow-hidden rounded-[26px] border border-white/70 bg-white/90 shadow-[0_30px_90px_rgba(37,33,36,0.18)] backdrop-blur-2xl">
          <div className="flex items-center gap-3 border-b border-[#EEE9E6] px-5 py-4">
            <Search size={18} className="text-[#9A9295]" />

            <input
              autoFocus
              placeholder="Search institution, finding, evidence..."
              className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-[#B8B2B4]"
            />

            <button
              type="button"
              onClick={onClose}
              className="grid size-8 place-items-center rounded-[10px] bg-[#F3F0ED] text-[#6F686B]"
            >
              <X size={15} />
            </button>
          </div>

          <div className="p-4">
            <div className="rounded-[16px] bg-[#F8F7F4] px-4 py-5 text-center text-[12px] text-[#9A9295]">
              Search results are loaded from the existing CIP backend.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
