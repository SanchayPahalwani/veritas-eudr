"use client";

import { useEffect, useState } from "react";
import { CHAPTERS } from "@/lib/chapters";

export function ScrollProgressRail() {
  const [active, setActive] = useState(CHAPTERS[0].id);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-45% 0px -45% 0px", threshold: 0 },
    );
    for (const c of CHAPTERS) {
      const el = document.getElementById(c.id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);

  return (
    <nav
      aria-label="Sections"
      className="fixed left-2 top-1/2 z-40 hidden -translate-y-1/2 xl:block"
    >
      <ul className="flex flex-col gap-2">
        {CHAPTERS.map((c) => {
          const isActive = c.id === active;
          return (
            <li key={c.id} className="group relative flex items-center">
              <a
                href={`#${c.id}`}
                className="flex items-center py-1"
                aria-current={isActive ? "true" : undefined}
                aria-label={`${c.index} ${c.label}`}
              >
                {/* Resting state: just a tick. Stays clear of full-bleed content. */}
                <span
                  className="block h-px transition-all duration-300"
                  style={{
                    width: isActive ? 20 : 11,
                    background: isActive ? "var(--color-hazard)" : "var(--color-line-bright)",
                  }}
                />
                {/* Label reveals on hover as a tooltip chip, over the content. */}
                <span
                  className="pointer-events-none absolute left-7 -translate-x-1 whitespace-nowrap border border-line bg-bg/95 px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.14em] opacity-0 backdrop-blur transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100"
                >
                  <span className={isActive ? "text-hazard" : "text-fg-faint"}>{c.index}</span>{" "}
                  <span className="text-fg-dim">{c.label}</span>
                </span>
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
