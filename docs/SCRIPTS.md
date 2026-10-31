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

| Script | Actual behavior / usage |
|---|---|
| `./bin/list_cloud_account` | Lists backup accounts, detected POS type/OS, disk usage |
| `./bin/create_cloud_account` | Prompts for shopcode, client public SSH key, and subscriber flag; creates Linux user/home under `/backups/tfrsync-SHOP` and restricted authorized key |
| `./bin/change_account_key` | Prompts for shopcode and public key; replaces that account's authorized key |
| `./bin/view_cloud_key` | Prompts for shopcode; displays its authorized key |
| `./bin/get_cloud_key` | Copies backed-up client public **and private** SSH keys to `/home/tfsupport/keys`; requires that account/directory; automatic nightly cleanup is not installed |
| `./bin/delete_cloud_account` | Prompts for shopcode and whether to delete data; removes Linux user and optionally its home/data |
| `./bin/is_subscriber` | Prompts for shopcode; reads/toggles `paying_customer` marker |
| `./bin/tfrsync` | SSH forced-command wrapper; invoked by authorized_keys, accepts an rsync server command and rejects several shell metacharacters; not the backup client |
| `./bin/install` | Compatibility forwarder to global installer; no longer changes SELinux or installs the old cron file |

The legacy scripts have limited input validation and should be operated only by
trusted host administrators. The account restriction is a legacy shell wrapper,
not a general SSH sandbox. Review SSH restrictions and backup-user permissions for
your environment. `tfrsync.pl` client software is external (`ostools`). A typical
client backup command documented by the old project is:

```sh
tfrsync.pl --cloud --backup=all --rsync-account=tfrsync-01234567 --cloud-server=YOUR_HOST --force-rsync-account
```

This command runs on the client with its own prepared SSH keys and software, not
inside the installer. Scheduler setup and a tested restore are separate operations.

## POS containers (`cd /usr/local/share/mykos/pos`)

`./pos` is interactive: choose shopcode/image, build images, start/stop containers,
connect as container root, and add/remove VPNs. It uses hidden files `.shopcode`,
`.SHOP.postype`, `.SHOP.imagetype`, `.SHOP.container`, and `.SHOP/` VPN data. Preserve
those files during upgrades. Image creation uses `livemedia-creator`, kickstarts in
`ks/rh7.ks` through `ks/rh10.ks`, Podman build/commit, and site repositories.

The legacy menu's default guest/image selection is RH8; this does not restore
RHEL 8 host support. Its staging logic uses that global value;
selecting another image does not comprehensively update all launch logic. Use the
new configured API launcher for controlled, noninteractive staging, and validate
legacy image workflows before relying on them across OS versions.

`./get_peers` reads Meraki configuration from `/etc/mykos/pos.env` and prints the
organization's third-party VPN peers through `jq`. Configure credentials first.
The legacy VPN update performs a PUT of a single constructed peers list; it can
replace other configured organization peers. Review the intended full peer list
and Meraki API behavior before using that menu option. Secrets are no longer embedded
in maintained code, but legacy per-shop generated files may still contain them.

`./pipework HOST_INTERFACE -i eth1 CONTAINER dhclient` adds the container interface
and obtains DHCP. It changes host/container networking and requires root and Linux
network tooling. The new staging helper calls this exact form. Check interface,
DHCP client availability, routes, and VPN policies in your staging environment.

The legacy Purge action stops **all** rootful Podman containers, prunes images, and
removes hidden state. It is broad and destructive; it is not part of installation,
update, removal, or the API's single-shop stop operation.

| Installed privileged helper | Invocation / role |
|---|---|
| `mykos-container-helper` | systemd socket request `{ "args": ["ACTION", ...] }`; actions `list`, `diskfree`, `logs`, `top`, `ipsec`, `checkit`, `stage-check`, `stage-status`, `stage-start`, `stop-pos` |
| `mykos-container-helper --stage-worker SHOP` | Invoked by `mykos-stage@SHOP.service`; consumes queued job and writes result |
| `mykos-stage-pos SHOP yes\|no` | Root-only noninteractive configured staging; normally invoked only by worker |
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
| `./bin/sync_repos` | Installs httpd/Podman as needed, enables Apache, creates privileged temporary UBI containers for RHEL 7–10, runs reposync/createrepo, removes containers |

Synchronization needs working registry/network/subscription access and enough disk.
Pinned historical UBI tags may need review. The script is not invoked or scheduled
by the global installer. Review its failure handling and target repositories before
running it against production mirrors.

## Reports, billing, and legacy scheduled jobs

| Script | Inputs / outputs / effects |
|---|---|
| `data/bin/tfcloudaccountstats.sh` | Parses per-shop `tfrsync-summary.log`, writes `/tmp/TU_STATS_LINUX_Cloud_Backups.txt`; status, counts, size, subscriber flag |
| `data/bin/tfcloudaccountdetail.sh` | Writes `/tmp/TU_STATS_LINUX_Cloud_Backup_Detail.txt`; yearly monthly success/failure counts; needs installed `monthname.sh` |
| `data/bin/tfcloudauthkeys.sh` | Collects backed-up client public keys and certificate dates into `/tmp/TU_STATS_LINUX_Cloud_Auth_Keys.txt`; treat report as restricted |
| `back/bin/tfcloud*.sh` | Older duplicates; prefer the `data/bin` copies for report work; they are not identical |
| `back/bin/cloud_pricing` | Computes subscriber storage pricing tiers, updates `/usr/local/bin/cloud_pricing_previous`, writes change report, sends mail to hard-coded recipients |
| `back/bin/cloud_pricing_previous`, `cloud_pricing_changed` | Historical output/state files; intentionally excluded from installation |
| `back/bin/backup_perf.sh` | Samples load, connections, free space into `/tmp/backup_perf.txt`; eventually emails hard-coded recipients |
| `back/bin/recon.sh` | Writes `/tmp/recon.txt` and emails account/space totals to hard-coded recipients |
| `back/bin/monthname.sh NN` | Prints English month name for 01–12 |
| `back/bin/strip.sh` | `strip.sh FILENAME` prints a cleaned `/var/log/session/FILENAME` stream with terminal control sequences removed; does not edit the original |
| `back/bin/tsrs_batch` | Publishes backed-up Dell TSR files into Apache docroot, replacing shop folders, with permissive modes |
| `back/bin/tsrs` | CGI HTML index linking published TSR reports; hostname is hard-coded |
| `back/bin/crontab` | Historical schedule reference; first two entries lack the user field required by `/etc/cron.d`; not installed or enabled |

Review report scripts before using them for billing decisions: the summary script
can overwrite Daisy detection with Unknown, and success matching/counts have legacy
assumptions. Reports use shared `/tmp` names. No guarantee of billing accuracy or
secure publication is implied by copying these scripts. Email scripts must be
reviewed for recipients, credentials, transport, and approvals before scheduling.
The installer supplies support scripts at `/usr/local/bin` for compatibility but
never executes them, publishes TSR content, or sends email.

## Host utilities

`bin/updateos [groupname|scriptname|help] [dry]` is a legacy root script downloader
and executor. It defaults to a historical HTTP backend, caches scripts under
`/usr/bin/.updateos`, and uses MD5 comparison. Its `dry` behavior is specific to
individual commands and may still perform downloads. It is not used by the new
installer; audit and configure trusted delivery before operational use.

`bin/blacklist.sh` downloads IP lists and modifies iptables/ipset rules; it reads
`/etc/ip-blacklist.conf` and related manual-blacklist state. `bin/blacklist_login_attempts`
follows `/var/log/secure` and appends invalid-login source IPs to a manual blacklist.
These are third-party/legacy tools, not enabled services. They can block legitimate
administrators and conflict with firewalld policy; inspect them before enabling.
