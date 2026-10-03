"use client";

import { motion, type HTMLMotionProps } from "framer-motion";
import { cn } from "@/lib/utils";

type CipGlassProps = HTMLMotionProps<"div"> & {
  strong?: boolean;
};

export function CipGlass({
  className,
  strong = false,
  ...props
}: CipGlassProps) {
  return (
    <motion.div
      className={cn(
        "rounded-[22px] border",
        strong
          ? "border-white/75 bg-white/80 backdrop-blur-2xl"
          : "border-white/65 bg-white/70 backdrop-blur-xl",
        "shadow-[0_12px_36px_rgba(37,33,36,0.08)]",
        className
      )}
      {...props}
    />
  );
}
