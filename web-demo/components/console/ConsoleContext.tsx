"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { loadPlotRisk } from "@/lib/data";
import type { PlotRisk } from "@/lib/types";

interface ConsoleState {
  selectedId: string | null;
  hoveredId: string | null;
  detail: PlotRisk | null;
  loading: boolean;
  select: (id: string | null) => void;
  hover: (id: string | null) => void;
}

const Ctx = createContext<ConsoleState | null>(null);

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PlotRisk | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch the selected plot's payload on change. The synchronous state resets are
  // intentional here (a classic fetch-on-prop-change effect syncing an external
  // async source), so the experimental cascading-render rule is suppressed.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    loadPlotRisk(selectedId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const value: ConsoleState = {
    selectedId,
    hoveredId,
    detail,
    loading,
    select: setSelectedId,
    hover: setHoveredId,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConsole(): ConsoleState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useConsole must be used within ConsoleProvider");
  return ctx;
}
