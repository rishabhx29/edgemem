/**
 * Layout and navigation.
 *
 * Four pages, one header, one measure. The width cap is deliberately narrow for
 * the prose and wider for the demonstration, because the verdict panels carry
 * tables that need the room while a paragraph does not.
 */

import { NavLink, Outlet } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Overview", end: true },
  { to: "/demo", label: "The demonstration", end: false },
  { to: "/how", label: "How it works", end: false },
  { to: "/evidence", label: "Evidence", end: false },
];

export function Shell() {
  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="bg-paper-inverse text-ink-inverse sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[var(--z-modal)] focus:rounded-sm focus:px-3 focus:py-2 focus:text-sm"
      >
        Skip to content
      </a>

      <header className="border-rule bg-paper/90 sticky top-0 z-[var(--z-sticky)] border-b backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-6 px-5 sm:px-8">
          <NavLink
            to="/"
            className="press-quiet text-ink shrink-0 text-[0.9375rem] font-semibold tracking-[-0.02em]"
          >
            edgemem
          </NavLink>

          <nav aria-label="Primary" className="flex min-w-0 flex-1 gap-5 overflow-x-auto">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  `press-quiet -mb-px shrink-0 border-b-2 px-0.5 pb-1 text-[0.875rem] whitespace-nowrap ${
                    isActive
                      ? "border-signal text-ink font-medium"
                      : "border-transparent text-ink-muted hover:text-ink"
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>

          <NavLink
            to="/demo"
            className="press bg-paper-inverse text-ink-inverse hidden rounded-sm px-3.5 py-2 text-[0.8125rem] font-medium sm:inline-block"
          >
            Run it
          </NavLink>
        </div>
      </header>

      <main id="main">
        <Outlet />
      </main>

      <footer className="border-rule mt-24 border-t">
        <div className="mx-auto max-w-[1400px] px-5 py-10 sm:px-8">
          <p className="marginal max-w-[70ch] leading-relaxed">
            Every verdict, byte count and latency on this site was produced by the
            engine during the request that rendered it. Nothing on these pages is
            authored sample data, and nothing is estimated. Built on the in-process
            vector engine from Qdrant's Edge release, pinned to an exact wheel.
          </p>
          <p className="marginal mt-4">
            MIT licensed. The question interface is the only seam; the interface
            you are reading is a projection of what it returns.
          </p>
        </div>
      </footer>
    </div>
  );
}

/** The page's measure. Prose stops here; the demonstration does not. */
export function Page({
  children,
  wide = false,
}: {
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className={`mx-auto px-5 sm:px-8 ${wide ? "max-w-[1400px]" : "max-w-[68rem]"}`}
    >
      {children}
    </div>
  );
}