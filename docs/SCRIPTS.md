# Operator script catalog

[Documentation index](README.md)

Use `sudo mykos` for the menu workflow on an installed host. Most legacy scripts are
interactive and depend on their current working directory. The following paths are
relative to `/usr/local/share/mykos` unless an absolute path is shown. Do not execute
legacy administration scripts on a development desktop just to inspect their help.
They often perform mutations immediately and have no dry-run mode.

<!-- contents:start -->
**Contents**

- [Global deployment and service tools](#global-deployment-and-service-tools)
- [Menus](#menus)
- [Backup accounts (`cd /usr/local/share/mykos/back`)](#backup-accounts-cd-usrlocalsharemykosback)
- [POS containers (`cd /usr/local/share/mykos/pos`)](#pos-containers-cd-usrlocalsharemykospos)
- [Repository mirrors (`cd /usr/local/share/mykos/repo`)](#repository-mirrors-cd-usrlocalsharemykosrepo)
- [Reports, billing, and legacy scheduled jobs](#reports-billing-and-legacy-scheduled-jobs)
- [Host utilities](#host-utilities)
  - [updateos](#updateos)
- [Physical memory inventory](#physical-memory-inventory)
- [Build and sync job history](#build-and-sync-job-history)

<!-- contents:end -->

## Global deployment and service tools

| Script / invocation | Use |
|---|---|
| `bin/install install [--install-deps] [--enable]` | Install/update all maintained components; see installation guide |
| `bin/install verify` | Check managed files, permissions, imports, Linux unit syntax |
| `bin/install status` | Print manifest and managed systemd states |
| `bin/install backup --output FILE [--include-data]` | Snapshot managed code/settings; optionally runtime data and identities |
| `bin/install restore --snapshot FILE [--include-data]` | Validate and restore format-2 snapshot with another pre-change snapshot |
| `bin/install uninstall [--dry-run]` | Remove unchanged managed code/units, retain settings/data |
| `bin/install-services` | Forward to global installer; explicit format-1 bundle flags use `install-bundle` |
| `bin/install-bundle package --output FILE [--with-data]` | Legacy deployed-host packaging; not a fresh source install |
| `bin/install-bundle verify --bundle FILE` | Validate legacy archive paths and file checksums |
| `bin/install-bundle install --bundle FILE [--with-data]` | Historical archive recovery; can replace current code |
| `bin/install-bundle restore --snapshot FILE [--with-data]` | Restore a format-1 pre-install snapshot |
| `bin/install-legacy` | Historical installer retained for reference; do not run (recursive replacement, old dependency/config handling) |
| `/usr/local/sbin/mykos-users` | `create`, `passwd`, `role`, `list`, `revoke-sessions`, `delete`; see identity guide |
| `/usr/local/sbin/mykos-api-keys` | `create LABEL read\|admin`, `list`, `revoke ID` |
| `/opt/mykos-web/create-admin.sh NAME` | Compatibility command to create a new browser admin |
| `/opt/mykos-web/manage.py users\|keys ...` | Underlying manager; normally use root wrappers so files belong to `mykosweb` |
| `/opt/mykos-web/.venv/bin/python /opt/mykos-web/monitor.py` | Legacy interactive console over snapshots; run on the installed host, not as a collector |

All global installer actions accept `--root DIR` for isolated file staging.
Mutating actions accept `--dry-run`; no external changes are performed by that flag.
The old bundle utility does not have the global installer's staging-root mode.

## Menus

`MENU` routes to `back/MENU`, `pos/pos`, `repo/MENU`, and `data/MENU`. Root is expected
for operational use. `MENU` creates `/backups` if absent. `.envtype` in the toolkit
root supplies its display name; create it manually as root when desired. Submenus
return to the launcher directory so relative support paths resolve.

The repository menu advertises Create/Delete, but `create_repos` and `delete_repos`
are absent. The data menu displays Pricing, Insights, and Dell TSR items without
handlers. Only Summary/Detail are implemented there. These are existing product
gaps, not hidden features supplied by the installer.

## Backup accounts (`cd /usr/local/share/mykos/back`)

Backup account management is delivered by a separately installed private business extension. Its dashboard pane, account-management API, transfer setup and implementation are not included in the base MYKOS distribution. Existing customer backup data is preserved during core updates.

## POS containers (`cd /usr/local/share/mykos/pos`)

`./pos` is interactive: choose shopcode/image, build images, start/stop containers,
connect as container root, and add/remove VPNs. It uses hidden files `.shopcode`,
`.SHOP.postype`, `.SHOP.imagetype`, `.SHOP.container`, and `.SHOP/` VPN data. Preserve
those files during upgrades. Current native choices and engines are listed in
[image builds](IMAGE_BUILDS.md#all-supported-instance-types). EL6/EL7 use signed
vendor-RPM assembly; the five additional Linux families use official OCI bases;
other EL/Fedora/SQL recipes use Kickstart rootfs creation. These are rootful
managed containers sharing the host kernel.

`sudo mykos` opens the top-level menu: choose **2. Containers**, then **c. Build
image**. The selected OS is saved per ID and passed to the configured staging
helper. Native starts use the managed network and persistent lifecycle; the old
global-RH8/pipework description does not describe the current path. Reopen an old
terminal menu after upgrades before using new tracking or image choices.

`./get_peers` reads Meraki configuration from `/etc/mykos/pos.env` and prints the
organization's third-party VPN peers through `jq`. Configure credentials first.
The Meraki peer update fetches and validates the complete peer list before
appending a new peer; matching entries are not overwritten and conflicts fail.
See [Meraki peer updates](NETWORKING.md#meraki-peer-updates). Avoid concurrent
external updates because the upstream endpoint replaces a complete peer list.
Secrets are no longer embedded
in maintained code, but legacy per-shop generated files may still contain them.

`./pipework` is retained for historical network compatibility; current native
staging uses Podman-managed networking, not a post-start pipework/dhclient call.
Read the [network guide](NETWORKING.md) before changing an older instance.

The **p. Purge** menu enters the explicit MYKOS purge workflow. It is broad and
destructive, with its own confirmation and preservation checks; it is not a
single-instance delete and must never be run as a documentation or release test.

| Installed privileged helper | Invocation / role |
|---|---|
| `mykos-container-helper` | systemd socket request `{ "args": ["ACTION", ...] }`; actions `list`, `diskfree`, `logs`, `top`, `ipsec`, `checkit`, `stage-check`, `stage-status`, `stage-start`, `stop-pos` |
| `mykos-container-helper --stage-worker SHOP` | Invoked by `mykos-stage@SHOP.service`; consumes queued job and writes result |
| `mykos-stage-pos SHOP yes\|no [IMAGE]` | Root-only noninteractive configured staging; normally invoked only by worker |
| `mykos-terminal-helper` | systemd socket: `{ "shop":"01234567" }`, readiness line, then PTY bytes |
| `mykos_runtime.py` | Shared validation, root config checks, Podman targeting, bounded command output, job storage |

Do not expose helper sockets over TCP or make them world-writable. They are internal
protocols; integrations should use authenticated HTTP API v2.

## Repository mirrors (`cd /usr/local/share/mykos/repo`)

| Script | Behavior |
|---|---|
| `./bin/list_repos` | Lists Apache-hosted repository URLs |
| `./bin/repos_status` | Prints Apache active status |
| `./bin/disable_repos` | Toggles the entire host Apache service, affecting more than mirrors if the host also proxies the dashboard |
| `./bin/sync_repos [SELECTION]` | Defaults to RHEL7–10. Explicit majors6–10 or legacy clone identifiers select the corresponding final-release mirrors. Uses bounded workers, vendor signatures, checksum validation and private COW staging with atomic publication. Legacy/RHEL/updateos remain LAN-only. |

Synchronization needs working registry/network/subscription access and enough disk.
Pinned historical UBI tags may need review. The script is not invoked or scheduled
by the global installer. Review its failure handling and target repositories before
running it against production mirrors.

## Reports, billing, and legacy scheduled jobs

Business-specific backup, billing and report implementations are supplied by a
separate private extension. Their commands are not public-core features and are
not enabled as scheduled jobs by installing the core. Use the installed edition's
own documentation rather than historical paths from another deployment.

## Host utilities

### updateos

`bin/updateos [groupname|scriptname|help] [dry]` is a legacy root script downloader
and executor. It defaults to a historical HTTP backend, caches scripts under
`/usr/bin/.updateos`, and uses MD5 comparison. Its `dry` behavior is specific to
individual commands and may still perform downloads. It is not used by the new
installer; audit and configure trusted delivery before operational use.

The usage documentation is public. Updater script downloads and staging repositories are LAN-only; a public-host download returning403 does not mean they should be exposed. Use the reachable LAN repository configured for this installation. `MYKOS_UPDATEOS_BACKEND` selects the trusted updater backend. The maintained downloader keeps anchor-only directory parsing and rejects failed, empty or HTML script downloads before execution.

`bin/blacklist.sh` downloads IP lists and modifies iptables/ipset rules; it reads
`/etc/ip-blacklist.conf` and related manual-blacklist state. `bin/blacklist_login_attempts`
follows `/var/log/secure` and appends invalid-login source IPs to a manual blacklist.
These are third-party/legacy tools, not enabled services. They can block legitimate
administrators and conflict with firewalld policy; inspect them before enabling.


## Physical memory inventory

The gray hardware line at the bottom of the Host pane groups the installed memory type, configured speed, and total DIMM capacity reported by SMBIOS under one **Memory** label, for example `Memory: DDR4 · 3200 MT/s · 32 GiB`. Empty slots are excluded; mixed types or speeds are listed separately. Configured speed is preferred, falling back to module speed when firmware omits it. Installed capacity can exceed Linux usable memory because firmware reserves some RAM. Unavailable fields are omitted. The root collector caches inventory for 24 hours; the dashboard reads only the filtered collector output.

## Build and sync job history

Full local administrators use [Jobs & Recovery](UI_GUIDE.md#jobs--recovery).
Native builds launched through the current POS entry point and repository syncs
record attempts; VM rows can also be observations of existing saved builder state.
History cannot reconstruct earlier native builds. Retry/Resume is explicit and
revision-checked; closing the pane does not stop work. API keys and peer forwarding
cannot access these browser-only recovery controls.
