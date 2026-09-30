/**
 * The verdict, rendered.
 *
 * Every value in this file comes from a `Verdict`. There is no state kept here,
 * nothing derived, and no branch that reaches for information the payload does
 * not carry — which is the whole claim the interface makes, kept in one place so
 * it is checkable in one place.
 */

import type {
  AuthorityView,
  CitedClaim,
  Corroboration,
  NeededClaim,
  Verdict,
  VerdictKind,
} from "../lib/types";
import { Band, Chip, Figure, KeyValue, bytes, shortId } from "./Primitives";

const KIND_COPY: Record<VerdictKind, { say: string; className: string }> = {
  ANSWERED_LOCALLY: {
    say: "Settled from this device's own memory.",
    className: "text-verdict-answered",
  },
  CONFLICTED: {
    say: "Two claims cannot both be true. Neither was chosen between.",
    className: "text-signal",
  },
  UNRESOLVED_CLOUD_REQUIRED: {
    say: "The device holds nothing it is permitted to answer from.",
    className: "text-verdict-unresolved",
  },
  CORRECTED: {
    say: "This question answers differently now. Both answers are shown.",
    className: "text-signal",
  },
};

export function VerdictKindLabel({ kind }: { kind: VerdictKind }) {
  const copy = KIND_COPY[kind];
  return (
    <span className={`marginal !text-[0.75rem] font-semibold ${copy.className}`}>
      {kind}
    </span>
  );
}

function Attribution({ claim }: { claim: CitedClaim }) {
  return (
    <div className="marginal mt-1">
      {claim.author} &middot; {claim.observer} &middot; {claim.device_id} &middot;{" "}
      <Figure>{shortId(claim.claim_id)}</Figure>
    </div>
  );
}

function CorroborationNote({ c }: { c: Corroboration | null }) {
  if (!c) return null;
  return (
    <p className="marginal mt-1.5">
      {c.voices} voice{c.voices === 1 ? "" : "s"}, {c.echoes} echo
      {c.echoes === 1 ? "" : "es"}
      {c.independent_classes.length > 0 ? (
        <>
          {" "}
          &middot; independent: {c.independent_classes.join(", ")}
        </>
      ) : null}
      {c.self_corroborated ? " · self-corroborated" : null}
    </p>
  );
}

function ClaimRow({ claim }: { claim: CitedClaim }) {
  return (
    <tr>
      <td className="align-top">
        <span className="text-ink text-[0.9375rem]">{claim.value}</span>
        <div className="marginal mt-0.5">{claim.attribute}</div>
      </td>
      <td className="align-top">
        <span className="text-ink-muted text-[0.875rem]">{claim.author}</span>
        <div className="marginal mt-0.5">{claim.observer}</div>
      </td>
      <td className="align-top">
        <span
          className={`marginal !text-[0.75rem] font-semibold ${
            claim.residency === "LOCAL" ? "text-verdict-answered" : "text-ink"
          }`}
        >
          {claim.residency}
        </span>
        <div className="marginal mt-1 max-w-[34ch] leading-relaxed">
          {claim.reason}
        </div>
      </td>
    </tr>
  );
}

function NeededRow({ claim }: { claim: NeededClaim }) {
  return (
    <tr>
      <td className="align-top">
        <span className="text-ink text-[0.9375rem]">{claim.value}</span>
        <div className="marginal mt-0.5">
          <Figure>{shortId(claim.claim_id)}</Figure>
        </div>
      </td>
      <td className="align-top">
        <span className="marginal block max-w-[46ch] leading-relaxed">
          {claim.why_withheld}
        </span>
      </td>
      <td className="align-top">
        <Figure className="text-ink text-[0.8125rem]">
          {bytes(claim.bytes_if_sent)}
        </Figure>
      </td>
    </tr>
  );
}

function Ladder({ view }: { view: AuthorityView }) {
  return (
    <div className="border-rule bg-paper-raised mt-3 border p-3.5">
      <div className="marginal">
        ladder <Figure className="!text-ink">{view.ladder_version}</Figure>
      </div>
      <ol className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {view.ordered_classes.map((cls, i) => {
          const entitling = cls === view.entitling_class;
          return (
            <li key={cls} className="flex items-center gap-1.5">
              {i > 0 ? <span className="text-rule-strong">›</span> : null}
              <span
                className={`figure text-[0.75rem] ${
                  entitling
                    ? "text-signal font-semibold"
                    : "text-ink-muted"
                }`}
              >
                {cls}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="marginal mt-2.5 max-w-[62ch] leading-relaxed">{view.note}</p>
      <p className="marginal mt-2 text-ink">
        Entitled to settle:{" "}
        <Figure className="!text-signal font-semibold">
          {view.entitling_class ?? "none present"}
        </Figure>
        . Presented, not applied.
      </p>
    </div>
  );
}

export function VerdictPanel({
  verdict,
  deviceId,
}: {
  verdict: Verdict;
  deviceId: string;
}) {
  const copy = KIND_COPY[verdict.kind];

  return (
    <article className="border-rule bg-paper border p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <VerdictKindLabel kind={verdict.kind} />
        <div className="marginal">
          asked by <Figure className="!text-ink">{deviceId}</Figure>
        </div>
      </header>

      <p className="measure text-ink mt-3 text-[1.0625rem] leading-relaxed">
        {verdict.summary}
      </p>

      <div className="mt-3.5 flex flex-wrap gap-1.5">
        <Chip label="latency" value={`${verdict.latency_ms.toFixed(2)} ms`} />
        <Chip label="paths" value={verdict.paths_used.join(" + ") || "none"} />
        {verdict.bytes_withheld > 0 ? (
          <Chip label="withheld" value={bytes(verdict.bytes_withheld)} />
        ) : null}
        {verdict.changed_by ? (
          <Chip label="changed by" value={shortId(verdict.changed_by)} />
        ) : null}
      </div>

      {verdict.previous_summary ? (
        <Band title="what this device said last time">
          <p className="border-rule bg-paper-raised text-ink-muted max-w-[62ch] rounded-sm border p-3 text-[0.9375rem] leading-relaxed">
            {verdict.previous_summary}
          </p>
        </Band>
      ) : null}

      {verdict.conflicts.length > 0 ? (
        <Band title="the disagreement">
          {verdict.conflicts.map((pair, i) => (
            <div key={i} className="mb-4 last:mb-0">
              {(["a", "b"] as const).map((side) => {
                const claim = pair[side];
                const entitling = pair[`${side}_entitling` as "a_entitling"];
                return (
                  <div
                    key={side}
                    className={`mb-2 rounded-sm border p-3.5 last:mb-0 ${
                      entitling
                        ? "border-signal-edge bg-signal-wash"
                        : "border-rule bg-paper-raised"
                    }`}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                      <span className="text-ink text-[1.0625rem] font-medium">
                        {claim.value}
                      </span>
                      <span
                        className={`marginal rounded-tag border px-1.5 py-px !text-[0.6875rem] font-semibold ${
                          entitling
                            ? "border-signal-edge text-signal bg-paper"
                            : "border-rule text-ink-muted"
                        }`}
                      >
                        {entitling ? "entitled to settle" : "retained"}
                      </span>
                    </div>
                    <Attribution claim={claim} />
                    <div className="marginal mt-1">
                      <Figure>{pair[`${side}_supporters` as "a_supporters"]}</Figure>{" "}
                      claim(s) assert this
                    </div>
                    <CorroborationNote
                      c={pair[`${side}_corroboration` as "a_corroboration"]}
                    />
                  </div>
                );
              })}
            </div>
          ))}
          {verdict.authority ? <Ladder view={verdict.authority} /> : null}
        </Band>
      ) : null}

      {verdict.claims.length > 0 ? (
        <Band title="claims behind this answer">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-rule border-b">
                <th className="marginal py-2 text-left font-medium">value</th>
                <th className="marginal py-2 text-left font-medium">
                  who observed it
                </th>
                <th className="marginal py-2 text-left font-medium">
                  residency, and why
                </th>
              </tr>
            </thead>
            <tbody>
              {verdict.claims.map((claim) => (
                <ClaimRow key={claim.claim_id} claim={claim} />
              ))}
            </tbody>
          </table>
        </Band>
      ) : null}

      {verdict.needed.length > 0 ? (
        <Band title="missing, and what it would cost">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-rule border-b">
                <th className="marginal py-2 text-left font-medium">held</th>
                <th className="marginal py-2 text-left font-medium">
                  why it cannot answer
                </th>
                <th className="marginal py-2 text-left font-medium">cost</th>
              </tr>
            </thead>
            <tbody>
              {verdict.needed.map((claim) => (
                <NeededRow key={claim.claim_id} claim={claim} />
              ))}
            </tbody>
          </table>
        </Band>
      ) : null}

      <footer className="border-rule mt-6 border-t pt-3">
        <KeyValue
          rows={[
            ["question", verdict.question || "—"],
            ["subject", verdict.subject || "none inferred"],
            ["retrieval", verdict.paths_used.join(", ") || "—"],
            [
              "anchored to",
              <span key="c" className="font-mono text-[0.75rem]">
                {verdict.citation}
              </span>,
            ],
          ]}
        />
      </footer>

      <p className="marginal mt-3 leading-relaxed">
        {copy.say}
      </p>
    </article>
  );
}