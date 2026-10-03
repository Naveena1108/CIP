"use client";

import { Bell, Command, Search } from "lucide-react";
import { CipGlass } from "@/components/ui/cip-glass";

type CipTopbarProps = {
  title: string;
  subtitle?: string;
  onSearch?: () => void;
};

export function CipTopbar({
  title,
  subtitle,
  onSearch,
}: CipTopbarProps) {
  return (
    <header className="sticky top-4 z-30 mb-6">
      <CipGlass
        className="flex min-h-[68px] items-center justify-between gap-4 px-4 py-3"
        strong
      >
        <div className="min-w-0">
          <h1 className="truncate text-[20px] font-semibold tracking-[-0.025em] text-[#252124]">
            {title}
          </h1>

          {subtitle && (
            <p className="mt-0.5 truncate text-[12px] text-[#9A9295]">
              {subtitle}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onSearch}
            aria-label="Search"
            className="hidden sm:flex h-10 items-center gap-2 rounded-[13px] border border-[#EEE9E6] bg-[#F8F7F4] px-3 text-[#9A9295] transition-colors hover:bg-[#F3F0ED]"
          >
            <Search size={16} />
            <span className="text-[12px]">Search</span>
            <span className="ml-3 flex items-center gap-1 rounded-md border border-[#E5DFDC] bg-white px-1.5 py-0.5 text-[10px]">
              <Command size={10} />
              K
            </span>
          </button>

          <button
            type="button"
            aria-label="Notifications"
            className="grid size-10 place-items-center rounded-[13px] border border-[#EEE9E6] bg-white text-[#6F686B] transition-colors hover:bg-[#F3F0ED]"
          >
            <Bell size={17} />
          </button>
        </div>
      </CipGlass>
    </header>
  );
}
