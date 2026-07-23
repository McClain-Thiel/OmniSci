import { useMemo, useState, type ReactNode } from "react";
import {
  CheckIcon,
  CircleAlertIcon,
  FileCode2Icon,
  Globe2Icon,
  Loader2Icon,
  SearchIcon,
  ServerCogIcon,
  TerminalSquareIcon,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BRAIN_HARNESS_LABELS, useBrainHarnessLabels } from "@/lib/agentLabels";
import { buildAgentYaml, type AgentBundleInput, type WorkspaceAccessMode } from "@/lib/agentBundle";
import { cn } from "@/lib/utils";

const DEFAULT_HARNESS = Object.keys(BRAIN_HARNESS_LABELS)[0];

const BUILTIN_CAPABILITIES = [
  {
    id: "web_search",
    label: "Web search",
    description: "Find papers, documentation, and current sources.",
    icon: SearchIcon,
  },
  {
    id: "web_fetch",
    label: "Read web pages",
    description: "Open and extract content from selected sources.",
    icon: Globe2Icon,
  },
] as const;

/**
 * Visual editor for one reusable app-level agent template.
 *
 * YAML remains the portable source of truth: the form produces config.yaml
 * and AGENTS.md, while the server stores the bundle as a durable template.
 * Credentials are deliberately absent. Shared tool connections will be
 * referenced from the app-level connection registry rather than copied here.
 */
export function CreateAgentDialog({
  open,
  onOpenChange,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: AgentBundleInput) => void | Promise<void>;
}) {
  const brainHarnessLabels = useBrainHarnessLabels();
  const harnessOptions = Object.entries(brainHarnessLabels).map(([value, label]) => ({
    value,
    label,
  }));
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [harness, setHarness] = useState(DEFAULT_HARNESS);
  const [model, setModel] = useState("");
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceAccessMode>("write");
  const [builtins, setBuiltins] = useState<string[]>(["web_search", "web_fetch"]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const input = useMemo<AgentBundleInput>(
    () => ({
      name: name.trim() || "new-science-agent",
      description: description.trim() || undefined,
      instructions: instructions.trim() || undefined,
      harness,
      model: model.trim() || undefined,
      workspaceMode,
      builtins,
    }),
    [builtins, description, harness, instructions, model, name, workspaceMode],
  );
  const yaml = useMemo(() => buildAgentYaml(input), [input]);

  function reset() {
    setName("");
    setDescription("");
    setInstructions("");
    setHarness(DEFAULT_HARNESS);
    setModel("");
    setWorkspaceMode("write");
    setBuiltins(["web_search", "web_fetch"]);
    setSaving(false);
    setError(null);
  }

  function handleOpenChange(next: boolean) {
    if (saving) return;
    if (!next) reset();
    onOpenChange(next);
  }

  function toggleBuiltin(id: string, enabled: boolean) {
    setBuiltins((current) =>
      enabled ? [...new Set([...current, id])] : current.filter((item) => item !== id),
    );
  }

  async function handleSubmit() {
    const trimmedName = name.trim();
    if (!trimmedName || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onCreate({ ...input, name: trimmedName });
      reset();
      onOpenChange(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setSaving(false);
    }
  }

  const canSubmit = name.trim().length > 0 && !saving;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        data-testid="create-agent-dialog"
        className="flex max-h-[90vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
      >
        <DialogHeader className="border-b border-border px-6 py-5">
          <div className="flex items-start justify-between gap-4 pr-8">
            <div>
              <DialogTitle>Create an agent</DialogTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Save it once, then use it in any project. OmniSci generates the YAML bundle.
              </p>
            </div>
            <Badge variant="outline" className="shrink-0 gap-1.5 font-normal">
              <ServerCogIcon className="size-3" />
              App resource
            </Badge>
          </div>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)]">
          <div className="min-h-0 overflow-y-auto px-6 py-5">
            <FormSection number="01" title="Identity" description="What this agent is for.">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" required htmlFor="create-agent-name">
                  <Input
                    id="create-agent-name"
                    data-testid="create-agent-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="literature-reviewer"
                    autoFocus
                  />
                </Field>
                <Field label="Description" htmlFor="create-agent-description">
                  <Input
                    id="create-agent-description"
                    data-testid="create-agent-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="Reviews evidence and methods"
                  />
                </Field>
              </div>
            </FormSection>

            <FormSection
              number="02"
              title="Runtime"
              description="Defaults can still be overridden when a session starts."
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Harness" required>
                  <Select value={harness} onValueChange={setHarness}>
                    <SelectTrigger data-testid="create-agent-harness" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {harnessOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Model" htmlFor="create-agent-model">
                  <Input
                    id="create-agent-model"
                    data-testid="create-agent-model"
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    placeholder="Use harness default"
                  />
                  <span className="font-normal text-muted-foreground">
                    Leave blank to keep this agent portable across harnesses.
                  </span>
                </Field>
              </div>
            </FormSection>

            <FormSection
              number="03"
              title="Capabilities"
              description="Grant only what this role needs. Credentials stay outside the agent."
            >
              <div className="overflow-hidden rounded-xl border border-border">
                <div className="flex items-center gap-3 border-b border-border px-4 py-3">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    <TerminalSquareIcon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">Workspace & host CLIs</p>
                    <p className="text-xs text-muted-foreground">
                      Read project files and run commands on the selected execution host.
                    </p>
                  </div>
                  <Select
                    value={workspaceMode}
                    onValueChange={(value) => setWorkspaceMode(value as WorkspaceAccessMode)}
                  >
                    <SelectTrigger
                      aria-label="Workspace access"
                      data-testid="create-agent-workspace-mode"
                      className="w-44"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="write">Author outputs</SelectItem>
                      <SelectItem value="read">Read only</SelectItem>
                      <SelectItem value="none">Off</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {BUILTIN_CAPABILITIES.map((capability) => {
                  const Icon = capability.icon;
                  const enabled = builtins.includes(capability.id);
                  return (
                    <div
                      key={capability.id}
                      className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
                    >
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                        <Icon className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{capability.label}</p>
                        <p className="text-xs text-muted-foreground">{capability.description}</p>
                      </div>
                      <Switch
                        aria-label={`Enable ${capability.label}`}
                        checked={enabled}
                        onCheckedChange={(checked) => toggleBuiltin(capability.id, checked)}
                      />
                    </div>
                  );
                })}
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <ResourceNote
                  icon={TerminalSquareIcon}
                  title="Host CLIs"
                  text={
                    workspaceMode === "write"
                      ? "Can write analyses, notebooks, figures, reports, results, and managed science state. Raw data stays read-only."
                      : workspaceMode === "read"
                        ? "Can inspect the project and run read-only commands on the selected host."
                        : "Disabled for this agent."
                  }
                  badge={
                    workspaceMode === "write"
                      ? "Authoring"
                      : workspaceMode === "read"
                        ? "Read only"
                        : "Off"
                  }
                />
                <ResourceNote
                  icon={ServerCogIcon}
                  title="Shared connections"
                  text="MCP and signed-in services will attach here by stable app reference."
                  badge="Next slice"
                />
              </div>
            </FormSection>

            <FormSection
              number="04"
              title="Instructions"
              description="Stored as AGENTS.md beside the generated config."
            >
              <Field label="System instructions" htmlFor="create-agent-instructions">
                <Textarea
                  id="create-agent-instructions"
                  data-testid="create-agent-instructions"
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  placeholder="You are a rigorous scientific reviewer. Check methods, evidence, and reproducibility…"
                  className="min-h-36 resize-y"
                />
              </Field>
            </FormSection>
          </div>

          <aside className="flex min-h-0 flex-col border-t border-border bg-muted/25 lg:border-t-0 lg:border-l">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <FileCode2Icon className="size-4 text-muted-foreground" />
                <span className="text-xs font-medium">Generated manifest</span>
              </div>
              <span className="font-mono text-[10px] text-muted-foreground">config.yaml</span>
            </div>
            <pre
              data-testid="create-agent-yaml-preview"
              className="min-h-0 flex-1 overflow-auto p-4 font-mono text-[11px] leading-5 text-foreground"
            >
              {yaml}
            </pre>
            <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
              <p className="flex items-start gap-2">
                <CheckIcon className="mt-0.5 size-3.5 shrink-0 text-teal-600 dark:text-teal-300" />
                YAML remains portable and can be exported or edited directly later.
              </p>
            </div>
          </aside>
        </div>

        <DialogFooter className="border-t border-border px-6 py-4 sm:justify-between">
          <div className="min-w-0 flex-1">
            {error && (
              <p className="flex items-center gap-1.5 text-xs text-destructive" role="alert">
                <CircleAlertIcon className="size-3.5 shrink-0" />
                <span className="truncate">{error}</span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => handleOpenChange(false)} disabled={saving}>
              Cancel
            </Button>
            <Button
              data-testid="create-agent-submit"
              onClick={() => void handleSubmit()}
              disabled={!canSubmit}
              className="min-w-28"
            >
              {saving ? <Loader2Icon className="size-4 animate-spin" /> : "Save agent"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FormSection({
  number,
  title,
  description,
  children,
}: {
  number: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-border py-5 first:pt-0 last:border-b-0 last:pb-0">
      <div className="mb-3 flex items-start gap-3">
        <span className="pt-0.5 font-mono text-[10px] text-muted-foreground">{number}</span>
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="pl-7">{children}</div>
    </section>
  );
}

function Field({
  label,
  required = false,
  htmlFor,
  children,
}: {
  label: string;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-xs font-medium" htmlFor={htmlFor}>
      <span>
        {label} {required && <span className="text-destructive">*</span>}
      </span>
      {children}
    </label>
  );
}

function ResourceNote({
  icon: Icon,
  title,
  text,
  badge,
}: {
  icon: typeof ServerCogIcon;
  title: string;
  text: string;
  badge: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" />
        <p className="text-xs font-medium">{title}</p>
        <Badge
          variant="outline"
          className={cn("ml-auto px-1.5 py-0 text-[9px] font-normal", {
            "border-teal-500/30 text-teal-700 dark:text-teal-300": badge === "Automatic",
          })}
        >
          {badge}
        </Badge>
      </div>
      <p className="mt-2 text-xs leading-4 text-muted-foreground">{text}</p>
    </div>
  );
}
