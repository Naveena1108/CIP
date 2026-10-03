"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Gauge,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

const MOBILE_NAV = [
  {
    label: "Home",
    href: "/dashboard",
    icon: Gauge,
  },
  {
    label: "Investigate",
    href: "/dashboard/investigations",
    icon: Search,
  },
  {
    label: "Risk",
    href: "/dashboard/risks",
    icon: ShieldAlert,
  },
  {
    label: "Forecast",
    href: "/dashboard/predictions",
    icon: BarChart3,
  },
  {
    label: "More",
    href: "/dashboard/what-if",
    icon: Sparkles,
  },
];

export function CipMobileNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-3 left-3 right-3 z-50 lg:hidden">
      <div className="mx-auto flex max-w-lg items-center justify-around rounded-[24px] border border-white/70 bg-white/82 p-2 shadow-[0_18px_50px_rgba(37,33,36,0.14)] backdrop-blur-2xl">
        {MOBILE_NAV.map((item) => {
          const Icon = item.icon;

          const active =
            pathname === item.href ||
            (item.href !== "/dashboard" &&
              pathname.startsWith(item.href));

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex min-w-[62px] flex-col items-center gap-1 rounded-[17px] px-2 py-2",
                "transition-all duration-200",
                active
                  ? "bg-[#E9DDE0] text-[#7A2438]"
                  : "text-[#8E888A]"
              )}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
              <span className="text-[10px] font-medium">
                {item.label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
