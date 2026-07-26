# Running jobs on an HPC cluster

OmniSci submits work to Slurm and Grid Engine / PBS clusters over SSH. This page
covers what you need before it will work, how to configure a connector, and what
the failure modes actually look like.

## How OmniSci connects

**OmniSci drives the real `ssh` binary and reads your `~/.ssh/config`.** It does
not reimplement SSH. That is deliberate, and it is the most useful thing to know
here: anything you can already express in your SSH config — jump hosts, per-host
keys, agent forwarding, connection multiplexing — works unchanged.

The practical consequence: **the `Host` field of a connector may be an
`~/.ssh/config` alias**, not just a hostname. For most cluster users the alias is
the right answer, because that is where the jump host and identity live.

Two constraints follow from how it connects:

- **Non-interactive only.** OmniSci passes `BatchMode=yes`, so it can never
  answer a password or MFA prompt. A background poller must not block on one.
  See *Clusters that require a password or MFA* below.
- **Strict host-key checking.** `StrictHostKeyChecking=yes` against a
  `known_hosts` file that must already exist. Connect once by hand so the host
  key is recorded.

## Before you configure anything

Four things must be true. The **Test connection** button checks all of them and
names which one failed.

1. `ssh <host>` works from a terminal, non-interactively, with a key.
2. The host key is in your `known_hosts`.
3. The host you point at is a **submit node** — one where `qsub` or `sbatch`
   actually exists. Cluster gateways are frequently pure jump hosts with no
   scheduler on them.
4. `remote_root` is on a **shared filesystem** (see below).

## Configuring a connector

Settings → Compute → the connector you want.

| Field | Notes |
| --- | --- |
| `host` | Hostname **or an `~/.ssh/config` alias**. |
| `user` | Remote username. May differ from your local one. |
| `identity_file` | Optional. Omit it if your SSH config already selects the key. |
| `known_hosts_file` | Defaults to `~/.ssh/known_hosts`; must exist. |
| `remote_root` | Where jobs are staged. **Must be shared across compute nodes.** |
| `max_runtime_minutes` | Upper bound OmniSci enforces before the scheduler does. |
| `dialect` (qsub only) | `sge` or `pbs`. Defaults to `pbs`. |
| `queue`, `account`, `parallel_environment`, `gpu_resource` | Site-specific; optional. |

### `remote_root` must be on a shared filesystem

This is the most common way to get a job that runs and produces nothing.

The default is `/tmp/omnisci`, which is correct for a single SSH host but
**wrong for a cluster**: your job lands on a compute node whose `/tmp` is not the
login node's. The working directory will be missing, and any outputs it writes
will be invisible when OmniSci collects them.

Use your home or scratch space:

```yaml
remote_root: /home/<user>/Scratch/omnisci    # or /scratch/<user>/omnisci
```

### Direct connection

For a cluster reachable directly — for example from inside the institution's VPN:

```yaml
host: cluster.example.ac.uk
user: <remote-user>
identity_file: ~/.ssh/id_ed25519
remote_root: /home/<remote-user>/Scratch/omnisci
dialect: sge
```

### Through a jump host

Put the topology in `~/.ssh/config`, where it belongs:

```sshconfig
Host cluster-login
    HostName login.cluster.example.ac.uk
    User <remote-user>
    ProxyJump gateway.example.ac.uk
    IdentityFile ~/.ssh/id_ed25519
    ControlMaster auto
    ControlPath ~/.ssh/sockets/%r@%h-%p
    ControlPersist 1h
```

then point the connector at the alias:

```yaml
host: cluster-login
user: <remote-user>
remote_root: /home/<remote-user>/Scratch/omnisci
```

There is no `ProxyJump` connector field, and there does not need to be — SSH
already has one.

## Clusters that require a password or MFA

OmniSci cannot type a password. If your login node requires one, use SSH
connection multiplexing: you authenticate once, and OmniSci reuses that session.

Add to the `Host` block:

```sshconfig
ControlMaster auto
ControlPath ~/.ssh/sockets/%r@%h-%p
ControlPersist 1h
```

Create the socket directory once (`mkdir -p ~/.ssh/sockets`), then open a session
whenever you start work:

```bash
ssh cluster-login
```

Leave it open, or let `ControlPersist` hold it. OmniSci's connections ride over
it and need no credentials. **When it expires, jobs stop reconciling** — the
health check reports `auth_failed` and tells you to open a session again.

This is a supported path, not a workaround. It is the only way to use an
MFA-protected cluster with a background job poller.

## Grid Engine vs PBS

The `qsub` provider speaks both, and they need different directives. `dialect`
defaults to **`pbs`**, so on a Grid Engine site you must set `sge` — otherwise
the wrong directives are emitted and **silently ignored**: the job queues but
never receives the resources it asked for.

You do not have to guess. **Test connection** detects which is present and tells
you if the setting disagrees. To check by hand: `qconf` means Grid Engine,
`pbsnodes` means PBS.

## Queue waits are normal

A job sitting in `qw` / `PD` for minutes, hours, or overnight is ordinary shared
HPC, not a fault. OmniSci reports it as `queued` because it *is* queued.

What is **not** normal is a job that can never be scheduled — for example one
submitted to a queue that only accepts interactive work. Those wait forever and
look identical from the outside. If a job has waited far longer than the cluster
warrants, ask the scheduler directly whether the request is satisfiable:

```bash
qalter -w v <job-id>        # Grid Engine / PBS
scontrol show job <job-id>  # Slurm
```

"found possible assignment" means the request is fine and you are just waiting.

## Troubleshooting

**Test connection** classifies failures. Each maps to one fix:

| Result | Means | Do this |
| --- | --- | --- |
| `auth_failed` | Key rejected, or the host wants a password/MFA | Open a session yourself and enable `ControlMaster` (above) |
| `unreachable` | DNS, VPN, or the host is down | Check the VPN; if `host` is an alias, check the `Host` block |
| `misconfigured` — host key | Not in `known_hosts` | `ssh <host>` once by hand |
| `misconfigured` — dialect | Setting disagrees with the cluster | Set the detected dialect |
| `missing_dependency` | No `qsub`/`sbatch` on that host | Point at the submit node, not the gateway |

Two failures the health check cannot see, both listed above because they are
common:

- **Outputs missing after a successful run** — `remote_root` is probably
  node-local. Move it to shared storage.
- **A job queued far longer than the cluster warrants** — it may be
  unschedulable as configured. Use `qalter -w v`.

## Current limitations

Stated plainly, because HPC support is under active development:

- Slurm and PBS have not yet been validated end to end against a live cluster.
  Grid Engine has been, apart from the job-execution path. See
  [`OMNISCI_AUDIT.md`](OMNISCI_AUDIT.md) section 10.
- The whole working tree is re-staged on every submission; there is no
  incremental transfer and no way yet to run in place against data already on
  the cluster.
- Staging refuses to follow symlinks and does not exclude `.venv` or
  `node_modules`.
- Per-job cost is not recorded. Institutional schedulers do not report it.
