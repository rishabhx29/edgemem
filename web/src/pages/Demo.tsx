/**
 * The demonstration.
 *
 * Steps are cumulative on the server: asking for step 5 runs one through four
 * first. So every step in the rail is an entry point, and the page never has to
 * defend against being clicked out of order or clicked twice.
 */

import { useCallback, useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { api } from "../lib/api";
import type { AskResult, StoryState, VerdictKind } from "../lib/types";
import { Page } from "../components/Shell";
import { VerdictPanel } from "../components/VerdictPanel";
import { ExchangeStrip, LinkState } from "../components/Transport";

const KINDS: VerdictKind[] = [
  "ANSWERED_LOCALLY",
  "CONFLICTED",
  "UNRESOLVED_CLOUD_REQUIRED",
  "CORRECTED",
];

const DEVICES = ["TRK-7", "DEPOT-2"];

export default function Demo() {
  const reduce = useReducedMotion();
  const [state, setState] = useState<StoryState | null>(null);
  const [active, setActive] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [device, setDevice] = useState(DEVICES[0]);
  const [question, setQuestion] = useState("");
  const [free, setFree] = useState<AskResult | null>(null);
  const [freeBusy, setFreeBusy] = useState(false);

  useEffect(() => {
    api
      .state()
      .then((s) => {
        setState(s);
        setActive(s.reached);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const go = useCallback(
    async (n: number) => {
      setBusy(true);
      setError(null);
      try {
        const s = await api.run(n);
        setState(s);
        setActive(n);
        setFree(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const reset = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await api.reset();
      setState(s);
      setActive(0);
      setFree(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const askFree = useCallback(async () => {
    const q = question.trim();
    if (!q) return;
    setFreeBusy(true);
    setError(null);
    try {
      setFree(await api.ask(device, q));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFreeBusy(false);
    }
  }, [device, question]);

  const steps = state?.steps ?? [];
  const current = steps.find((s) => s.n === active);
  const linkUp = state?.devices[0]?.link_up ?? true;
  const total = steps.length || 6;

  return (
    <Page wide>
      <header className="pt-16 md:pt-20">
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
          <div>
            <h1 className="text-[clamp(1.875rem,4vw,2.75rem)] leading-tight font-semibold">
              The disagreement, end to end
            </h1>
            <p className="measure text-ink-muted mt-4 leading-relaxed">
              Two devices record the same seal differently while neither can reach
              the other. They reconnect. Both claims survive, the engine refuses to
              choose, and it says who would be entitled to.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <LinkState up={linkUp} />
            <button
              type="button"
              onClick={reset}
              disabled={busy}
              className="press-quiet border-rule text-ink-muted inline-flex min-h-11 items-center rounded-sm border px-3.5 text-[0.8125rem] disabled:opacity-40"
            >
              Start over
            </button>
          </div>
        </div>

        <div className="border-rule mt-8 flex flex-wrap gap-2 border-t pt-4">
          {KINDS.map((kind) => {
            const seen = state?.seen?.[kind] ?? false;
            return (
              <span
                key={kind}
                className={`marginal inline-flex items-center gap-1.5 rounded-tag border px-2 py-0.5 !text-[0.6875rem] ${
                  seen
                    ? kind === "CONFLICTED" || kind === "CORRECTED"
                      ? "border-signal-edge text-signal bg-signal-wash"
                      : "border-rule text-ink bg-paper-raised"
                    : "border-rule text-rule-strong"
                }`}
              >
                <span
                  aria-hidden
                  className={`inline-block size-1 rounded-full ${
                    seen ? "bg-current" : "bg-rule-strong"
                  }`}
                />
                {kind}
                {!seen ? " · not yet" : null}
              </span>
            );
          })}
        </div>
      </header>

      {error ? (
        <p
          role="alert"
          className="border-signal-edge text-signal bg-signal-wash mt-8 rounded-sm border p-3 text-[0.875rem]"
        >
          {error}
        </p>
      ) : null}

      <nav aria-label="Scenario steps" className="mt-10">
        <ol className="border-rule grid border-t sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => {
            const done = state ? state.reached >= n : false;
            const isActive = active === n;
            const expected = steps.find((s) => s.n === n)?.expects;
            return (
              <li key={n} className="border-b">
                <button
                  type="button"
                  onClick={() => go(n)}
                  disabled={busy}
                  aria-current={isActive ? "step" : undefined}
                  className={`press-quiet group flex min-h-16 w-full items-start gap-3 px-1 py-3.5 text-left disabled:opacity-50 ${
                    isActive ? "bg-paper-raised" : ""
                  }`}
                >
                  <span
                    className={`figure mt-0.5 shrink-0 text-[0.75rem] ${
                      isActive ? "text-signal" : "text-rule-strong"
                    }`}
                  >
                    {String(n).padStart(2, "0")}
                  </span>
                  <span className="min-w-0">
                    <span
                      className={`block text-[0.9375rem] leading-snug ${
                        isActive
                          ? "text-ink font-medium"
                          : done
                            ? "text-ink"
                            : "text-ink-muted"
                      }`}
                    >
                      {steps.find((s) => s.n === n)?.title ?? `Step ${n}`}
                    </span>
                    <span className="marginal mt-0.5 block">
                      {done ? (expected ?? "executed") : "not run"}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      <motion.section
        key={active}
        initial={reduce ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
        className="mt-12"
      >
        {current ? (
          <>
            <div className="grid gap-6 md:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] md:gap-10">
              <div>
                <p className="marginal uppercase">
                  step {String(current.n).padStart(2, "0")}
                </p>
                <h2 className="mt-2 text-[1.5rem] leading-snug font-semibold">
                  {current.title}
                </h2>
                <p className="text-ink-muted mt-3 text-[0.9375rem] leading-relaxed">
                  {current.note}
                </p>
              </div>

              <div className="space-y-5">
                {current.asks.map((ask) => (
                  <VerdictPanel
                    key={`${ask.device_id}-${ask.question}-${current.n}`}
                    verdict={ask.verdict}
                    deviceId={ask.device_id}
                  />
                ))}
                {current.exchanges.map((report) => (
                  <ExchangeStrip key={`${report.device_id}-${report.started_at}`} report={report} />
                ))}
                {current.asks.length === 0 && current.exchanges.length === 0 ? (
                  <p className="text-ink-muted text-[0.9375rem] italic">
                    Nothing was asked in this step. It only changed what the
                    devices hold.
                  </p>
                ) : null}
              </div>
            </div>
          </>
        ) : (
          <div className="border-rule bg-paper-raised rounded-sm border p-8 text-center">
            <p className="text-ink text-[1.0625rem]">
              Nothing has run yet.
            </p>
            <p className="measure text-ink-muted mx-auto mt-2 leading-relaxed">
              Choose a step above. Each one replays every step before it, so any
              step can be entered directly.
            </p>
            <button
              type="button"
              onClick={() => go(total)}
              disabled={busy}
              className="press bg-paper-inverse text-ink-inverse mt-6 inline-flex min-h-11 items-center rounded-sm px-5 text-[0.9375rem] font-medium disabled:opacity-40"
            >
              Run all six steps
            </button>
          </div>
        )}
      </motion.section>

      <section className="border-rule mt-20 border-t pt-10">
        <h2 className="text-[1.375rem] leading-snug font-semibold">
          Ask it something else
        </h2>
        <p className="measure text-ink-muted mt-3 leading-relaxed">
          No subject is sent with this one. The engine infers which subject the
          question is about from what the device holds, and reports the subject it
          inferred — so you can watch it guess rather than take its word for it.
        </p>

        <form
          className="mt-6 flex flex-wrap items-center gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void askFree();
          }}
        >
          <label htmlFor="device" className="sr-only">
            Device
          </label>
          <select
            id="device"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            className="border-rule bg-paper text-ink min-h-11 rounded-sm border px-3 text-[0.875rem]"
          >
            {DEVICES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <label htmlFor="question" className="sr-only">
            Question
          </label>
          <input
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="what state is the trailer T-114 seal in"
            className="border-rule bg-paper text-ink placeholder:text-ink-faint min-h-11 min-w-0 flex-1 rounded-sm border px-3 text-[0.9375rem]"
          />
          <button
            type="submit"
            disabled={freeBusy || !question.trim()}
            className="press bg-paper-inverse text-ink-inverse inline-flex min-h-11 items-center rounded-sm px-4 text-[0.875rem] font-medium disabled:opacity-40"
          >
            {freeBusy ? "Asking…" : "Ask"}
          </button>
        </form>

        {free ? (
          <div className="mt-6">
            <VerdictPanel verdict={free.verdict} deviceId={free.device_id} />
          </div>
        ) : null}
      </section>
    </Page>
  );
}