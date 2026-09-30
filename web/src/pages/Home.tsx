/**
 * Overview.
 *
 * The argument, in the order it needs making: what goes wrong, what this does
 * instead, and then somewhere to go and watch it happen. No figures are quoted
 * here — every number lives on the pages that measured it, because a number on a
 * marketing page is a number nobody can check.
 */

import { Link } from "react-router-dom";
import { Page } from "../components/Shell";

const VERDICTS = [
  [
    "ANSWERED_LOCALLY",
    "The device's own memory settled it. It says which claims it used and why each was usable.",
  ],
  [
    "CONFLICTED",
    "Claims cannot both be true. Both come back, with the authority ladder presented and not applied.",
  ],
  [
    "UNRESOLVED_CLOUD_REQUIRED",
    "The answer exists but the device may not use it. It names what is missing and prices it in bytes.",
  ],
  [
    "CORRECTED",
    "The same question now answers differently. Both answers are returned and the cause is named.",
  ],
] as const;

export default function Home() {
  return (
    <Page>
      <section className="grid items-start gap-10 pt-16 md:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] md:gap-16 md:pt-24">
        <div>
          <p className="marginal uppercase">disagreement-durable edge memory</p>
          <h1 className="mt-4 text-[clamp(2.25rem,5.2vw,3.75rem)] leading-[1.02] font-semibold">
            Two claims that cannot both be true both survive.
          </h1>
          <p className="measure text-ink-muted mt-6 text-[1.0625rem] leading-relaxed">
            An offline-first semantic memory engine for devices that lose
            connectivity. A claim is an attributed assertion, never an overwritten
            fact — and whether one may leave the device is a decision with a
            stated reason.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              to="/demo"
              className="press bg-paper-inverse text-ink-inverse inline-flex min-h-11 items-center rounded-sm px-5 text-[0.9375rem] font-medium"
            >
              Watch the disagreement happen
            </Link>
            <Link
              to="/how"
              className="press-quiet border-rule text-ink inline-flex min-h-11 items-center rounded-sm border px-5 text-[0.9375rem]"
            >
              Read the interface
            </Link>
          </div>
        </div>

        <div className="border-rule bg-paper-raised rounded-sm border p-5">
          <p className="marginal">the entire interface</p>
          <pre className="figure mt-3 overflow-x-auto text-[0.8125rem] leading-relaxed">
            <code>
              {`device.ask(
  "what state is the trailer
   T-114 seal in",
  subject="trailer T-114 seal"
)`}
            </code>
          </pre>
          <p className="text-ink-muted mt-4 text-[0.9375rem] leading-relaxed">
            One method, one labelled verdict, and the same shape whichever of the
            four comes back. There is no second query path, which is why the
            demonstration and the test suite are the same artefact.
          </p>
        </div>
      </section>

      <section className="mt-28 grid gap-10 border-t pt-12 md:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] md:gap-16">
        <h2 className="text-[clamp(1.5rem,3vw,2rem)] leading-tight font-semibold md:sticky md:top-24 md:self-start">
          The record is destroyed, and nothing reports it.
        </h2>
        <div className="measure space-y-5 text-[1.0625rem] leading-relaxed text-ink-muted">
          <p>
            Two devices record the same thing differently while they are apart. The
            central store keeps whichever arrived last. The device is told to
            collect its local copy as already synced. Nothing is written to
            anybody, and the evidence is gone.
          </p>
          <p>
            This is not a bug in a particular product. It is what happens when
            synchronisation has no merge semantics: the newest write wins, the
            older one is not recorded as lost, and the person who was wrong about
            it is never told.
          </p>
          <p>
            The engine treats a disagreement as something to escalate rather than
            something to resolve. Both claims keep their attribution, both stay
            readable, and the authority ladder is shown so a person can decide
            which one settles it — because deciding is not the engine&apos;s to do.
          </p>
        </div>
      </section>

      <section className="mt-28 border-t pt-12">
        <h2 className="measure text-[clamp(1.5rem,3vw,2rem)] leading-tight font-semibold">
          Four verdicts, one shape
        </h2>
        <dl className="mt-8">
          {VERDICTS.map(([kind, say]) => (
            <div
              key={kind}
              className="border-rule grid gap-x-8 gap-y-1 border-t py-5 first:border-t-0 first:pt-0 md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]"
            >
              <dt className="figure text-ink text-[0.875rem] font-semibold">
                {kind}
              </dt>
              <dd className="measure text-ink-muted text-[1rem] leading-relaxed">
                {say}
              </dd>
            </div>
          ))}
        </dl>
        <p className="measure text-ink-muted mt-8 text-[1rem] leading-relaxed">
          Each one carries the claims it rests on, their attribution, the
          residency reasoning where it is relevant, and — where the answer is
          incomplete — the content and byte cost of what would be needed to
          complete it.
        </p>
      </section>

      <section className="border-rule mt-28 grid gap-8 border-t pt-12 md:grid-cols-2">
        <div>
          <h3 className="text-[1.25rem] leading-snug font-semibold">
            A vertical is data, not a fork
          </h3>
          <p className="measure text-ink-muted mt-3 leading-relaxed">
            The engine knows only claim, subject, attribute, conflict, verdict,
            residency and source class. A vertical supplies labels, an authority
            ladder, a subject model and a regulatory citation. Two are bound here:
            freight and substation metering. Binding the second changed no engine
            code.
          </p>
        </div>
        <div>
          <h3 className="text-[1.25rem] leading-snug font-semibold">
            No model in the decision path
          </h3>
          <p className="measure text-ink-muted mt-3 leading-relaxed">
            Residency and conflict resolution are deterministic and reproducible
            from the decision record. Nothing is guessed, and a language model is
            not consulted when the answer is decided.
          </p>
        </div>
      </section>

      <section className="border-rule mt-28 border-t pt-12">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <h2 className="measure max-w-[30ch] text-[clamp(1.5rem,3vw,2rem)] leading-tight font-semibold">
            Two devices, one uplink, one disagreement
          </h2>
          <Link
            to="/demo"
            className="press bg-paper-inverse text-ink-inverse inline-flex min-h-11 items-center rounded-sm px-5 text-[0.9375rem] font-medium"
          >
            Run the demonstration
          </Link>
        </div>
        <p className="measure text-ink-muted mt-5 leading-relaxed">
          Six steps against two real devices, a real depot over a real socket and
          real shards. Every figure on that page is measured during the run.
        </p>
      </section>
    </Page>
  );
}