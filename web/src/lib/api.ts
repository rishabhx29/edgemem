import type { AskResult, StoryState } from "./types";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  /** Everything the demo has done so far, plus the steps it can run. */
  state: () => json<StoryState>("/api/state"),

  /**
   * Run the scenario up to and including `n`.
   *
   * Cumulative on purpose: step 6 replays 1..6, so skipping ahead cannot
   * produce a state the engine would not have reached on its own.
   */
  run: (n: number) =>
    json<StoryState>("/api/step", { method: "POST", body: JSON.stringify({ n }) }),

  /** Throw the console away and build a fresh one. */
  reset: () => json<StoryState>("/api/reset", { method: "POST" }),

  /**
   * Ask one device a question.
   *
   * No subject is sent. The engine infers it from what the device actually
   * holds and declines when nothing stands out, which is the behaviour worth
   * being able to try rather than read about.
   */
  ask: (deviceId: string, question: string) =>
    json<AskResult>(
      `/api/ask?device=${encodeURIComponent(deviceId)}&q=${encodeURIComponent(question)}`,
    ),
};