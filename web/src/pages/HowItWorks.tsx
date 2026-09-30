/**
 * How it works.
 *
 * The four verdicts on this page are not illustrations. They were produced by
 * running the scenario and are rendered from the same payload the demonstration
 * renders, so this page cannot drift from what the engine does.
 */

import { useEffect, useState } from "react";
import type { AskResult, VerdictKind } from "../lib/types";
import { Page } from "../components/Shell";
import { VerdictPanel } from "../components/VerdictPanel";

interface Pack {
  key: string;
  claim: string;
  attribute: string;
  conflict: string;
  id_label: string;
  default_attribute: string;
  attributes: Record<string, string>;
  citation: string;
  ladder_version: string;
  ladder: string[];
}

interface Reference {
  verdicts: Record<string, AskResult>;
  kinds: string[];
  packs: Pack[];
}

const ORDER: VerdictKind[] = [
  "ANSWERED_LOCALLY",
  "CONFLICTED",
  "UNRESOLVED_CLOUD_REQUIRED",
  "CORRECTED",
];

const NOTES: Record<VerdictKind, string> = {
  ANSWERED_LOCALLY:
    "The device had enough of its own memory to settle this. It still reports which claims it used, who observed each one, and which retrieval path surfaced it — an answer with nothing behind it is not an answer.",
  CONFLICTED:
    "Two claims about the same aspect, recorded from the same prior state, asserting different values. Both are kept with their attribution. The ladder names who would be entitled to settle it and is deliberately not applied, because the engine escalating is the feature and the engine choosing is the failure.",
  UNRESOLVED_CLOUD_REQUIRED:
    "The device holds a record it may not answer from, and it can say exactly why: every signal that was weighed, its value, and the threshold it crossed. What it would cost to be allowed to answer is reported in bytes rather than asserted in prose.",
  CORRECTED:
    "The same question, asked again after new claims arrived. The earlier answer is kept and returned beside the new one, and the cause is named only when exactly one claim accounts for the change — where several do, the engine declines to attribute it rather than picking one.",
};

export default function HowItWorks() {
  const [ref, setRef] = useState<Reference | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/verdicts")
      .then((r) => r.json())
      .then(async (v) => {
        const p = await fetch("/api/packs").then((r) => r.json());
        setRef({ ...v, packs: p.packs });
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <Page>
      <header className="pt-16 md:pt-20">
        <h1 className="text-[clamp(1.875rem,4vw,2.75rem)] leading-tight font-semibold">
          One interface, and the four things it can say
        </h1>
        <p className="measure text-ink-muted mt-5 leading-relaxed">
          Asking a question about a subject returns exactly one labelled verdict,
          and the verdict&apos;s shape does not change with its kind. There is no
          second query path: the conflict arbiter sits behind this interface
          rather than beside it, because the conflicted verdict{" "}
          <em>is</em> the arbiter&apos;s output.
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

      {ORDER.map((kind) => {
        const ask = ref?.verdicts[kind];
        return (
          <section key={kind} className="mt-20 border-t pt-10 first:mt-16">
            <h2 className="text-[1.375rem] leading-snug font-semibold">{kind}</h2>
            <p className="measure text-ink-muted mt-3 leading-relaxed">
              {NOTES[kind]}
            </p>
            <div className="mt-6">
              {ask ? (
                <VerdictPanel verdict={ask.verdict} deviceId={ask.device_id} />
              ) : (
                <div className="border-rule bg-paper-raised h-32 animate-pulse rounded-sm border" />
              )}
            </div>
          </section>
        );
      })}

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          Corroboration counts sources, not claims
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          A rumour written down four times is four claims and one source. A
          verdict that reported only &ldquo;three supporting claims&rdquo; would
          have dressed a single voice up as a chorus, so the figure is broken out
          by source class: the raw count stays visible, and beside it is the count
          of classes <em>other than this claim&apos;s own</em>. A claim with echoes
          and no independent class says so about itself rather than leaving the
          reader to divide one number by another to discover it.
        </p>
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          Two verticals, no engine change
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          A vertical supplies display labels, an authority ladder, a subject model
          and a regulatory citation. Everything else in the engine is domain-free,
          and a test reads the forbidden words from the packs themselves so the net
          cannot quietly narrow.
        </p>

        <div className="mt-8 grid gap-8 md:grid-cols-2">
          {(ref?.packs ?? []).map((pack) => (
            <div key={pack.key} className="border-rule rounded-sm border p-5">
              <p className="marginal uppercase">{pack.key}</p>
              <dl className="mt-4 space-y-3">
                {[
                  ["calls a record", pack.claim],
                  ["conflicts are", pack.conflict],
                  ["subjects are", pack.id_label],
                  ["asks about", pack.default_attribute],
                  ["ladder", pack.ladder.join(" › ")],
                  ["anchored to", pack.citation],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="marginal">{k}</dt>
                    <dd className="text-ink mt-0.5 text-[0.9375rem]">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          What this project does not claim
        </h2>
        <ul className="measure mt-4 space-y-2.5 text-ink-muted leading-relaxed">
          <li>
            It does not sync to a Qdrant server. The vendor&apos;s server-to-edge
            delta is driven by the shard manifest, which needs a running server;
            where none is available the engine implements the equivalent delta
            exchange against its own endpoint and reports which path was active.
          </li>
          <li>
            It does not de-duplicate claims by identity. Overwrite detection is
            required; identity resolution is not implemented.
          </li>
          <li>
            It does not authenticate, authorise, or model multi-tenancy. None of
            those appear in the requirements.
          </li>
          <li>
            It does not prove the published single-writer pattern destroys unsynced
            writes. That claim is false and disprovable in seconds. The accurate
            and much narrower statement is that the pattern has no merge semantics.
          </li>
        </ul>
      </section>
    </Page>
  );
}