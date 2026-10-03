import {
  BarChart3,
  Database,
  FileSearch,
  Gauge,
  History,
  Lightbulb,
  Radar,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

export type CipNavItem = {
  id: string;
  label: string;
  href: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number }>;
  group: "primary" | "context";
};

export const CIP_NAVIGATION: CipNavItem[] = [
  {
    id: "overview",
    label: "Overview",
    href: "/dashboard",
    icon: Gauge,
    group: "primary",
  },
  {
    id: "investigations",
    label: "Investigate",
    href: "/dashboard/investigations",
    icon: Search,
    group: "primary",
  },
  {
    id: "insights",
    label: "Insights",
    href: "/dashboard/insights",
    icon: Lightbulb,
    group: "primary",
  },
  {
    id: "risks",
    label: "Risk",
    href: "/dashboard/risks",
    icon: ShieldAlert,
    group: "primary",
  },
  {
    id: "predictions",
    label: "Forecast",
    href: "/dashboard/predictions",
    icon: BarChart3,
    group: "primary",
  },
  {
    id: "what-if",
    label: "What-If",
    href: "/dashboard/what-if",
    icon: Sparkles,
    group: "primary",
  },
  {
    id: "evidence",
    label: "Evidence",
    href: "/dashboard/evidence",
    icon: FileSearch,
    group: "primary",
  },
  {
    id: "data",
    label: "Data",
    href: "/dashboard/data",
    icon: Database,
    group: "primary",
  },
  {
    id: "external-intelligence",
    label: "External Context",
    href: "/dashboard/external-intelligence",
    icon: Radar,
    group: "context",
  },
  {
    id: "historical-context",
    label: "Historical Context",
    href: "/dashboard/historical-context",
    icon: History,
    group: "context",
  },
];
