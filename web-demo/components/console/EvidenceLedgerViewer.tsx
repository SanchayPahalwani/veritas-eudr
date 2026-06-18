"use client";

import { useMemo } from "react";
import { evidenceLedger } from "@/lib/data";
import { Tele } from "@/components/ui";
import { frac } from "@/lib/format";
import { useConsole } from "./ConsoleContext";

export function EvidenceLedgerViewer() {
  const { selectedId, select } = useConsole();
  const rows = evidenceLedger.evidence;

  const filtered = useMemo(
    () => (selectedId ? rows.filter((r) => r.plot_id === selectedId) : rows),
    [rows, selectedId],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2">
        <Tele>Evidence ledger · append-only</Tele>
        <span className="font-mono text-[0.625rem] text-fg-faint">
          {selectedId ? (
            <>
              {filtered.length} / {rows.length} rows · plot {selectedId} ·{" "}
              <button
                className="text-band21 underline-offset-2 hover:underline"
                onClick={() => select(null)}
              >
                clear
              </button>
            </>
          ) : (
            <>{rows.length} rows · run {evidenceLedger.run_id}</>
          )}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full border-collapse font-mono text-[0.6875rem]">
          <thead className="sticky top-0 bg-surface-2 text-fg-faint">
            <tr className="[&>th]:border-b [&>th]:border-line [&>th]:px-3 [&>th]:py-1.5 [&>th]:text-left [&>th]:font-normal [&>th]:uppercase [&>th]:tracking-[0.1em]">
              <th className="w-10">#</th>
              <th>plot</th>
              <th>dataset</th>
              <th>rule</th>
              <th className="w-16 text-right">cover</th>
              <th>verdict</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const isSel = r.plot_id === selectedId;
              return (
                <tr
                  key={r.id}
                  onClick={() => select(r.plot_id)}
                  className={`cursor-pointer border-b border-line/60 transition-colors hover:bg-surface-2 ${
                    isSel ? "bg-band21/5" : ""
                  } [&>td]:px-3 [&>td]:py-1`}
                >
                  <td className="text-fg-faint tabular-nums">{r.id}</td>
                  <td className="text-fg">{r.plot_id}</td>
                  <td className="text-fg-dim">{r.dataset_version}</td>
                  <td className="text-fg-dim">{r.rule_id}</td>
                  <td className="text-right tabular-nums text-fg-dim">
                    {r.covered_fraction != null ? frac(r.covered_fraction, 0) : "—"}
                  </td>
                  <td className="text-fg-dim">{r.verdict}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
