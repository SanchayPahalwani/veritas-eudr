"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";

const EASE = [0.22, 1, 0.36, 1] as const;

/** Reveal-on-scroll wrapper. Short opacity + y translate.
 *
 * - Default: `whileInView` — animates as the element scrolls into view.
 * - `eager`: animates on mount instead. Use for ABOVE-THE-FOLD content, where
 *   `whileInView` can fail to fire (an element already in view on load may never
 *   receive an "enter" event), leaving it stuck invisible.
 *
 * Disabled automatically under prefers-reduced-motion (Motion respects it). */
export function Reveal({
  children,
  className = "",
  delay = 0,
  y = 18,
  as = "div",
  eager = false,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  y?: number;
  as?: "div" | "section" | "li" | "span";
  eager?: boolean;
}) {
  const Comp = motion[as];
  const transition = { duration: 0.6, delay, ease: EASE };

  if (eager) {
    return (
      <Comp className={className} initial={{ opacity: 0, y }} animate={{ opacity: 1, y: 0 }} transition={transition}>
        {children}
      </Comp>
    );
  }

  return (
    <Comp
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-12%" }}
      transition={transition}
    >
      {children}
    </Comp>
  );
}
