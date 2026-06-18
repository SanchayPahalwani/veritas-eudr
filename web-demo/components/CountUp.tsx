"use client";

import { animate, useInView, useMotionValue } from "motion/react";
import { useEffect, useRef } from "react";

const EASE = [0.22, 1, 0.36, 1] as const;

/** Animated number count-up, triggered when scrolled into view. Drives the
 * formatted value straight into the DOM via a motion value — no React state, so
 * no re-renders. Honors prefers-reduced-motion by snapping to the final value. */
export function CountUp({
  to,
  from = 0,
  duration = 1.5,
  decimals = 0,
  prefix = "",
  suffix = "",
  className = "",
}: {
  to: number;
  from?: number;
  duration?: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-20%" });
  const value = useMotionValue(from);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fmt = (v: number) => `${prefix}${v.toFixed(decimals)}${suffix}`;
    el.textContent = fmt(from);
    if (!inView) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      el.textContent = fmt(to);
      return;
    }
    const controls = animate(value, to, {
      duration,
      ease: EASE,
      onUpdate: (v) => {
        el.textContent = fmt(v);
      },
    });
    return () => controls.stop();
  }, [inView, from, to, duration, decimals, prefix, suffix, value]);

  return <span ref={ref} className={`mono tabular-nums ${className}`} />;
}
