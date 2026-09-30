/** Small shared pieces. Nothing here knows about the engine. */

import type { ReactNode } from "react";

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

/** Short form for a claim id, which is a full digest and never needs to be whole. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function Band({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`band mt-8 ${className}`}>
      {title ? (
        <h3 className="marginal mb-3 !text-ink-muted uppercase">{title}</h3>
      ) : null}
      {children}
    </section>
  );
}

/** A measured figure. Tabular so a re-ask does not shove the layout sideways. */
export function Figure({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <span className={`figure ${className}`}>{children}</span>;
}

export function Chip({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <span className="border-rule bg-paper-raised inline-flex items-baseline gap-1.5 rounded-tag border px-2 py-0.5">
      <span className="marginal">{label}</span>
      <span className="figure text-ink text-[0.75rem]">{value}</span>
    </span>
  );
}

export function Code({ children }: { children: string }) {
  return (
    <pre className="border-rule bg-paper-raised text-ink overflow-x-auto border p-4 text-[0.8125rem] leading-relaxed">
      <code className="font-mono">{children}</code>
    </pre>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-5 gap-y-1.5">
      {rows.map(([k, v]) => (
        <div key={k} className="col-span-2 grid grid-cols-subgrid">
          <dt className="marginal pt-0.5">{k}</dt>
          <dd className="figure text-ink text-[0.8125rem]">{v}</dd>
        </div>
      ))}
    </dl>
  );
}