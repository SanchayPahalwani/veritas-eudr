"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="relative z-10 flex min-h-dvh flex-col items-center justify-center gap-5 px-6 text-center">
      <span className="font-mono text-[0.6875rem] uppercase tracking-[0.2em] text-hazard">
        ⚠ runtime fault
      </span>
      <h1 className="display max-w-2xl text-[clamp(2rem,6vw,4rem)] text-fg">
        Something failed to render.
      </h1>
      <p className="max-w-md text-sm leading-relaxed text-fg-dim">
        An unexpected error interrupted the page. The engine data is intact — reloading usually
        clears it.
      </p>
      <button
        onClick={reset}
        className="border border-line-bright px-5 py-2 font-mono text-xs uppercase tracking-[0.14em] text-fg transition-colors hover:border-hazard hover:text-hazard"
      >
        Reload
      </button>
    </div>
  );
}
