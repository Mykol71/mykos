# API usage

## Base URLs and authentication

Use `https://YOUR_HOST/mykos/api/v2` publicly. Uvicorn listens on
`http://127.0.0.1:8000` internally and its route paths start `/api/v2`; Apache strips
the external `/mykos` prefix. The supported integration interface is API v2.

Create a read key using `sudo /usr/local/sbin/mykos-api-keys create my-monitor read`. Set it in your
client's secret store. For an interactive shell, avoid placing the key literally
in command history:

```sh
read -rs -p 'API key: ' MYKOS_KEY; echo
export MYKOS_KEY
BASE=https://mykos.example.com/mykos/api/v2
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" "$BASE/overview"
unset MYKOS_KEY
```

The examples use an environment variable for readability; take care with process
visibility on shared systems. TLS is required for credentials. An invalid supplied
key returns 401 even if a browser session is otherwise valid. API keys are not
accepted by legacy browser endpoints or the browser-session terminal WebSocket.
An admin key can use the separate API terminal at `/api/v2/terminal/{shop}`.

API v2 operations require an `X-MYKOS-API-Key` header with a valid `read` or `admin`
key. Browser cookies do not authorize API v2 requests. No login, cookie jar, or
Origin header is required for API clients. Dashboard session routes retain their
browser authentication and Origin checks.

## Interactive reference

Open `/mykos/api/v2/docs` and enter your key in Swagger's **Authorize** control.
The documentation and OpenAPI schema are public so the reference can load without
a browser login or cookies; all operational endpoints still require a key.
Swagger sends `X-MYKOS-API-Key` on Execute requests, using `/mykos` as the public proxy
prefix. Swagger assets load from unpkg.com and require browser network access to
that host.

```sh
curl --fail-with-body -sS "$BASE/openapi.json"
```

A generated offline schema is also available in [openapi.json](openapi.json).
Regenerate it when routes change; the running service remains authoritative.

## Endpoint inventory

All paths below are relative to `/mykos/api/v2`. `shop` is exactly eight ASCII digits,
including leading zeros. The read column includes both `read` and `admin` keys.

| Method and path | Permission | Result / notes |
|---|---|---|
| `GET /overview` | Read | Host, counts, snapshot ages/freshness, joined nodes |
| `GET /host` | Read | Hostname, OS, kernel, uptime, backup storage, live `metrics` |
| `GET /host/metrics` | Read | CPU, logical CPU count, memory, swap, load averages, root disk, uptime and `graph_totals` (`containers`, `vpns`) counted from installed POS metadata files; null if unavailable/stale |
| `GET /host/status` | Read | Host row data: `host` contains hostname, status, OS/version/kernel, internal IP and collected VPN status; memory and uptime are top-level fields |
| `GET /host/diagnostics/{action}` | Read | Host `logs` (last 200 journal entries), `top` (processes), or `ipsec` status; fixed commands, no container execution |
| `POST /host/reboot` | Admin | JSON `hostname` must exactly match the server hostname; schedules a reboot in five seconds; interrupts all containers/users |
| `WS /terminal/host` | Admin | Interactive host root shell, or host VPN setup with `?action=add-vpn`; host `terminal_enabled` setting must be enabled; API key in handshake header |
| `GET /host/os` | Read | Hostname, OS, kernel |
| `GET /host/kernel` | Read | Kernel string |
| `GET /host/storage` | Read | Backup storage free/used/total bytes and readable free value |
| `GET /nodes` | Read | `count`, `fresh`, `nodes`; optional filters below |
| `GET /nodes/{shop}` | Read | Full joined node |
| `GET /nodes/{shop}/status` | Read | Status, running flag, VPN state, start epoch/time, image, OS type/version |
| `GET /nodes/{shop}/network` | Read | `eth1_ipv4`, collected address list |
| `GET /nodes/{shop}/resources` | Read | CPU/memory display values from Podman |
| `GET /nodes/{shop}/vpn` | Read | VPN status, Libreswan counters/connections when available |
| `GET /nodes/{shop}/details` | Read | Container inspection details |
| `GET /nodes/{shop}/ipsec` | Read | Live `ipsec status` output |
| `GET /nodes/{shop}/logs` | Read | Last 200 Podman log lines; bounded output |
| `GET /nodes/{shop}/processes` | Read | Live `podman top` output |
| `GET /nodes/{shop}/checkit` | Admin | Runs `/usr/bin/checkit` inside the selected container |
| `GET /containers` | Read | Managed rootful container list |
| `GET /images` | Read | OS choices from running supported template containers; returns `os` and the local source `image` |
| `GET /notes/{pane}` | mgreen session | Private plain-text pane notes (mgreen session only), revision, and last-saved timestamp |
| `PUT /notes/{pane}` | mgreen session | Save notes with JSON `text` (up to 20,000 characters) and current `revision`; stale revisions return 409 |
| `GET /personal-notes` | mgreen session | Read personal notes; separate from change requests and retained through updates |
| `PUT /personal-notes` | mgreen session | Save personal notes with `text` and `revision`; stale revisions return 409 |
| `GET /actions` | Read | Supported administrative operations and permissions |
| `GET /actions/stage/{shop}/check` | Admin | Container existence, active job, RTI backup presence, host configuration readiness |
| `GET /jobs/{shop}` | Admin | Staging state and bounded output; `idle` if no recorded job |
| `POST /actions/stage` | Admin | Queue staging; JSON `shop`, `os` from `/images`, and optional `restore` boolean |
| `POST /nodes/{shop}/stop` | Admin | Stop the unique selected managed container |
| `GET /backups` | Read | Backup accounts, POS/OS, disk use, subscriber state and storage; optional `refresh=true` |
| `GET /backups/{code}` | Read | One backup account; optional `refresh=true` |
| `POST /backups` | Admin | Account creation/deletion, SSH key changes, subscriber updates and key retrieval |
| `GET /backups/{code}/keys` | Admin | Backed-up public and private RSA keys; response is not cached |
| `GET /repos` | Read | Repositories, disk use, modification times, Apache and sync state; optional `refresh=true` |
| `GET /repos/{name}` | Read | One repository; optional `refresh=true` |
| `POST /repos` | Admin | Delete, enable, disable or start repository sync |
| `WS /terminal/{shop}` | Admin | Interactive terminal; key in WebSocket handshake header, host terminal setting must be enabled |
| `GET /openapi.json`, `GET /docs` | Public | Machine/browser API reference; use Authorize for operations |

`GET /nodes` filters: `status=online|offline|unknown`,
`vpn=up|down|unknown|not_configured`, and `running=true|false`.
`include` is `all` or a comma-separated selection of
`observation,details,extended,network,resources,vpn`; base identity/status fields
remain present. Status/vpn filters are exact string comparisons. Unknown filter
values simply match nothing. Missing collected values can be `null`, empty objects,
`Unknown`, or `unknown` depending on the field.

Container `details` include `image` (the image reference), `os_type` (distribution
name), and `os_version` (distribution version). These fields are also available on
joined nodes and `/nodes/{shop}/status`. The details collector reads the
container's `/etc/os-release`, falling back to `/usr/lib/os-release`, including for
stopped containers. Missing metadata displays as `Unknown` in the dashboard.

### Read examples and response shapes

```sh
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" \
  "$BASE/nodes?status=online&include=network,resources"
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" \
  "$BASE/nodes/01234567/network"
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" \
  "$BASE/nodes/01234567/logs"
```

Illustrative network result (addresses and node are examples, not production data):

```json
{"shopcode":"01234567","eth1_ipv4":"192.0.2.10","addresses":[{"network":"podman","address":"10.88.0.2"}]}
```

Live diagnostic responses have this shape:

```json
{"shop":"01234567","container":"01234567.host.example","output":"diagnostic text"}
```

`/nodes` returns `{"count":0,"fresh":{"status":false,"details":false,"extended":false},"nodes":[]}`
on a fresh installation without collected nodes. A 200 response alone does not mean
collectors are fresh. Inspect freshness and timestamps in `/overview`. Podman resource
values are display strings, not uniformly normalized numeric metrics. Helper failures
can produce `Unknown` backup storage in `/host` while the rest of the response works.

### Administrative examples

Use an admin-scoped key and a staging shop approved for these operations. Staging
creates a privileged container. For native Linux images, `restore: true` mounts
available `/backups/tfrsync-ID` trees `/usr2`, `/d`, `/etc`, `/home`, and
`/usr/java` directly
read/write; guest writes change the original backup. The application adapter need
not be enabled. VM images do not support this restore option.

Before the new instance starts, its private account files are reconciled with the
backup IDs. Conflicting image account ownership and POSIX ACLs are adjusted only
inside that instance; backup ownership and saved account files stay unchanged.
The image root password is retained, missing customer password hashes produce
locked accounts, and the old `tfrsync-ID` login account is omitted. The entire backup home tree
is mounted, including any old backup-client home; its source is never deleted.
Private per-instance bind mounts retain reconciled account files, hostname/hosts,
machine ID, image fstab/crypttab and Podman interface ownership settings inside
the complete `/etc` mount. Other saved customer configuration and service
definitions are visible directly; files absent from the backup cannot be restored. The host
backup account is untouched; use the existing **Backups** text menu to configure
backup accounts afterward. Existing instances are not restored again on restart.

Root configuration can explicitly map receiver-owned files to customer identities:
`"backup_identity_overrides": {"01234567": {"users": {"appuser": 1011},
"groups": {"appgroup": 1012}}}`. Only use IDs confirmed for that backup; mappings
never change the backup files. An optional `backup_root` changes the host search
root from `/backups`.
Stopping affects the selected running POS instance.

```sh
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" \
  "$BASE/actions/stage/01234567/check"
curl --fail-with-body -sS -X POST \
  -H "X-MYKOS-API-Key: $MYKOS_KEY" -H 'Content-Type: application/json' \
  --data '{"shop":"01234567","os":"rh9","restore":false}' "$BASE/actions/stage"
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" "$BASE/jobs/01234567"
curl --fail-with-body -sS -X POST -H "X-MYKOS-API-Key: $MYKOS_KEY" \
  "$BASE/nodes/01234567/stop"
```

A staging submission returns HTTP 200 with a queued job, for example:

```json
{"shop":"01234567","state":"queued","restore":false,"output":"","updated_at":1792690000}
```

Poll until `state` is `done` or `failed`; `queued`/`running` are nonterminal. The
submission is not proof of completed staging. A lost/failed worker produces failed
status when polled. Jobs are not automatically retried. Repeated start requests
while a worker is active or a container exists are rejected. Stopping a successfully
staged container keeps it present; removal/restart is an explicit operator task.

## Host metrics, Backup, Repos and terminal integrations

API v2 now exposes the same data and operations as all dashboard panes. Use
`X-MYKOS-API-Key` for each request; neither browser login nor Origin is required.
`/host/metrics` returns the same raw readings used by the Host gauges and footer.
CPU/memory/disk percentages are 0–100. Load averages are the raw 1/5/15-minute
values; the gauge normalizes one-minute load by `cpu_count`. Uptime is seconds.
When Apache uses a Certbot certificate, `host.certificate_domain` identifies
the certified dashboard domain used for the Host pane heading. `host.hostname`
continues to identify the actual server. Certificate metadata is collected on
the host and does not add guest calls.
Swap remains available in the API even though its gauge is hidden in the UI.
Unavailable counters are `null`, rather than a fabricated zero.

```sh
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" "$BASE/host/metrics"
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" "$BASE/backups"
curl --fail-with-body -sS -H "X-MYKOS-API-Key: $MYKOS_KEY" "$BASE/repos?refresh=true"
```

Backup account responses include `updated_at`, the Unix modification timestamp of the newest backed-up RTI/Daisy `tfrsync-summary.log`, or `null` when unavailable. This is the dashboard’s Last updated value, not a guarantee of a successful backup.

Backup POST bodies share the dashboard's schema:

| `action` | Fields in addition to `action` |
|---|---|
| `create` | `code`, SSH public `key`, optional boolean `subscriber` |
| `key` | `code`, replacement public `key` |
| `subscriber` | `code`, boolean `subscriber` |
| `delete` | `code`, boolean `remove_data` (default false preserves data) |
| `keys` | `code`; returns backed-up public/private client keys |

The equivalent key read is `GET /backups/{code}/keys`. Key material is restricted
to admin keys and uses `Cache-Control: no-store`. It is never included in listings.
The account's currently authorized public key and the backed-up client's own RSA
keys are different: `key` updates authorized access; `keys` reads the client backup.

Repository POST bodies use `action: delete|enable|disable|sync` and `name`.
Deletion requires an existing repository name. Other actions use `name: "all"`
(the default). Actions use the same helper validation and locks as the dashboard,
and invalidate the shared listing cache. Enable/disable operates on all of Apache;
disable can interrupt the API itself. Sync queues the same background systemd job;
read `/repos` to monitor its state.

The terminal URL is `wss://YOUR_HOST/mykos/api/v2/terminal/01234567`. Supply an admin
key in the `X-MYKOS-API-Key` **handshake header**, never the URL. Standard browser
WebSocket objects cannot set that header; use a programmatic WebSocket client.
The existing browser terminal remains on its separate session-authenticated route.
Both use the same PTY helper, honor `terminal_enabled`, and reject invalid codes
before opening the helper. Missing/invalid/read-scoped keys close with code 1008;
an unavailable/disabled helper closes with 1011. Authorization is checked at the
handshake; revoking a key does not terminate an already connected terminal.
WebSocket routes are documented here and in `/actions`, since OpenAPI does not
model the WebSocket transport.

For deployments using the explicit Apache WebSocket proxy rules, add these **before**
the general `/mykos/` HTTP proxy rule in the existing HTTPS VirtualHost:

```apache
ProxyPass /mykos/api/v2/terminal/ ws://127.0.0.1:8000/api/v2/terminal/
ProxyPassReverse /mykos/api/v2/terminal/ ws://127.0.0.1:8000/api/v2/terminal/
```

Validate with `apachectl configtest` before reloading Apache. The installer updates
the example file but preserves the site's actual Apache configuration.

## Legacy routes

The dashboard uses browser-session routes: `POST /api/login` with JSON
`username/password`, `POST /api/logout`, `GET /api/nodes`, `/api/nodes/{shop}/details`,
`/api/web/current-user`, `/api/web/host-version`, `/api/web/host-uptime`,
`/api/web/extended`, `/api/web/host-metrics`, `/api/terminal/permission`, `/api/admin/containers`, and
`/api/admin/containers/{shop}/{logs|top|ipsec|checkit}`. Checkit requires admin.
The Host pane reads `/api/web/host-metrics` every ten seconds for aggregate CPU
usage, memory and swap usage, 1/5/15-minute load averages, root filesystem usage,
and host uptime. CPU usage is sampled over 100 ms; memory usage excludes available
reclaimable memory. Unavailable counters return `null`.

Browser-only admin staging routes are `/api/web/stage/{shop}/{check|status}`,
`POST /api/web/stage`, and `POST /api/web/containers/{shop}/stop`.
The terminal WebSocket is `/api/terminal/{shop}` and requires an admin browser
session plus matching Origin. Prefix all these paths with `/mykos` externally.

Legacy `/api/monitor/summary`, `/api/monitor/nodes`, `/api/monitor/nodes/{shop}`,
`/api/monitor/extended`, and `/api/monitor/extended/{shop}` require
`Authorization: Bearer LEGACY_TOKEN`; API v2 keys are not interchangeable with it.
Do not build new integrations against these compatibility routes.

## Common errors

| Status | Meaning / next action |
|---|---|
| 400 | Invalid eight-digit shopcode, invalid include fields, or bad route-level input |
| 401 | Missing/expired browser session or missing, invalid, or revoked API key |
| 403 | Admin privilege required, or missing/mismatched browser Origin |
| 404 | Shop absent from collected inventory, or details unavailable |
| 422 | JSON/query validation failed; inspect FastAPI's `detail` array |
| 500 | Helper rejected operation or a host command failed; inspect `detail` and unit journal |
| 503 | Helper socket unavailable/timeout/bad response, legacy token unavailable, or snapshot unavailable on a legacy route |

The current helper bridge maps helper-side errors (including a container conflict
or unconfigured image) to 500, not 409. Do not automatically retry a mutating request
after a timeout; inspect its job/container state first. The helper limits diagnostic
output and command duration; long operations belong to the staging job workflow.

## Dashboard Backup and Repos panes

The dashboard orders its panes as Host, Backups, Repos, Containers. Header section
links update the breadcrumb location. Container status includes the collected OS
type and version; missing or stale details show `OS unknown`.

These browser-session endpoints sit outside API v2:

| Method and path | Permission | Behavior |
|---|---|---|
| `GET /api/web/mykos/backups` | Browser user | Backup accounts, POS/OS, disk use, subscriber state and storage |
| `GET /api/web/mykos/repos` | Browser user | Repository directories, disk use, modification times, Apache and sync state |
| `POST /api/web/mykos/backups` | Browser admin + Origin | JSON `action`, eight-digit `code`, and action fields below |
| `POST /api/web/mykos/repos` | Browser admin + Origin | JSON `action` and optional `name` (default `all`) |

Backup actions: `create` (SSH public `key`, `subscriber`), `key` (replacement public
`key`), `subscriber` (boolean `subscriber`), `delete` (boolean `remove_data`), and
`keys` (read the backed-up client's public/private RSA keys). Account creation uses
`useradd`, the existing forced-command `back/bin/tfrsync`, and restricted SSH key
permissions. Delete can retain backup data while removing the account and SSH
access. Retained directories appear only when an OS backup account exists and
must be resolved by an operator before reusing that code. View/get keys opens a
private-key dialog and supports browser download; it does not copy keys into the
server's `/home/tfsupport/keys` directory. Closing the dialog or logging out clears
its displayed key material.

Repository storage may use a root-controlled symlink, such as
`/var/www/html -> /home/html`, when its target and parent directories are trusted.
Symlinks inside the repository root are excluded from listing and deletion.

Repo actions: `delete` requires an existing immediate directory `name`; `enable`,
`disable`, and `sync` use `name: "all"`. Enable/disable starts/stops **all of Apache**,
matching the CLI menu, and may interrupt this UI and other sites. Sync starts
`mykos-repo-sync.service` asynchronously using the toolkit's existing sync script;
it installs dependencies and runs privileged mirror containers as that script
specifies. Sync state is the systemd ActiveState (`activating` while the oneshot
runs, `inactive` after exit, `failed` after a unit failure). Delete is rejected
while sync is active. Service output is available via
`journalctl -u mykos-repo-sync.service`.

Lists refresh every minute and are cached for up to 60 seconds. The Refresh button
passes `refresh=true` to collect immediately. Disk usage collection is limited to
20 seconds per pane; unavailable size values display `Unknown`. No account, key,
subscriber, repository, or service changes occur on page load. These operations
use fixed privileged helper commands; interactive legacy menus are not executed by
HTTP requests. Deployment must include the new helper module and sync unit and
reload systemd before Sync can run.

The dashboard requires an OS selection when adding a container. The helper validates the selection against running supported OS templates and records the source image in the queued job. Existing API clients that omit `os` use the configured `stage_image` fallback. No image is pulled from a registry. Backup creation accepts the ID and SSH public key directly in the pane.

### Add a VPN from the container pane

For a running container with VPN status `not_configured`, administrators can use the blue plus in its VPN column. It opens the existing POS `add_vpn` prompts in the popup terminal. Enable terminal access in `/etc/mykos/config.json`. The prompts collect the remote name, public IP/hostname, IPv4 network (without `/24`), and a hidden PSK entry, then ask for confirmation and whether to push to Meraki. The entered PSK is written to its customer folder’s per-connection `.secrets` file with mode `0600`; no preconfigured `VPN_PSK` is required. The host runs the fixed `pos --add-vpn SHOP CONTAINER` entry point, not an arbitrary host shell.

API clients use `WS /api/v2/terminal/{shop}?action=add-vpn` with an admin API key in the handshake header. The browser uses the authenticated, Origin-checked `/api/terminal/{shop}?action=add-vpn` route. Standard root container terminals retain the default `action=shell`.

Host responses include `internal_ip`, the host source IPv4 address selected by the route to `8.8.8.8`, or `null` when unavailable. The dashboard footer shows this address; the header continues to show the hostname.

Supported OS template names are `rh7`, `rh8`, `rh9`, `rh10`, `win10`, `wns2025`, `wns2022`, `wns2019`, `wns2016`, `win11`, `win8e`, `win7u`, `winxp`, `winme`, `win2k`, `win98`, `mac15`, `mac14`, `mac13`, `mac12`. Only running templates appear in the dropdown and `/api/v2/images`; each selection uses that template’s local image.


## Persistent containers and live snapshots

The following key-authenticated v2 routes use the same backend jobs as the web buttons:

| Route (under `/mykos/api/v2`) | Scope | Result |
| --- | --- | --- |
| `POST /nodes/{shop}/start` | Admin | Start the existing persistent container and current disks |
| `POST /nodes/{shop}/stop` | Admin | Stop it while retaining writable storage, code and ports |
| `POST /nodes/{shop}/snapshot` | Admin | Capture live state; leave the source running |
| `GET /snapshots` | Read | List all saved snapshots, grouped by `code` in the UI |
| `GET /nodes/{shop}/snapshots` | Read | List one container's available snapshots |
| `POST /nodes/{shop}/snapshots/{identifier}/restore` | Admin | Replace the original container's disk/live state with the selection |

POST responses return a queued job. Poll `GET /jobs/{shop}` until `state` is
`done` or `failed`; the job's `output` contains the result JSON or failure detail.
The helper permits one job per code. Restore interrupts that container and replaces
current state; API callers must explicitly choose the original code and snapshot
identifier. There is no cross-container restore. API authentication remains
`X-MYKOS-API-Key` or Bearer key, without browser cookies or an Origin requirement.

Browser equivalents are `GET /api/web/snapshots`,
`GET /api/web/containers/{shop}/snapshots`, and POST routes under
`/api/web/containers/{shop}` ending in `/start`, `/stop`, `/snapshot`, or
`/snapshots/{identifier}/restore`. Browser writes require an admin session and the
configured Origin; list routes require a signed-in session. Private configuration,
guest credentials and mounted source paths are excluded from snapshot list payloads.

Snapshot notes use pane name `snapshots` through the existing notes endpoints.


`DELETE /mykos/api/v2/nodes/{shop}/snapshots/{identifier}` permanently removes
only that selected saved state (admin key required), through the same queued-job
protocol. It does not stop, reboot or delete the live container. Other saved states
remain. The browser equivalent is
`DELETE /mykos/api/web/containers/{shop}/snapshots/{identifier}`, requiring an admin
session and the configured Origin. Both restore and delete operate on the selected
snapshot ID; list responses include its original `os` and `created_at` epoch time.
The Snapshots table displays a full OS name and a Taken date/time for that selection.


## Scheduled commands (admin API keys only)

`PUT /mykos/api/v2/commands/{id}` queues one command. The local
`mykos-command-collector.timer` checks due work every 30 seconds. `run_at` is
an ISO 8601 date/time with timezone; omit it to run on the next check. A busy
target waits for its earlier job. Up to four different targets can run concurrently.
A command may start later when a target is busy or the host is offline.

```json
{
  "target": "01234567",
  "command": "Get-Date; Get-Service -Name Spooler",
  "run_at": "2026-10-25T14:30:00-05:00",
  "timeout_seconds": 300
}
```

Use an eight-digit container ID or `host`. Windows and Windows Server jobs
run Windows PowerShell **inside the guest**, through QEMU Guest Agent. They
never execute PowerShell text in the Linux Dockurr wrapper. Host and native
RedHat/Linux jobs use Bash as root. macOS and VM-based Ubuntu scheduled commands
are currently rejected. Native Linux requires `/usr/bin/timeout` in the guest.

Windows requires a working QEMU Guest Agent service with `guest-exec` enabled
and the MYKOS private QGA channel. New Windows images created by this release
include `QGA=/dev/shm/mykos-qga.sock`; install the signed QEMU Guest Agent from
the VirtIO Windows driver distribution inside Windows and start its service
before capturing the image. Older images/running instances are not automatically
restarted or changed: without the channel/agent the job fails with an explicit
prerequisite error. Adding a device to an older RAM capture requires a compatible
recapture; do not edit its launch topology and restore the old capture. No public
QGA port, guest password, SSH service, or WinRM listener is used.

Use a unique ID (1–64 ASCII letters/digits/underscores/hyphens, beginning with
an alphanumeric). Repeating an identical PUT returns the existing record without
rerunning it. A pending record may be rescheduled with a changed PUT; changing a
claimed/completed/cancelled record returns 409. Use a new ID to run again.

| Endpoint | Behavior |
| --- | --- |
| `GET /mykos/api/v2/commands` | Latest 100 records, excluding scripts/output |
| `GET /mykos/api/v2/commands/{id}` | Full record, state, timestamps, exit code and bounded combined output |
| `POST /mykos/api/v2/commands/{id}/cancel` | Cancel only a pending job; 409 once claimed |

All endpoints require `X-MYKOS-API-Key` with **admin** scope; browser cookies and
read keys cannot access them. The command limit is 16000 UTF-8 bytes and timeout
is 1–3600 seconds. Output retains up to 64 KiB and reports truncation. Results
and scripts are stored root-only under `/var/lib/mykos/commands`, retained across
updates. Treat admin keys as root-equivalent credentials.

States are `pending`, `starting`, `running`, `succeeded`, `failed`, `timed_out`,
`cancelled`, and `interrupted`. Dispatch is at most once: a worker lost during a
reboot or service failure is marked interrupted and is not automatically retried.
It may have executed some or all of the script; inspect the result before choosing
a new ID. Timeouts kill the host process group or invoke guest timeout/taskkill;
a disconnected Windows agent can prevent confirmation of termination. Detached
processes or services deliberately started by a command can outlive the job.

Transport references: [Dockur QGA configuration](https://github.com/dockur/windows/blob/master/docs/environment.md)
and [QEMU guest execution protocol](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html).

The authenticated `GET /api/v2/images` response includes `images` (startable OS choices)
and `builds` (active unfinished images with `os`, `name`, and `console_url`).
Builds expose the installation console but cannot be selected for starting a container
until capture completes. The browser endpoint `/api/web/stage-images` returns the same data.

## Delete a container and its private data

`DELETE /api/v2/nodes/{shop}` requires an **admin API key** and JSON
`{"confirmation":"01234567"}` matching the path ID exactly. The browser equivalent
is `DELETE /api/web/containers/{shop}`, with an administrator session and same-origin
request. The trash action appears beside start, stop and snapshot in every OS pane.
It asks you to type the ID. Stop still preserves the container and its data.

```bash
curl --fail-with-body -X DELETE "$BASE/nodes/01234567" \
  -H "X-MYKOS-API-Key: $MYKOS_API_KEY" -H 'Content-Type: application/json' \
  --data '{"confirmation":"01234567"}'
```

Deletion queues a per-ID worker; poll `GET /jobs/{shop}` for its result
(the existing staging job status endpoint). It pins the selected Podman container
identity, stops it if necessary, removes it without force-removing an image or
volume, and removes its MYKOS-managed VM disk directory, private VPN directory,
and per-ID lifecycle metadata. Writable native container layers are removed by
Podman. It refuses unsafe/symlink cleanup paths and storage also mounted by another
container. Unmanaged/external bind mounts, named volumes, rsync backups, OS images,
and intentional snapshots are retained. `retained_mounts` lists untouched mounts.

Snapshots are retained as separate archives; deletion does not automatically
recreate a container for restore. VM restore needs an existing compatible container
with the same ID. The current native Linux CRIU restore requires its **original**
container identity, so deleting that container prevents that restore method. The
web confirmation warns when such native memory snapshots are present.

## Explicit container-state purge

Administrator only: `GET /api/v2/system/purge` previews the host-wide reset and
returns the exact `confirmation` phrase, `PURGE hostname`. Send that exact phrase
as `{"confirmation":"PURGE hostname"}` to `POST /api/v2/system/purge` to queue
it. Poll `GET /api/v2/system/purge/status` after the dashboard restarts. These
routes require an admin API key. Browser equivalents live under
`/api/web/system/purge`; browser execution additionally checks the request origin.

This is irreversible: it removes **all rootful Podman containers and images**,
including non-MYKOS resources, pods, volumes, custom networks, build cache, VM
disks, startup captures, intentional snapshots, recovery disks, per-ID VPN data,
MYKOS build files, queued commands, runtime inventory, and pane notes. Active
MYKOS lifecycle/command workers and recognized kickstart builders are cancelled.
Rootless containers belonging to other Unix users are outside this scope.

MYKOS browser users/password hashes/roles, sessions and API key records remain.
Rsync backup accounts/data, synced repos, application files, settings, docs and
installer/RPM recovery packages remain. The operation refuses unsafe storage
paths, storage overlapping preserved data, and nested host mounts. It does not
remove application packages or run automatically during install/update.

Local text menu: POS **Purge**, or `sudo /usr/local/sbin/mykos-prune purge`.
Preview without changes: `sudo /usr/local/sbin/mykos-prune --dry-run`.
Status: `sudo /usr/local/sbin/mykos-prune status`.
The interactive command requires typing the exact host confirmation, waits for
the worker, and reports success/failure. Stop/start of MYKOS web/collector/socket
services is temporary; only infrastructure that was active is resumed. Guest
containers and cancelled jobs are never restarted by the purge.

## Authenticated HTTPS desktop consoles

`GET /console/{selection}/{path}` under API v2 serves verified image/container
console assets with an admin API key. WebSocket upgrades to
`/api/v2/console/{selection}/websockify` require the same admin key header and
are rechecked while connected. Human browser links use
`/mykos/console/{selection}/` with a current admin browser session; the WebSocket
also checks the configured public Origin. Selection is a supported VM OS name
for an image build, or an eight-digit container ID. Clients cannot supply a
backend host or port. Upstream requests never receive MYKOS credentials.

Image/desktop metadata `console_url` now names the authenticated public proxy
path. `direct_console_url` supplies the former LAN URL for diagnostics. Native
RDP/VNC `login_url` values retain their LAN/VPN semantics.

Host Add VPN (`WS /mykos/api/v2/terminal/host?action=add-vpn`, admin key required)
uses the same interactive workflow as container Add VPN: remote name, public
address, IPv4 network without `/24`, hidden PSK entry, confirmation, optional
Meraki push, and remote-peer instructions. Browser sessions use the equivalent
authenticated host terminal action. The fixed workflow writes new host connection
files and loads only the selected tunnel into host IPsec.

### Host-side monitoring and RAM allocation

Periodic OS identity, container state, resource statistics and native network
addresses now come from host Podman metadata and host iproute tools. Image OS
identity is not a report of changes made by an in-guest OS upgrade. Native VPN
results are sampled by the basic collector through the Libreswan control socket;
details/extended reuse the same sample, rejecting samples older than 60 seconds
or belonging to an earlier container start. VM wrappers are not queried for Linux
VPN state or a pipework interface. Unsupported control protocols report unknown.

`PUT /mykos/api/v2/nodes/{id}/memory/allocation` (admin API key) accepts
`{"target_mib":2048,"confirmation":"12345678"}` with confirmation matching the
path ID. It queues a cold restart and returns a systemd unit name. Existing disks,
OS images, snapshots and console/desktop ports are retained; the previous stopped
wrapper is retained for recovery, explicitly stopped under unless-stopped policy. Running VMs also receive a quiesced reflink disk recovery copy; mismatched live storage paths are rejected before stopping. Windows instances use manual installation mode to prevent unattended reinstallation. Check its
journal for completion and use `GET /mykos/api/v2/nodes/{id}/memory` to inspect
`configured_mib`. The existing `PUT .../memory` is a guarded live balloon request.
Both setters enforce the current 2048 MiB policy. Cold resizing is available for
Windows, Ubuntu and macOS. Old 4096 MiB RAM captures cannot resume at 2048 MiB;
existing instances are cold-booted and old captures/snapshots remain untouched.

Equivalent root CLI:

```bash
sudo /usr/local/share/mykos/pos/vm memory-configure 12345678 --target-mib 2048 --confirm 12345678
```

All new VM image builds use 2048 MiB RAM. The interactive menu displays the
allocation; `./vm create OS --ram-mib 2048` makes it explicit. Captures and derived
instances inherit this setting. Old images captured with larger allocations must
be rebuilt at 2048 MiB before creating new instances. Windows 11 at 2048 MiB is
below Microsoft's supported minimum; successful installation is not guaranteed.

The pagefile/swap target is 12 GiB. For Ubuntu, the public fixed download
`/mykos/image-setup/ubuntu.py` contains no credentials. Download and run it with
`sudo python3 mykos-ubuntu-setup.py` **inside the installed Ubuntu VM**, reboot and
verify `swapon --show` before capture. The menu prints these steps and asks for
confirmation. The script enables new swap before disabling recognized older
installer swap files, preserves old files, backs up fstab and rejects unknown
swap devices/symlinks. New supported Windows installations receive a fixed
12288 MiB pagefile provisioner through Dockur's OEM hook. Reboot and verify
Win32_PageFileUsage before capture. Existing guests without a functioning agent
need the same setup through their console; the host cannot infer their pagefile
configuration. Legacy Windows without CIM/PowerShell is not automatically
provisioned. macOS uses kernel-managed swap; a fixed total is unavailable in the
stock guest. Its RAM allocation is still 2048 MiB.


## Retained host performance history

`GET /mykos/api/v2/host/history` accepts a read/admin API key and returns up to
30 minutes of real host samples. Browser sessions use
`GET /mykos/api/web/host-history`. An optional `since` query parameter is a
nonnegative Unix timestamp in seconds; timestamps remain UTC Unix time.
Responses include `retention_seconds: 1800`, `sample_interval_seconds: 3`,
`collected_at`, and `samples`, ordered oldest first. Each sample contains
`collected_at`, CPU percentage/count, load averages, memory/disk measurements,
and cached `graph_counts`/`graph_totals` for containers and VPNs.

`mykos-host-history.service` collects without a browser open and performs no
container commands. It reads host counters and the existing collector's cached
status, storing a rolling SQLite history under `/opt/mykos-web/data`.
A fresh latest sample is shared by the live metrics endpoints. Missing or stale
container observations remain unknown. History survives browser/web-service
reloads; old readings expire after 30 minutes. On initial deployment there is no
synthetic historical data: the retained interval grows as samples are collected.
The dashboard shows a trailing five-minute window with a history slider and
Live control; horizontal/Shift-wheel scrolling also moves the selected window.


### Update 81

Repository responses include `latest_rpm`, the filename (without directories), plus `latest_rpm_path`, the relative path of the most recently modified RPM. Snapshot responses include `size_bytes`, allocated disk bytes for that snapshot's private storage (sparse holes are excluded). Container detail responses include `os_codename` beside the unchanged numeric `os_version`.

`DELETE /api/v2/images/{os_name}/build` removes only a failed, unfinished image builder and its private disks. Send `{"confirmation":"ubuntu22.04"}` with the matching OS; administrator authorization is required. Published images and intentional snapshots are retained. The host terminal also accepts `?action=remove-vpn`, with interactive selection and confirmation of a MYKOS host connection.

The terminal title bar uses pane name `terminal` for its notes. Opening or closing
the notes dialog leaves the active terminal connection running.

Pane notes are disposable change requests and are cleared by each installation/update. User accounts, password hashes and API key digests are preserved.

Pane notes are private to the `mgreen` browser account. Both web and v2 notes endpoints require its authenticated session cookie; scope-only API keys cannot read or write these notes. PUT also requires the configured Origin header.

The orange Host notes icon opens personal notes. The equivalent web endpoints are `GET/PUT /api/web/personal-notes`. These use the separate `personal_notes` table and must never be included in automated change-request scans. Installation clears only disposable `pane_notes`; personal text remains private to mgreen and is preserved.

Prepared images stay visible to administrators in their OS panes.
`GET /api/v2/images` also returns `captured_images` with image ID, size, version
and removal state. `DELETE /api/v2/images/{os_name}` accepts an exact OS
`confirmation`, queues guarded removal, and requires an administrator.
`GET /api/v2/images/{os_name}/delete` reports removal progress. Other containers,
saved instances, intentional snapshots, and active builds block removal. The
generic base and its private startup capture are removed only after dependency
checks; Podman image removal never uses force.

Container terminals now accept `?action=remove-vpn` for native Linux and Ubuntu.
The action opens the existing interactive connection selection and confirmation
workflow. Native VPN setup advertises the actual Podman IPv4 address as a /32,
which must match the router peer's private-subnet selector. IKE identities are
independent of this traffic selector and remain unchanged.

Prepared-image catalog entries include `size_bytes` and `created_at` (Unix
seconds, or null when unavailable). Production time comes from Podman's image
creation metadata, without probing a guest. Admin image rows show both size
and the production date/time in the browser's local timezone, including when
removal is blocked by a dependency.


## Shared VPN gateways and topology

The existing admin terminal VPN prompts select independent or shared mode. A
shared gateway must use a newly managed full-tunnel-capable host/native Linux
connection; legacy site-only configurations are rejected before routing changes.
Independent configurations and shared-default profiles cannot be mixed in this
release. Ubuntu-specific new VPN storage support is deferred.

| Method/path under `/mykos/api/v2` | Permission | Behavior |
|---|---|---|
| `GET /vpn/gateways` | Read | Configured profiles and cached full-tunnel/controller status; no credentials |
| `PUT /nodes/{shop}/vpn/gateway` | Admin | Select an existing managed native Linux VPN as shared gateway |
| `PUT /host/vpn/gateway` | Admin | Select an existing managed host VPN as shared gateway |
| `DELETE /nodes/{shop}/vpn/gateway/{connection}` | Admin | Remove only that guest gateway routing profile |
| `DELETE /host/vpn/gateway/{connection}` | Admin | Remove only that host gateway routing profile |

PUT body is `{"connection":"office","remote":"10.77.0.0/24","confirmation":"01234567"}`
for a guest gateway; host confirmation is `"host"`. `remote` identifies the
specific site subnet while the selected default CHILD_SA carries `0.0.0.0/0`.
The host connection name includes its `mykos-` prefix. Profile removal retains the
underlying tunnel files; use the existing interactive remove-VPN workflow to
remove the tunnel as well. Deleting a gateway container is refused until its
shared profiles are removed.

Collected extended node data includes `vpn_topology` with `guest`, `links`,
`via_gateways` and `primary`, plus network membership metadata. Connection links
contain only name, peer, local/remote subnets and mode. Shared links include gateway
ID (`host` for the host option), site subnet and cached `full_tunnel_sa`. Stale
state is unconfirmed, not connected. `host_internet_via_vpn` indicates installed
routing through a confirmed local full-tunnel SA, not a successful remote internet
probe. All guest types can use a shared gateway. No new per-guest polling or PSK
fields are introduced.
