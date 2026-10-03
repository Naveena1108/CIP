"use client";

import type { ReactNode } from "react";
import { CipMobileNav } from "./cip-mobile-nav";
import { CipSidebar } from "./cip-sidebar";
import { CipTopbar } from "./cip-topbar";

type CipShellProps = {
  children: ReactNode;
  institution: {
    id: string;
    name: string;
    type?: string;
  };
  userName?: string;
  title: string;
  subtitle?: string;
  onSearch?: () => void;
  onLogout?: () => void;
};

export function CipShell({
  children,
  institution,
  userName,
  title,
  subtitle,
  onSearch,
  onLogout,
}: CipShellProps) {
  return (
    <div className="min-h-screen bg-[#F8F7F4] text-[#252124]">
      <CipSidebar
        institution={institution}
        userName={userName}
        onLogout={onLogout}
      />

      <main className="min-h-screen lg:pl-[280px]">
        <div className="mx-auto w-full max-w-[1440px] px-4 py-4 sm:px-6 lg:px-8">
          <CipTopbar
            title={title}
            subtitle={subtitle}
            onSearch={onSearch}
          />

          <div className="pb-28 lg:pb-10">
            {children}
          </div>
        </div>
      </main>

      <CipMobileNav />
    </div>
  );
}
