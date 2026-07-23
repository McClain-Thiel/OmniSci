import { useState, type ReactNode } from "react";
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  CloudIcon,
  CpuIcon,
  HardDriveIcon,
  LoaderCircleIcon,
  LockKeyholeIcon,
  ServerIcon,
  TerminalIcon,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  useScienceInfrastructure,
  useScienceTools,
  useUpdateScienceInfrastructure,
} from "@/hooks/useScience";
import type {
  ScienceComputeConfig,
  ScienceInfrastructureStatus,
  ScienceProviderStatus,
  ScienceStorageConfig,
} from "@/lib/scienceApi";
import { cn } from "@/lib/utils";

function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function stringValue(config: Record<string, unknown>, key: string, fallback = ""): string {
  const value = config[key];
  return typeof value === "string" ? value : fallback;
}

function numberValue(config: Record<string, unknown>, key: string, fallback: number): string {
  const value = config[key];
  return typeof value === "number" ? String(value) : String(fallback);
}

function providerById(
  providers: ScienceProviderStatus[],
  id: string,
): ScienceProviderStatus | undefined {
  return providers.find((provider) => provider.id === id);
}

function statusLabel(provider: ScienceProviderStatus | undefined): string {
  if (!provider?.registered) return "Unavailable";
  if (!provider.dependencyAvailable) return "SDK missing";
  if (provider.configured) return provider.default ? "Default" : "Configured";
  return "Available";
}

function ProviderCard({
  title,
  description,
  icon,
  provider,
  children,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  provider?: ScienceProviderStatus;
  children?: ReactNode;
}) {
  const ready = provider?.registered && provider.dependencyAvailable;
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-lg",
            ready
              ? "bg-teal-500/10 text-teal-700 dark:text-teal-300"
              : "bg-muted text-muted-foreground",
          )}
        >
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-medium">{title}</p>
            <Badge variant={ready ? "secondary" : "outline"}>{statusLabel(provider)}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        </div>
        {ready ? (
          <CheckCircle2Icon className="mt-1 size-4 shrink-0 text-teal-600 dark:text-teal-300" />
        ) : (
          <CircleAlertIcon className="mt-1 size-4 shrink-0 text-muted-foreground" />
        )}
      </div>
      {children}
    </div>
  );
}

function InfrastructureError({ error }: { error: Error }) {
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>Could not load infrastructure</AlertTitle>
      <AlertDescription>{error.message}</AlertDescription>
    </Alert>
  );
}

function LoadingInfrastructure() {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
      <LoaderCircleIcon className="size-4 animate-spin" />
      Inspecting infrastructure…
    </div>
  );
}

function SaveState({
  pending,
  success,
  error,
}: {
  pending: boolean;
  success: boolean;
  error: Error | null;
}) {
  if (pending) return <span className="text-xs text-muted-foreground">Validating…</span>;
  if (error) return <span className="text-xs text-destructive">{error.message}</span>;
  if (success) return <span className="text-xs text-teal-700 dark:text-teal-300">Saved</span>;
  return null;
}

export function ComputeSettings() {
  const query = useScienceInfrastructure();
  const mutation = useUpdateScienceInfrastructure();

  if (query.isLoading) return <LoadingInfrastructure />;
  if (query.error) return <InfrastructureError error={query.error} />;
  if (!query.data) return null;

  return (
    <ComputeForm
      key={JSON.stringify(query.data.computeConfig)}
      status={query.data}
      onSave={(computeConfig) => mutation.mutate({ computeConfig })}
      pending={mutation.isPending}
      success={mutation.isSuccess}
      error={mutation.error}
    />
  );
}

function ComputeForm({
  status,
  onSave,
  pending,
  success,
  error,
}: {
  status: ScienceInfrastructureStatus;
  onSave: (config: ScienceComputeConfig) => void;
  pending: boolean;
  success: boolean;
  error: Error | null;
}) {
  const local = providerById(status.compute, "local");
  const modal = providerById(status.compute, "modal");
  const ssh = providerById(status.compute, "ssh");
  const slurm = providerById(status.compute, "slurm");
  const qsub = providerById(status.compute, "qsub");
  const modalConfig = status.computeConfig.providers.modal ?? {};
  const sshConfig = status.computeConfig.providers.ssh ?? {};
  const slurmConfig = status.computeConfig.providers.slurm ?? {};
  const qsubConfig = status.computeConfig.providers.qsub ?? {};
  const [modalEnabled, setModalEnabled] = useState(modal?.configured ?? false);
  const [sshEnabled, setSshEnabled] = useState(ssh?.configured ?? false);
  const [slurmEnabled, setSlurmEnabled] = useState(slurm?.configured ?? false);
  const [qsubEnabled, setQsubEnabled] = useState(qsub?.configured ?? false);
  const [defaultProvider, setDefaultProvider] = useState(status.computeConfig.defaultProvider);
  const [appName, setAppName] = useState(stringValue(modalConfig, "app_name", "omnisci-science"));
  const [image, setImage] = useState(stringValue(modalConfig, "default_image", "python:3.12-slim"));
  const [region, setRegion] = useState(stringValue(modalConfig, "region"));
  const [cloud, setCloud] = useState(stringValue(modalConfig, "cloud"));
  const [maxRuntime, setMaxRuntime] = useState(numberValue(modalConfig, "max_runtime_minutes", 60));
  const [graceSeconds, setGraceSeconds] = useState(
    numberValue(modalConfig, "collection_grace_seconds", 3600),
  );
  const [sshHost, setSshHost] = useState(stringValue(sshConfig, "host"));
  const [sshUser, setSshUser] = useState(stringValue(sshConfig, "user", "ec2-user"));
  const [sshPort, setSshPort] = useState(numberValue(sshConfig, "port", 22));
  const [identityFile, setIdentityFile] = useState(stringValue(sshConfig, "identity_file"));
  const [knownHostsFile, setKnownHostsFile] = useState(
    stringValue(sshConfig, "known_hosts_file", "~/.ssh/known_hosts"),
  );
  const [remoteRoot, setRemoteRoot] = useState(
    stringValue(sshConfig, "remote_root", "/tmp/omnisci"),
  );
  const [sshMaxRuntime, setSshMaxRuntime] = useState(
    numberValue(sshConfig, "max_runtime_minutes", 60),
  );
  const [cleanupRemote, setCleanupRemote] = useState(sshConfig.cleanup_remote !== false);
  const [slurmPartition, setSlurmPartition] = useState(stringValue(slurmConfig, "partition"));
  const [slurmAccount, setSlurmAccount] = useState(stringValue(slurmConfig, "account"));
  const [slurmQos, setSlurmQos] = useState(stringValue(slurmConfig, "qos"));
  const [slurmConstraint, setSlurmConstraint] = useState(stringValue(slurmConfig, "constraint"));
  const [qsubDialect, setQsubDialect] = useState(stringValue(qsubConfig, "dialect", "pbs"));
  const [qsubQueue, setQsubQueue] = useState(stringValue(qsubConfig, "queue"));
  const [qsubAccount, setQsubAccount] = useState(stringValue(qsubConfig, "account"));
  const [parallelEnvironment, setParallelEnvironment] = useState(
    stringValue(qsubConfig, "parallel_environment", "smp"),
  );
  const [gpuResource, setGpuResource] = useState(
    stringValue(qsubConfig, "gpu_resource", qsubDialect === "pbs" ? "ngpus" : "gpu"),
  );
  const runtime = Number(maxRuntime);
  const grace = Number(graceSeconds);
  const port = Number(sshPort);
  const sshRuntime = Number(sshMaxRuntime);
  const modalValid =
    appName.trim().length > 0 &&
    image.trim().length > 0 &&
    Number.isFinite(runtime) &&
    runtime > 0 &&
    Number.isFinite(grace) &&
    grace >= 60;
  const sshValid =
    sshHost.trim().length > 0 &&
    sshUser.trim().length > 0 &&
    knownHostsFile.trim().length > 0 &&
    remoteRoot.trim().startsWith("/") &&
    Number.isInteger(port) &&
    port >= 1 &&
    port <= 65535 &&
    Number.isFinite(sshRuntime) &&
    sshRuntime > 0;

  const save = () => {
    const providers = { ...status.computeConfig.providers };
    if (modalEnabled) {
      providers.modal = {
        ...modalConfig,
        app_name: appName.trim(),
        default_image: image.trim(),
        max_runtime_minutes: runtime,
        collection_grace_seconds: grace,
        ...(region.trim() ? { region: region.trim() } : {}),
        ...(cloud.trim() ? { cloud: cloud.trim() } : {}),
      };
      if (!region.trim()) delete providers.modal.region;
      if (!cloud.trim()) delete providers.modal.cloud;
    } else {
      delete providers.modal;
    }
    if (sshEnabled) {
      providers.ssh = {
        ...sshConfig,
        host: sshHost.trim(),
        user: sshUser.trim(),
        port,
        known_hosts_file: knownHostsFile.trim(),
        remote_root: remoteRoot.trim(),
        max_runtime_minutes: sshRuntime,
        cleanup_remote: cleanupRemote,
        ...(identityFile.trim() ? { identity_file: identityFile.trim() } : {}),
      };
      if (!identityFile.trim()) delete providers.ssh.identity_file;
    } else {
      delete providers.ssh;
    }
    if (slurmEnabled) {
      providers.slurm = {
        ...slurmConfig,
        transport_ref: "ssh",
        ...(slurmPartition.trim() ? { partition: slurmPartition.trim() } : {}),
        ...(slurmAccount.trim() ? { account: slurmAccount.trim() } : {}),
        ...(slurmQos.trim() ? { qos: slurmQos.trim() } : {}),
        ...(slurmConstraint.trim() ? { constraint: slurmConstraint.trim() } : {}),
      };
      for (const key of ["partition", "account", "qos", "constraint"]) {
        if (!String(providers.slurm[key] ?? "").trim()) delete providers.slurm[key];
      }
    } else {
      delete providers.slurm;
    }
    if (qsubEnabled) {
      providers.qsub = {
        ...qsubConfig,
        transport_ref: "ssh",
        dialect: qsubDialect,
        ...(qsubQueue.trim() ? { queue: qsubQueue.trim() } : {}),
        ...(qsubAccount.trim() ? { account: qsubAccount.trim() } : {}),
        ...(parallelEnvironment.trim() ? { parallel_environment: parallelEnvironment.trim() } : {}),
        ...(gpuResource.trim() ? { gpu_resource: gpuResource.trim() } : {}),
      };
      for (const key of ["queue", "account", "parallel_environment", "gpu_resource"]) {
        if (!String(providers.qsub[key] ?? "").trim()) delete providers.qsub[key];
      }
    } else {
      delete providers.qsub;
    }
    const selectedProvider =
      (defaultProvider === "modal" && !modalEnabled) ||
      (defaultProvider === "ssh" && !sshEnabled) ||
      (defaultProvider === "slurm" && !slurmEnabled) ||
      (defaultProvider === "qsub" && !qsubEnabled)
        ? "local"
        : defaultProvider;
    onSave({
      defaultProvider: selectedProvider,
      providers,
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <Alert>
        <ServerIcon />
        <AlertTitle>App-level compute resources</AlertTitle>
        <AlertDescription>
          These connectors and defaults apply to every OmniSci run and cannot be overridden by a
          project. Project policy still controls whether a run needs approval. Configuration:{" "}
          <span className="font-mono text-xs">{status.configPath}</span>
        </AlertDescription>
      </Alert>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <ProviderCard
          title="Local process"
          description="Runs commands on this machine with project-scoped approvals and captured outputs."
          icon={<ServerIcon className="size-4" />}
          provider={local}
        />
        <ProviderCard
          title="Modal Sandbox"
          description="Isolated remote sandboxes for longer CPU or GPU work."
          icon={<CloudIcon className="size-4" />}
          provider={modal}
        >
          {!modal?.dependencyAvailable && (
            <p className="mt-3 rounded-lg bg-muted/50 px-3 py-2 font-mono text-xs text-muted-foreground">
              uv sync --extra modal
            </p>
          )}
        </ProviderCard>
        <ProviderCard
          title="SSH host"
          description="Runs on an existing Linux workstation, cluster login node, or cloud VM."
          icon={<TerminalIcon className="size-4" />}
          provider={ssh}
        />
        <ProviderCard
          title="Slurm cluster"
          description="Submits asynchronous jobs with sbatch and reconciles through squeue and sacct."
          icon={<CpuIcon className="size-4" />}
          provider={slurm}
        />
        <ProviderCard
          title="PBS / Grid Engine"
          description="Supports qsub clusters with PBS qstat or Grid Engine qstat and qacct."
          icon={<ServerIcon className="size-4" />}
          provider={qsub}
        />
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div>
          <h2 className="text-sm font-medium">Compute routing</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            New execution specs can target any configured provider. Each remote run still requires
            project approval unless policy explicitly allows it.
          </p>
        </div>
        <Field label="Default compute provider">
          <Select value={defaultProvider} onValueChange={setDefaultProvider}>
            <SelectTrigger className="mt-3 w-full sm:max-w-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="local">Local process</SelectItem>
              {modalEnabled && <SelectItem value="modal">Modal Sandbox</SelectItem>}
              {sshEnabled && <SelectItem value="ssh">SSH host</SelectItem>}
              {slurmEnabled && <SelectItem value="slurm">Slurm cluster</SelectItem>}
              {qsubEnabled && <SelectItem value="qsub">PBS / Grid Engine</SelectItem>}
            </SelectContent>
          </Select>
        </Field>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-medium">Modal compute</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Modal uses the server's authenticated CLI profile or workload identity. Tokens are
              never stored in OmniSci configuration.
            </p>
          </div>
          <Switch
            aria-label="Enable Modal compute"
            checked={modalEnabled}
            disabled={!modal?.registered || !modal.dependencyAvailable}
            onCheckedChange={(checked) => {
              setModalEnabled(checked);
              if (!checked && defaultProvider === "modal") setDefaultProvider("local");
            }}
          />
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="Modal app name">
            <Input
              value={appName}
              onChange={(event) => setAppName(event.target.value)}
              disabled={!modalEnabled}
            />
          </Field>
          <Field label="Default container image">
            <Input
              value={image}
              onChange={(event) => setImage(event.target.value)}
              disabled={!modalEnabled}
              className="font-mono text-xs"
            />
          </Field>
          <Field label="Modal maximum runtime (minutes)">
            <Input
              type="number"
              min="1"
              value={maxRuntime}
              onChange={(event) => setMaxRuntime(event.target.value)}
              disabled={!modalEnabled}
            />
          </Field>
          <Field label="Region" hint="Optional">
            <Input
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              disabled={!modalEnabled}
              placeholder="eu-west"
            />
          </Field>
          <Field label="Cloud" hint="Optional">
            <Input
              value={cloud}
              onChange={(event) => setCloud(event.target.value)}
              disabled={!modalEnabled}
              placeholder="aws"
            />
          </Field>
          <Field label="Output collection window (seconds)">
            <Input
              type="number"
              min="60"
              value={graceSeconds}
              onChange={(event) => setGraceSeconds(event.target.value)}
              disabled={!modalEnabled}
            />
          </Field>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-medium">SSH compute</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Uses strict host-key checking and the server's SSH agent or a private-key path. Key
              contents are never stored in OmniSci configuration.
            </p>
          </div>
          <Switch
            aria-label="Enable SSH compute"
            checked={sshEnabled}
            disabled={!ssh?.registered || !ssh.dependencyAvailable}
            onCheckedChange={(checked) => {
              setSshEnabled(checked);
              if (!checked) {
                setSlurmEnabled(false);
                setQsubEnabled(false);
                if (["ssh", "slurm", "qsub"].includes(defaultProvider)) {
                  setDefaultProvider("local");
                }
              }
            }}
          />
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="SSH host">
            <Input
              value={sshHost}
              onChange={(event) => setSshHost(event.target.value)}
              disabled={!sshEnabled}
              placeholder="compute.example.edu"
            />
          </Field>
          <Field label="SSH user">
            <Input
              value={sshUser}
              onChange={(event) => setSshUser(event.target.value)}
              disabled={!sshEnabled}
              placeholder="researcher"
            />
          </Field>
          <Field label="SSH port">
            <Input
              type="number"
              min="1"
              max="65535"
              value={sshPort}
              onChange={(event) => setSshPort(event.target.value)}
              disabled={!sshEnabled}
            />
          </Field>
          <Field label="Private key path" hint="Optional when using ssh-agent">
            <Input
              value={identityFile}
              onChange={(event) => setIdentityFile(event.target.value)}
              disabled={!sshEnabled}
              placeholder="~/.ssh/id_ed25519"
              className="font-mono text-xs"
            />
          </Field>
          <Field label="Known hosts file">
            <Input
              value={knownHostsFile}
              onChange={(event) => setKnownHostsFile(event.target.value)}
              disabled={!sshEnabled}
              className="font-mono text-xs"
            />
          </Field>
          <Field label="Remote work directory">
            <Input
              value={remoteRoot}
              onChange={(event) => setRemoteRoot(event.target.value)}
              disabled={!sshEnabled}
              className="font-mono text-xs"
            />
          </Field>
          <Field label="SSH maximum runtime (minutes)">
            <Input
              type="number"
              min="1"
              value={sshMaxRuntime}
              onChange={(event) => setSshMaxRuntime(event.target.value)}
              disabled={!sshEnabled}
            />
          </Field>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border px-3 py-3 sm:self-end">
            <div>
              <p className="text-sm font-medium">Clean up remote files</p>
              <p className="text-xs text-muted-foreground">
                Remove the isolated run directory after collection.
              </p>
            </div>
            <Switch
              aria-label="Clean up SSH run files"
              checked={cleanupRemote}
              onCheckedChange={setCleanupRemote}
              disabled={!sshEnabled}
            />
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <div className="rounded-xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-medium">Slurm scheduler</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Reuses the SSH connection above. OmniSci stages the working tree, submits with
                sbatch, then tracks logs, cancellation, and outputs by job ID.
              </p>
            </div>
            <Switch
              aria-label="Enable Slurm compute"
              checked={slurmEnabled}
              disabled={!sshEnabled || !slurm?.registered || !slurm.dependencyAvailable}
              onCheckedChange={(checked) => {
                setSlurmEnabled(checked);
                if (!checked && defaultProvider === "slurm") setDefaultProvider("local");
              }}
            />
          </div>
          {!sshEnabled && (
            <p className="mt-3 text-xs text-muted-foreground">
              Enable and configure the SSH connection before adding a scheduler.
            </p>
          )}
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <Field label="Partition" hint="Optional">
              <Input
                value={slurmPartition}
                onChange={(event) => setSlurmPartition(event.target.value)}
                disabled={!slurmEnabled}
                placeholder="gpu"
              />
            </Field>
            <Field label="Account" hint="Optional">
              <Input
                value={slurmAccount}
                onChange={(event) => setSlurmAccount(event.target.value)}
                disabled={!slurmEnabled}
                placeholder="lab-allocation"
              />
            </Field>
            <Field label="QoS" hint="Optional">
              <Input
                value={slurmQos}
                onChange={(event) => setSlurmQos(event.target.value)}
                disabled={!slurmEnabled}
              />
            </Field>
            <Field label="Constraint" hint="Optional">
              <Input
                value={slurmConstraint}
                onChange={(event) => setSlurmConstraint(event.target.value)}
                disabled={!slurmEnabled}
                placeholder="a100"
              />
            </Field>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-medium">qsub scheduler</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Select PBS/Torque or Grid Engine semantics while using the same SSH connection and
                output collection path.
              </p>
            </div>
            <Switch
              aria-label="Enable qsub compute"
              checked={qsubEnabled}
              disabled={!sshEnabled || !qsub?.registered || !qsub.dependencyAvailable}
              onCheckedChange={(checked) => {
                setQsubEnabled(checked);
                if (!checked && defaultProvider === "qsub") setDefaultProvider("local");
              }}
            />
          </div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <Field label="qsub dialect">
              <Select
                value={qsubDialect}
                onValueChange={(value) => {
                  setQsubDialect(value);
                  setGpuResource(value === "pbs" ? "ngpus" : "gpu");
                }}
                disabled={!qsubEnabled}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pbs">PBS / Torque</SelectItem>
                  <SelectItem value="sge">Grid Engine</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="Queue" hint="Optional">
              <Input
                value={qsubQueue}
                onChange={(event) => setQsubQueue(event.target.value)}
                disabled={!qsubEnabled}
                placeholder={qsubDialect === "pbs" ? "workq" : "long.q"}
              />
            </Field>
            <Field label={qsubDialect === "pbs" ? "Account" : "Project"} hint="Optional">
              <Input
                value={qsubAccount}
                onChange={(event) => setQsubAccount(event.target.value)}
                disabled={!qsubEnabled}
              />
            </Field>
            <Field label="GPU resource name">
              <Input
                value={gpuResource}
                onChange={(event) => setGpuResource(event.target.value)}
                disabled={!qsubEnabled}
                placeholder={qsubDialect === "pbs" ? "ngpus" : "gpu"}
              />
            </Field>
            {qsubDialect === "sge" && (
              <Field label="Parallel environment">
                <Input
                  value={parallelEnvironment}
                  onChange={(event) => setParallelEnvironment(event.target.value)}
                  disabled={!qsubEnabled}
                  placeholder="smp"
                />
              </Field>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-3">
        <SaveState pending={pending} success={success} error={error} />
        <Button
          onClick={save}
          disabled={
            pending ||
            (modalEnabled && !modalValid) ||
            (sshEnabled && !sshValid) ||
            ((slurmEnabled || qsubEnabled) && !sshEnabled)
          }
        >
          Save compute configuration
        </Button>
      </div>

      <AdditionalProviders
        providers={status.compute}
        known={new Set(["local", "modal", "ssh", "slurm", "qsub"])}
      />
    </div>
  );
}

export function StorageSettings() {
  const query = useScienceInfrastructure();
  const mutation = useUpdateScienceInfrastructure();

  if (query.isLoading) return <LoadingInfrastructure />;
  if (query.error) return <InfrastructureError error={query.error} />;
  if (!query.data) return null;

  return (
    <StorageForm
      key={JSON.stringify(query.data.storageConfig)}
      status={query.data}
      onSave={(storageConfig) => mutation.mutate({ storageConfig })}
      pending={mutation.isPending}
      success={mutation.isSuccess}
      error={mutation.error}
    />
  );
}

function StorageForm({
  status,
  onSave,
  pending,
  success,
  error,
}: {
  status: ScienceInfrastructureStatus;
  onSave: (config: ScienceStorageConfig) => void;
  pending: boolean;
  success: boolean;
  error: Error | null;
}) {
  const local = providerById(status.storage, "local");
  const s3 = providerById(status.storage, "s3");
  const s3Config = status.storageConfig.providers.s3 ?? {};
  const [s3Enabled, setS3Enabled] = useState(s3?.configured ?? false);
  const [defaultProvider, setDefaultProvider] = useState(status.storageConfig.defaultProvider);
  const [roots, setRoots] = useState(status.storageConfig.allowedRoots.join("\n"));
  const [endpoint, setEndpoint] = useState(stringValue(s3Config, "endpoint_url"));
  const [region, setRegion] = useState(stringValue(s3Config, "region_name"));
  const [buckets, setBuckets] = useState(
    Array.isArray(s3Config.allowed_buckets) ? s3Config.allowed_buckets.join("\n") : "",
  );
  const [prefixes, setPrefixes] = useState(
    Array.isArray(s3Config.allowed_prefixes) ? s3Config.allowed_prefixes.join("\n") : "",
  );
  const [allowWrite, setAllowWrite] = useState(s3Config.allow_write === true);
  const bucketList = splitList(buckets);

  const save = () => {
    const providers = { ...status.storageConfig.providers };
    if (s3Enabled) {
      providers.s3 = {
        ...s3Config,
        allowed_buckets: bucketList,
        allowed_prefixes: splitList(prefixes),
        allow_write: allowWrite,
        ...(endpoint.trim() ? { endpoint_url: endpoint.trim() } : {}),
        ...(region.trim() ? { region_name: region.trim() } : {}),
      };
      if (!endpoint.trim()) delete providers.s3.endpoint_url;
      if (!region.trim()) delete providers.s3.region_name;
    } else {
      delete providers.s3;
    }
    onSave({
      defaultProvider: s3Enabled ? defaultProvider : "local",
      allowedRoots: splitList(roots),
      providers,
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <Alert>
        <HardDriveIcon />
        <AlertTitle>App-level storage resources</AlertTitle>
        <AlertDescription>
          Storage connectors and defaults apply across OmniSci and cannot be overridden by a
          project. Project storage policy and per-run approvals still bound access. Configuration:{" "}
          <span className="font-mono text-xs">{status.configPath}</span>
        </AlertDescription>
      </Alert>
      <div className="grid gap-3 lg:grid-cols-2">
        <ProviderCard
          title="Local filesystem"
          description="Universal local storage with each run confined to its active project and explicit roots."
          icon={<HardDriveIcon className="size-4" />}
          provider={local}
        />
        <ProviderCard
          title="S3-compatible storage"
          description="AWS S3, R2, MinIO, or institutional object stores behind allowlists."
          icon={<CloudIcon className="size-4" />}
          provider={s3}
        >
          {!s3?.dependencyAvailable && (
            <p className="mt-3 rounded-lg bg-muted/50 px-3 py-2 font-mono text-xs text-muted-foreground">
              uv sync --extra s3
            </p>
          )}
        </ProviderCard>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div>
          <h2 className="text-sm font-medium">Local storage boundary</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            These roots apply globally and resolve relative to each run's active project. Leave
            empty to allow only that project directory.
          </p>
        </div>
        <Field label="Allowed roots" hint="One path per line">
          <Textarea
            value={roots}
            onChange={(event) => setRoots(event.target.value)}
            placeholder={"data\nresults\nfigures"}
            className="mt-3 min-h-24 font-mono text-xs"
          />
        </Field>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-medium">Object storage</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Credentials come from the server's AWS credential chain. OmniSci stores allowlists and
              endpoint metadata only.
            </p>
          </div>
          <Switch
            aria-label="Enable S3 storage"
            checked={s3Enabled}
            disabled={!s3?.registered || !s3.dependencyAvailable}
            onCheckedChange={(checked) => {
              setS3Enabled(checked);
              if (!checked) setDefaultProvider("local");
            }}
          />
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="Default storage provider">
            <Select value={defaultProvider} onValueChange={setDefaultProvider}>
              <SelectTrigger className="w-full" disabled={!s3Enabled}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">Local filesystem</SelectItem>
                <SelectItem value="s3">S3-compatible storage</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Region" hint="Optional for custom endpoints">
            <Input
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              disabled={!s3Enabled}
              placeholder="eu-west-2"
            />
          </Field>
          <Field label="Endpoint URL" hint="Optional">
            <Input
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
              disabled={!s3Enabled}
              placeholder="https://s3.example.edu"
              className="font-mono text-xs"
            />
          </Field>
          <Field label="Allowed buckets" hint="Required; one per line">
            <Textarea
              value={buckets}
              onChange={(event) => setBuckets(event.target.value)}
              disabled={!s3Enabled}
              placeholder="research-data"
              className="min-h-20 font-mono text-xs"
            />
          </Field>
          <Field label="Allowed bucket prefixes" hint="bucket/prefix, one per line">
            <Textarea
              value={prefixes}
              onChange={(event) => setPrefixes(event.target.value)}
              disabled={!s3Enabled}
              placeholder="research-data/project-a/"
              className="min-h-20 font-mono text-xs"
            />
          </Field>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border px-3 py-3 sm:self-end">
            <div>
              <p className="text-sm font-medium">Allow writes</p>
              <p className="text-xs text-muted-foreground">Read-only is the safe default.</p>
            </div>
            <Switch
              aria-label="Allow S3 writes"
              checked={allowWrite}
              onCheckedChange={setAllowWrite}
              disabled={!s3Enabled}
            />
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-3 border-t border-border pt-4">
          <SaveState pending={pending} success={success} error={error} />
          <Button onClick={save} disabled={pending || (s3Enabled && bucketList.length === 0)}>
            Save storage configuration
          </Button>
        </div>
      </div>

      <AdditionalProviders providers={status.storage} known={new Set(["local", "s3"])} />
    </div>
  );
}

export function ToolsSettings() {
  const query = useScienceTools();

  if (query.isLoading) return <LoadingInfrastructure />;
  if (query.error) return <InfrastructureError error={query.error} />;
  if (!query.data) return null;

  const doctor = query.data;
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <SummaryCard
          icon={<CpuIcon className="size-4" />}
          label="Compute"
          value={doctor.computeProviders.join(", ")}
        />
        <SummaryCard
          icon={<HardDriveIcon className="size-4" />}
          label="Storage schemes"
          value={doctor.storageSchemes.join(", ")}
        />
        <SummaryCard
          icon={<LockKeyholeIcon className="size-4" />}
          label="Installed CLIs"
          value={`${doctor.tools.filter((tool) => tool.installed).length} of ${doctor.tools.length}`}
        />
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-medium">Application toolchain</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Detected once on the OmniSci application host and available to every project. Remote
            execution hosts report their own readiness when a session starts.
          </p>
        </div>
        <div className="divide-y divide-border">
          {doctor.tools.map((tool) => (
            <div key={tool.id} className="flex items-center gap-3 px-4 py-3">
              <span className="flex size-8 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <TerminalIcon className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-mono text-xs font-medium">{tool.command}</p>
                </div>
                <p className="truncate text-xs text-muted-foreground">{tool.description}</p>
                {tool.path && (
                  <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                    {tool.path}
                  </p>
                )}
              </div>
              <Badge variant={tool.available ? "secondary" : "outline"}>
                {tool.available ? "Available" : "Not installed"}
              </Badge>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        {icon}
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="mt-2 truncate font-mono text-sm">{value || "None"}</p>
    </div>
  );
}

function AdditionalProviders({
  providers,
  known,
}: {
  providers: ScienceProviderStatus[];
  known: Set<string>;
}) {
  const additional = providers.filter((provider) => !known.has(provider.id));
  if (additional.length === 0) return null;
  return (
    <div>
      <h2 className="mb-2 text-sm font-medium">Additional installed providers</h2>
      <div className="grid gap-2 sm:grid-cols-2">
        {additional.map((provider) => (
          <ProviderCard
            key={provider.id}
            title={provider.id}
            description="Discovered through the OmniSci provider interface. Configure this provider in app infrastructure."
            icon={<CpuIcon className="size-4" />}
            provider={provider}
          />
        ))}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="flex items-center justify-between gap-2 font-medium">
        {label}
        {hint && <span className="text-xs font-normal text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
