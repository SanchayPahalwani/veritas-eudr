import { manifest, plotsIndex } from "@/lib/data";

const DATASETS = [
  "JRC GFC2020 V3",
  "Hansen GFC-2025-v1.13",
  "ESA WorldCover v200",
  "© OpenStreetMap contributors",
];

export function SiteFooter() {
  return (
    <footer className="relative z-10 border-t border-line px-5 py-12 sm:px-8">
      <div className="mx-auto w-full max-w-6xl">
        <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
          <div className="max-w-md">
            <div className="flex items-center gap-3 font-mono text-xs uppercase tracking-[0.16em] text-fg">
              VERITAS <span className="text-hazard">·</span> EUDR
            </div>
            <p className="mt-3 text-sm leading-relaxed text-fg-dim">
              A working demo of an EU Deforestation Regulation due-diligence engine. Every figure on
              this page is real, reproducible engine output — frozen offline by{" "}
              <span className="font-mono text-fg-faint">scripts/export_demo_data.py</span>, no
              backend at runtime.
            </p>
            <a
              href="https://github.com/SanchayPahalwani/veritas-eudr"
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-block font-mono text-xs uppercase tracking-[0.12em] text-fg-dim underline decoration-line underline-offset-4 transition-colors hover:text-fg"
            >
              Source on GitHub ↗
            </a>
          </div>

          <div className="grid grid-cols-2 gap-x-10 gap-y-4 font-mono text-[0.6875rem] uppercase tracking-[0.1em]">
            <div>
              <div className="text-fg-faint">run</div>
              <div className="mt-1 text-fg-dim normal-case">{plotsIndex.run_id}</div>
              <div className="mt-3 text-fg-faint">policy</div>
              <div className="mt-1 text-fg-dim normal-case">{manifest.policy_version}</div>
            </div>
            <div>
              <div className="text-fg-faint">datasets</div>
              <ul className="mt-1 space-y-1 text-fg-dim normal-case">
                {DATASETS.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        <div className="mt-10 flex flex-col gap-2 border-t border-line pt-5 font-mono text-[0.625rem] uppercase tracking-[0.12em] text-fg-faint sm:flex-row sm:items-center sm:justify-between">
          <span>Synthetic AOI · Vietnam Central Highlands · {plotsIndex.n_plots} plots</span>
          <span>Reg (EU) 2023/1115 · amended 2025/2650 · cutoff 31 Dec 2020</span>
        </div>
      </div>
    </footer>
  );
}
