/**
 * Evidence.
 *
 * Two kinds of claim live here and they are kept apart on purpose. The first is
 * a measured comparison the engine produced during the run that backs it. The
 * second is a written finding from the substrate probes, linked to the file that
 * records it — provenance rather than assertion.
 */

import { useEffect, useState } from "react";
import { Page } from "../components/Shell";
import { Figure, bytes } from "../components/Primitives";

interface Comparison {
  written: number;
  keys: number;
  survivors: number;
  overwritten: number;
  describe: string;
  lines: string[];
  engine_survivors: number;
}

interface Exchange {
  device_id: string;
  path: string;
  pushed: number;
  pulled: number;
  bytes_sent: number;
  bytes_received: number;
  duplicates: number;
  cursor_before: number;
  cursor_after: number;
  complete: boolean;
  notes: string[];
}

interface Gate {
  question: string;
  finding: string;
  verdict: string;
}

const GATES: Gate[] = [
  {
    question: "Does the published wheel install and import?",
    finding:
      "Yes. qdrant-edge-py 0.8.0 installs as a win_amd64 abi3 wheel and imports under 3.12. There is no source distribution, so the wheel is the build input. The default python on this machine is 3.7, which fails — the venv pins 3.12.",
    verdict: "pass",
  },
  {
    question: "Does this release fuse dense and sparse at query time?",
    finding:
      "Yes, and the engine uses it. Fusion is the library's, not hand-rolled. Verified genuine rather than one leg passing through: hybrid scores match neither leg's own scores. The earlier note claiming fusion must be done in application code was not reproduced here and is recorded as observed behaviour of the installed library, not as a judgement about a document this project has not read.",
    verdict: "pass",
  },
  {
    question: "Does a blocking shard operation stall a concurrent query?",
    finding:
      "Partly. No stall at 50k points. A real stall at 120k: one query blocked for the entire 24.7s index build. The first probe ran optimize() over 400 points and completed in 0.11ms — too short a window to support any conclusion, so it was discarded and re-measured at sizes where the call takes seconds. optimize() is blocking and scales with corpus size, so index builds run in a worker process.",
    verdict: "partial",
  },
];

export default function Evidence() {
  const [data, setData] = useState<{
    comparison: Comparison;
    exchanges: Exchange[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/comparison")
      .then((r) => r.json())
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  const c = data?.comparison;

  return (
    <Page>
      <header className="pt-16 md:pt-20">
        <h1 className="text-[clamp(1.875rem,4vw,2.75rem)] leading-tight font-semibold">
          What was measured, including what failed
        </h1>
        <p className="measure text-ink-muted mt-5 leading-relaxed">
          Four questions about the substrate were asked before anything was built,
          because their answers change the architecture rather than the code. Each
          has a pass or fail that decided something. Raw output is in{" "}
          <code className="figure">docs/gates.md</code>, and every probe can be
          re-run.
        </p>
      </header>

      {error ? (
        <p
          role="alert"
          className="border-signal-edge text-signal bg-signal-wash mt-8 rounded-sm border p-3 text-[0.875rem]"
        >
          {error}
        </p>
      ) : null}

      <section className="mt-16 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          The destructive comparison
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          The same two incompatible claims, run through a modelled last-write-wins
          strategy and then through this engine. The comparison is implemented as
          a labelled mode solely to show the contrast, and is never a production
          path.
        </p>

        {c ? (
          <>
            <div className="mt-8 grid gap-8 md:grid-cols-2">
              <div className="border-rule rounded-sm border p-5">
                <p className="marginal uppercase">last-write-wins</p>
                <p className="mt-4 text-[1.0625rem] leading-relaxed">
                  <Figure className="text-ink !text-[1.125rem]">{c.written}</Figure>{" "}
                  writes collapse to{" "}
                  <Figure className="text-ink !text-[1.125rem]">{c.keys}</Figure>{" "}
                  storage key.{" "}
                  <span className="text-signal font-medium">
                    <Figure className="!text-signal !text-[1.125rem]">
                      {c.overwritten}
                    </Figure>{" "}
                    overwritten
                  </span>
                  ,{" "}
                  <Figure className="text-ink !text-[1.125rem]">{c.survivors}</Figure>{" "}
                  left.
                </p>
                <ul className="marginal mt-4 space-y-1">
                  {c.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>

              <div className="border-rule rounded-sm border p-5">
                <p className="marginal uppercase">this engine</p>
                <p className="mt-4 text-[1.0625rem] leading-relaxed">
                  Both claims are held, attributed and returned.{" "}
                  <Figure className="text-ink !text-[1.125rem]">
                    {c.engine_survivors}
                  </Figure>{" "}
                  claims survive the same disagreement, the ladder is presented and
                  not applied, and the engine states that it is not choosing.
                </p>
                <p className="marginal mt-4 leading-relaxed">
                  Read from the CONFLICTED verdict the demonstration just
                  produced, so the two counts are of the same two claims.
                </p>
              </div>
            </div>
          </>
        ) : (
          <div className="border-rule bg-paper-raised mt-8 h-40 animate-pulse rounded-sm border" />
        )}
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          The exchange that carried it
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          Measured during the same run. Two rounds, because one round cannot carry
          both directions: whichever device syncs first has nothing yet to pull.
          The duplicate counts on the second round are the evidence that the first
          was received rather than re-sent.
        </p>
        {data ? (
          <div className="mt-6 overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-rule border-b">
                  {["device", "path", "pushed", "pulled", "dup", "cursor", "sent", "received", "complete"].map(
                    (h) => (
                      <th key={h} className="marginal py-2 text-left font-medium">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {data.exchanges.map((x, i) => (
                  <tr key={i} className="border-rule/60 border-b last:border-b-0">
                    <td className="figure py-2 text-[0.8125rem]">{x.device_id}</td>
                    <td className="figure py-2 text-[0.8125rem]">{x.path}</td>
                    <td className="figure py-2 text-[0.8125rem]">{x.pushed}</td>
                    <td className="figure py-2 text-[0.8125rem]">{x.pulled}</td>
                    <td className="figure py-2 text-[0.8125rem]">{x.duplicates}</td>
                    <td className="figure py-2 text-[0.8125rem]">
                      {x.cursor_before} → {x.cursor_after}
                    </td>
                    <td className="figure py-2 text-[0.8125rem]">
                      {bytes(x.bytes_sent)}
                    </td>
                    <td className="figure py-2 text-[0.8125rem]">
                      {bytes(x.bytes_received)}
                    </td>
                    <td className="figure py-2 text-[0.8125rem]">
                      {String(x.complete)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="border-rule bg-paper-raised mt-6 h-28 animate-pulse rounded-sm border" />
        )}
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          Substrate gates
        </h2>
        <dl className="mt-8">
          {GATES.map((gate) => (
            <div key={gate.question} className="border-rule border-t py-6 first:border-t-0 first:pt-0">
              <dt className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <span className="text-ink text-[1.0625rem] font-medium">
                  {gate.question}
                </span>
                <span
                  className={`marginal rounded-tag border px-2 py-0.5 font-semibold !text-[0.6875rem] ${
                    gate.verdict === "pass"
                      ? "border-rule text-verdict-answered"
                      : "border-signal-edge text-signal bg-signal-wash"
                  }`}
                >
                  {gate.verdict}
                </span>
              </dt>
              <dd className="measure text-ink-muted mt-2.5 leading-relaxed">
                {gate.finding}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          A defect this design had to fix before it was honest
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          The interface promised that a corrected verdict returns both answers. It
          did not. A device recorded the fact that it could not answer a question,
          but stored an empty string in place of the answer it had given — so the
          correction that followed could report that it had changed without being
          able to show what it had changed from. The demonstration reached this by
          asking the same question twice, and it is the reason the corrected panel
          can now show both.
        </p>
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          Where the numbers come from
        </h2>
        <ul className="measure mt-4 space-y-2.5 text-ink-muted leading-relaxed">
          <li>
            The comparison and the exchange table are read from a run of the
            scenario performed when this page loaded. The counts are of the same
            two claims.
          </li>
          <li>
            The gates are transcribed from <code className="figure">docs/gates.md</code>,
            which records the machine, the interpreter, the exact wheel, and how to
            reproduce each probe.
          </li>
          <li>
            Nothing here is estimated. Latency is read off the clock around the
            question; byte counts are what actually crossed the socket.
          </li>
        </ul>
      </section>
    </Page>
  );
}