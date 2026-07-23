import { CheckCircle2Icon, CircleOffIcon, DatabaseIcon, FlaskConicalIcon } from "lucide-react";

const RESEARCH_STAGES = [
  ["Plan", "Define claims and acceptance criteria"],
  ["Run", "Execute tracked computational work"],
  ["Review", "Challenge evidence and conclusions"],
  ["Preserve", "Export provenance and artifacts"],
] as const;

export function ResearchSettings({ scienceEnabled }: { scienceEnabled: boolean }) {
  return (
    <div className="flex flex-col gap-8">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-300">
              <FlaskConicalIcon className="size-4" />
            </span>
            <div>
              <p className="text-sm font-medium">OmniSci provenance service</p>
              <p className="text-xs text-muted-foreground">
                {scienceEnabled ? "Connected to this OmniSci server" : "Unavailable on this server"}
              </p>
            </div>
            {scienceEnabled ? (
              <CheckCircle2Icon
                className="ml-auto size-4 text-teal-600 dark:text-teal-300"
                aria-label="Provenance service available"
              />
            ) : (
              <CircleOffIcon
                className="ml-auto size-4 text-muted-foreground"
                aria-label="Provenance service unavailable"
              />
            )}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-lg bg-pink-500/10 text-pink-600 dark:text-pink-300">
              <DatabaseIcon className="size-4" />
            </span>
            <div>
              <p className="text-sm font-medium">Local provenance record</p>
              <p className="text-xs text-muted-foreground">Stored in .omnisci/state.db</p>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <p className="text-sm font-medium">Automatic workspace binding</p>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Every session records provenance in its own working directory. There is no separate
          project path to configure: plans, runs, artifacts and review history follow the folder the
          agent is already using.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <div>
          <p className="text-sm font-medium">Research lifecycle</p>
          <p className="text-sm text-muted-foreground">
            OmniSci keeps the full loop visible, rather than treating research as a single chat
            response.
          </p>
        </div>
        <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {RESEARCH_STAGES.map(([stage, detail], index) => (
            <li key={stage} className="rounded-xl border border-border bg-muted/30 p-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-teal-700 dark:text-teal-300">
                {String(index + 1).padStart(2, "0")} / {stage}
              </span>
              <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
