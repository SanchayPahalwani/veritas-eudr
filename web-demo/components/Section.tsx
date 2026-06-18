import type { ReactNode } from "react";
import { Reveal } from "./Reveal";

/** Consistent section shell: full-width band, generous vertical rhythm, max-width
 * content column, optional hairline top border. */
export function Section({
  id,
  children,
  className = "",
  bordered = true,
}: {
  id: string;
  children: ReactNode;
  className?: string;
  bordered?: boolean;
}) {
  return (
    <section
      id={id}
      className={`relative z-10 scroll-mt-0 px-5 py-24 sm:px-8 md:py-32 ${
        bordered ? "border-t border-line" : ""
      } ${className}`}
    >
      <div className="mx-auto w-full max-w-6xl">{children}</div>
    </section>
  );
}

/** Section header: index + bracketed label, then a massive display heading and an
 * optional lede. */
export function SectionHeading({
  index,
  label,
  title,
  lede,
}: {
  index: string;
  label: string;
  title: ReactNode;
  lede?: ReactNode;
}) {
  return (
    <header className="mb-12 md:mb-16">
      <Reveal className="flex items-center gap-3 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-fg-dim">
        <span className="text-hazard">{index}</span>
        <span className="h-px w-8 bg-line-bright" />
        <span>{label}</span>
      </Reveal>
      <Reveal delay={0.05}>
        <h2 className="display mt-5 max-w-4xl text-balance text-[clamp(2.25rem,6vw,5rem)] text-fg">
          {title}
        </h2>
      </Reveal>
      {lede && (
        <Reveal delay={0.1}>
          <p className="mt-6 max-w-2xl text-pretty text-base leading-relaxed text-fg-dim md:text-lg">
            {lede}
          </p>
        </Reveal>
      )}
    </header>
  );
}
