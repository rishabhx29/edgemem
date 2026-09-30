/**
 * The wire contract.
 *
 * Every shape here is transcribed field-for-field from a `to_payload()` in the
 * engine. That is deliberate: if the engine renames a key, this file stops
 * compiling and the interface breaks at build time instead of rendering
 * `undefined` in front of somebody judging the project.
 *
 * Nothing in this file is invented. There is no view model, no client-side
 * derivation, and no field the engine does not produce.
 */

export type VerdictKind =
  | "ANSWERED_LOCALLY"
  | "CONFLICTED"
  | "UNRESOLVED_CLOUD_REQUIRED"
  | "CORRECTED";

export type Residency = "LOCAL" | "SYNC" | "CLOUD_ONLY";

export type Trust = "TRUSTED" | "QUARANTINED";

/** How much of a claim's apparent agreement is somebody other than itself. */
export interface Corroboration {
  /** [source class, agreeing claims], highest entitled class first. */
  classes: [string, number][];
  /** The classes *other than* this claim's own. The part that is corroboration. */
  independent_classes: string[];
  /** Agreeing claims from this claim's own class. Not support. */
  echoes: number;
  voices: number;
  self_corroborated: boolean;
}

/** A claim as a caller sees it: content plus attribution. */
export interface CitedClaim {
  claim_id: string;
  subject: string;
  attribute: string;
  value: string;
  author: string;
  observer: string;
  device_id: string;
  observed_at: string;
  source_class: string;
  trust: Trust;
  residency: Residency;
  /** The residency signals that produced the decision, rendered. */
  reason: string;
  corroborations: number;
  stale: boolean;
  /** Which retrieval legs surfaced it: dense, keyword, or both. */
  paths: string[];
  corroboration: Corroboration | null;
}

/** Who could settle a conflict. Presented, never applied. */
export interface AuthorityView {
  ladder_version: string;
  ordered_classes: string[];
  entitling_class: string | null;
  note: string;
}

/** One assertion in a disagreement. */
export interface ConflictSide {
  claim: CitedClaim;
  supporters: number;
  entitling: boolean;
  corroboration: Corroboration | null;
}

export interface NeededClaim {
  claim_id: string;
  subject: string;
  attribute: string;
  value: string;
  why_withheld: string;
  bytes_if_sent: number;
  device_id: string;
}

/** The single output of the question interface. */
export interface Verdict {
  kind: VerdictKind;
  subject: string;
  question: string;
  summary: string;
  claims: CitedClaim[];
  conflicts: {
    a: CitedClaim;
    a_supporters: number;
    a_entitling: boolean;
    a_corroboration: Corroboration | null;
    b: CitedClaim;
    b_supporters: number;
    b_entitling: boolean;
    b_corroboration: Corroboration | null;
  }[];
  authority: AuthorityView | null;
  needed: NeededClaim[];
  previous_summary: string | null;
  changed_by: string | null;
  /** Measured off the clock around the question, never modelled. */
  latency_ms: number;
  paths_used: string[];
  bytes_withheld: number;
  citation: string;
}

/**
 * Device transport, reported by the sync path.
 *
 * The only payload in this file that does not come from `ask()`. See
 * docs/adr/0001-demo-transport-rail.md for why it exists and what it is not.
 */
export interface SyncReport {
  path: string;
  device_id: string;
  depot_id: string;
  depot_path: string;
  started_at: string;
  duration_ms: number;
  offline_seconds: number;
  pushed: number;
  accepted: number;
  duplicates: number;
  withheld: number;
  pulled: number;
  bytes_sent: number;
  bytes_received: number;
  depot_bytes_received: number;
  depot_delta_bytes: number;
  cursor_before: number;
  cursor_after: number;
  held_during_apply: boolean;
  reapplied: boolean;
  complete: boolean;
  notes: string[];
}

/** One `ask()` response, as the device column received it. */
export interface AskResult {
  device_id: string;
  question: string;
  verdict: Verdict;
}

/**
 * One step of the guided scenario.
 *
 * A step is a pure function of the steps before it, so any step can be entered
 * directly: the server replays 1..n. That is what makes the demo jump-safe
 * instead of a sequence you can only run in order, twice.
 */
export interface StoryStep {
  n: number;
  title: string;
  /** What the operator should watch for, in one sentence. */
  note: string;
  /** The questions this step asks, if any. */
  asks: AskResult[];
  /** The sync exchanges this step performed, if any. */
  exchanges: SyncReport[];
  /** Which devices are holding the uplink, after this step. */
  link_up: boolean;
  /** The verdict kind this step is meant to produce, when it produces one. */
  expects: VerdictKind | null;
}

export interface StoryState {
  seeded: boolean;
  /** The highest step that has been run. */
  reached: number;
  steps: StoryStep[];
  devices: { id: string; link_up: boolean }[];
  vertical: { label: string; ladder_version: string; citation: string };
  /** The four kinds, and whether this run has produced one yet. */
  seen: Record<VerdictKind, boolean>;
}