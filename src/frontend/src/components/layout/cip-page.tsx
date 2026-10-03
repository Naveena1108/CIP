"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { cipPageTransition } from "@/lib/cip-motion";

export function CipPage({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <motion.div {...cipPageTransition}>
      {children}
    </motion.div>
  );
}
