"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ChevronDown, LogOut, Settings, UserRound } from "lucide-react";
import { usePathname } from "next/navigation";
import { CIP_NAVIGATION } from "@/config/cip-navigation";
import { cn } from "@/lib/utils";

type Institution = {
  id: string;
  name: string;
  type?: string;
};

type CipSidebarProps = {
  institution: Institution;
  userName?: string;
  collapsed?: boolean;
  onLogout?: () => void;
};

export function CipSidebar({
  institution,
  userName = "User",
  collapsed = false,
  onLogout,
}: CipSidebarProps) {
  const pathname = usePathname();

  const primary = CIP_NAVIGATION.filter(
    (item) => item.group === "primary"
  );

  const context = CIP_NAVIGATION.filter(
    (item) => item.group === "context"
  );

  return (
    <aside
      className={cn(
        "fixed left-4 top-4 bottom-4 z-40",
        "hidden lg:flex flex-col",
        "rounded-[28px] border border-white/70",
        "bg-white/78 backdrop-blur-2xl",
        "shadow-[0_20px_60px_rgba(37,33,36,0.10)]",
        "transition-[width] duration-300 ease-out",
        collapsed ? "w-[84px]" : "w-[248px]"
      )}
    >
      {/* Brand */}
      <div className="flex h-[76px] items-center px-5">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-[14px] bg-[#7A2438] text-sm font-semibold text-white shadow-sm">
            CI
          </div>

          {!collapsed && (
            <div>
              <div className="text-[15px] font-semibold tracking-[-0.01em] text-[#252124]">
                CIP
              </div>
              <div className="text-[11px] text-[#9A9295]">
                Crisis Intelligence
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Institution */}
      <div className="px-3">
        <button
          type="button"
          className={cn(
            "flex w-full items-center gap-3 rounded-[16px]",
            "border border-[#EEE9E6] bg-[#F8F7F4]",
            "px-3 py-3 text-left",
            "transition-colors hover:bg-[#F3F0ED]"
          )}
        >
          <div className="grid size-9 shrink-0 place-items-center rounded-[11px] bg-[#E9DDE0] text-xs font-semibold text-[#7A2438]">
            {institution.name.slice(0, 2).toUpperCase()}
          </div>

          {!collapsed && (
            <>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-[#252124]">
                  {institution.name}
                </div>
                <div className="truncate text-[11px] text-[#9A9295]">
                  {institution.type ?? "Institution"}
                </div>
              </div>

              <ChevronDown size={15} className="text-[#9A9295]" />
            </>
          )}
        </button>
      </div>

      {/* Navigation */}
      <nav className="cip-scrollbar mt-5 flex-1 overflow-y-auto px-3">
        <NavSection
          title="Workspace"
          items={primary}
          pathname={pathname}
          collapsed={collapsed}
        />

        <div className="my-5 h-px bg-[#EEE9E6]" />

        <NavSection
          title="Context"
          items={context}
          pathname={pathname}
          collapsed={collapsed}
        />
      </nav>

      {/* Profile */}
      <div className="border-t border-[#EEE9E6] p-3">
        <Link
          href="/profile"
          className={cn(
            "flex items-center gap-3 rounded-[16px] p-2.5",
            "transition-colors hover:bg-[#F3F0ED]"
          )}
        >
          <div className="grid size-9 shrink-0 place-items-center rounded-full bg-[#E9DDE0] text-[#7A2438]">
            <UserRound size={17} />
          </div>

          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium text-[#252124]">
                {userName}
              </div>
              <div className="text-[11px] text-[#9A9295]">
                Profile
              </div>
            </div>
          )}
        </Link>

        {!collapsed && onLogout && (
          <button
            type="button"
            onClick={onLogout}
            className="mt-1 flex w-full items-center gap-3 rounded-[14px] px-3 py-2.5 text-left text-[12px] text-[#6F686B] transition-colors hover:bg-[#F3F0ED] hover:text-[#7A2438]"
          >
            <LogOut size={15} />
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}

function NavSection({
  title,
  items,
  pathname,
  collapsed,
}: {
  title: string;
  items: typeof CIP_NAVIGATION;
  pathname: string;
  collapsed: boolean;
}) {
  return (
    <div>
      {!collapsed && (
        <div className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#B8B2B4]">
          {title}
        </div>
      )}

      <div className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;

          const active =
            pathname === item.href ||
            (item.href !== "/dashboard" &&
              pathname.startsWith(item.href));

          return (
            <Link
              key={item.id}
              href={item.href}
              className="relative block"
            >
              {active && (
                <motion.div
                  layoutId="cip-active-navigation"
                  transition={{
                    type: "spring",
                    stiffness: 380,
                    damping: 30,
                  }}
                  className="absolute inset-0 rounded-[15px] bg-[#E9DDE0]"
                />
              )}

              <div
                className={cn(
                  "relative flex items-center gap-3 rounded-[15px] px-3 py-2.5",
                  "text-[13px] transition-colors",
                  active
                    ? "font-medium text-[#7A2438]"
                    : "text-[#6F686B] hover:bg-[#F3F0ED]"
                )}
              >
                <Icon
                  size={17}
                  strokeWidth={active ? 2.1 : 1.8}
                />

                {!collapsed && <span>{item.label}</span>}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
