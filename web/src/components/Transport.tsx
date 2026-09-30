/**
 * Device transport, and the step rail.
 *
 * The exchange strip is the one thing on this page that is not a verdict. See
 * docs/adr/0001-demo-transport-rail.md.
 */

import type { SyncReport } from "../lib/types";
import { Figure, bytes } from "./Primitives";

export function ExchangeStrip({ report }: { report: SyncReport }) {
  return (
    <div className="border-rule bg-paper-raised rounded-sm border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="marginal !text-ink-muted">exchange</span>
        <span className="marginal">
          transport, not part of the question interface
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-4">
        {[
          ["device", report.device_id],
          ["path", report.path],
          ["pushed", report.pushed],
          ["accepted", report.accepted],
          ["duplicates", report.duplicates],
          ["pulled", report.pulled],
          ["cursor", `${report.cursor_before} → ${report.cursor_after}`],
          ["bytes sent", bytes(report.bytes_sent)],
          ["bytes received", bytes(report.bytes_received)],
          ["offline for", `${report.offline_seconds.toFixed(1)} s`],
        ].map(([k, v]) => (
          <div key={k}>
            <dt className="marginal">{k}</dt>
            <dd className="figure text-ink mt-0.5 text-[0.8125rem]">{v}</dd>
          </div>
        ))}
      </dl>
      {report.withheld > 0 ? (
        <p className="marginal mt-3">
          <Figure className="!text-ink">{report.withheld}</Figure> claim(s) held on
          the device and not yet at the depot.
        </p>
      ) : null}
      {report.notes.length > 0 ? (
        <ul className="marginal mt-2 space-y-1">
          {report.notes.map((note) => (
            <li key={note}>— {note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function LinkState({ up }: { up: boolean }) {
  return (
    <span className="marginal inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={`inline-block size-1.5 rounded-full ${
          up ? "bg-verdict-answered" : "bg-signal"
        }`}
      />
      {up ? "uplink up" : "uplink severed"}
    </span>
  );
}