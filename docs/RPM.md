# Signed RHEL 9 and RHEL 10 host packages

[Documentation index](README.md)

Step-by-step starting points: [Packaging README](PACKAGING_README.md) · [Testing README](TESTING_README.md).

**First stable-release host target: RHEL 9 (x86_64). RHEL 10 packages are preview-only; fresh installation is not yet supported because its repository lacks the native `mykos-criu` dependency.** RHEL 8 host support has been dropped. Guest/image support is separate: RHEL 8 guest recipes remain available.

**Clean-install pilot status:** RHEL 9 is the first qualification target. RHEL 10 candidate application RPMs are published, but a fresh install is currently blocked because the matching repository lacks the required native `mykos-criu` dependency. Do not treat staged RPM checks as fresh RHEL 10 installation certification.

Each target host major has its own signed binary RPM, source RPM and repository.
Older EL8 package archives are historical artifacts, not a supported host-installation path. Choose the repository
matching the **host**, not the OS you want to run inside a guest.

## Install from the matching repository

| Host | Repository configuration |
| --- | --- |
| RHEL 9 | [Download RHEL 9 repository configuration](https://myk.sh/repos/mykos-1.5-for-x86_64-el9/mykos-el9.repo) |
| RHEL 10 | [Download RHEL 10 repository configuration](https://myk.sh/repos/mykos-1.5-for-x86_64-el10/mykos-el10.repo) |

For example, on RHEL 9:

```bash
sudo curl -fsSL https://myk.sh/repos/mykos-1.5-for-x86_64-el9/mykos-el9.repo -o /etc/yum.repos.d/mykos-el9.repo
sudo dnf install mykos
rpm -q mykos
sudo systemctl status mykos-firstboot.service --no-pager
```

On RHEL 10, use `el10` in both the URL and destination filename. Keep
`gpgcheck=1`; confirm the signing fingerprint before accepting an imported key:
`BC14191DE284100A246DD552F4384FD1AD974308`. The repository supplies
`RPM-GPG-KEY-MYKOS`. Source and binary RPMs are signed; checksums describe the
signed files. Repository-metadata signing is not asserted by `gpgcheck=1`.

Fresh installation queues `mykos-firstboot.service` after the RPM transaction.
It configures Apache IP access and the application, checks application readiness,
then prints `MYKOS is running: http://SERVER_IP/mykos/`. Check the service journal
if the terminal returns before firstboot finishes. It creates no guest, browser
password or API key. Existing configured deployments skip automatic firstboot.
Create the first browser administrator with:

```bash
sudo /usr/local/sbin/mykos-users create NAME --role admin
```

RHEL BaseOS/AppStream access and a Python package source are needed. EL9 uses
Python 3.11 (`python3.11` and `python3.11-pip`); EL10 uses distribution `python3`
and `python3-pip`. The package has distinct host
release requirements. KVM checks open `/dev/kvm` and create an empty VM without
starting a guest; unavailable KVM produces a warning and retains native Linux use.
See [desktop prerequisites](DESKTOPS.md) before attempting VM lifecycle operations.

## Default HTTP and optional HTTPS

Default access is `http://SERVER_IP/mykos/`; it needs no domain or Certbot.
Domain virtual hosts and Certbot are configured only by explicit opt-in:

```bash
sudo mykos-setup --configure-https
# Or choose the domain and ACME account email explicitly:
sudo mykos-setup --configure-https --domain example.com --email admin@example.com
```

Public DNS and incoming TCP 80/443 must be ready for that domain. The URL is
printed only after app readiness. `--public-origin` alone configures the app's
origin; it does not request a certificate or provision a domain virtual host.
See [bootstrap details and renewal/removal behavior](BOOTSTRAP.md).

## Upgrade and verify

Back up deployment settings and identity data according to
[installation recovery](INSTALL_SERVICES.md#file-snapshots-backup-and-restore),
and back up guest disks and customer backup data separately. Then:

```bash
sudo dnf upgrade mykos
sudo mykos-setup --enable
sudo python3.11 /usr/share/mykos/bin/mykos-install verify
# On RHEL 10, use python3 for this verification command.
rpm -q mykos
```

The RPM transaction checks an existing dashboard's Python environment before
firstboot handling. After a RHEL 8 to RHEL 9 Leapp upgrade, an inherited Python
3.9 environment is rebuilt with EL9's Python 3.11 and the packaged pinned
requirements. EL10 uses its distribution Python. The check also requires the
TLS partial-chain support used for pinned peer certificates and password hashing
support. A compatible environment is left running unchanged.

Only the web runtime is migrated. Existing origins, certificates, firewall rules,
accounts, private notes, peer configuration, guests and service enablement remain
unchanged. A running web service is briefly stopped for a required migration;
a stopped service stays stopped. A failed dependency install or web startup
restores the previous runtime and reports an RPM scriptlet error. Migration
requires access to the configured Python package source. Fresh installations
continue through the normal firstboot setup.

Peer VPN checks accept active IPsec include patterns covering their exact
connection/secrets files, including an existing `mykos-*.secrets` include. They
do not broaden operator VPN include scopes.

`mykos-setup` uses the installed interpreter, pinned Python dependencies and
existing settings. Rerun it after an upgrade to snapshot/configure deployed files
and restart the MYKOS services. It does not select a VPN gateway or create images.
Use the existing public origin; do not replace a working domain with an example.
If verify reports local edits, reconcile them rather than overwriting them.
Kickstarts use `%config(noreplace)`; review `.rpmnew` alongside operator files.

The package owns code under `/usr/share/mykos`; setup deploys runtime sources and
helpers under `/usr/local/share/mykos`, `/usr/local/sbin` and `/opt/mykos-web`.
Configuration, identities, inventory, VM disks, intentional snapshots and saved
VPNs are runtime state, outside the RPM payload. Updates preserve existing guests
and do not migrate older networking automatically. New guests use the configured
private bridge; old pipework/macvlan instances need a separately planned migration.

## Install a downloaded signed RPM

Historical EL9 filename example (release146); substitute the current signed
release filename after downloading the RPM and public key
from the matching trusted repository and verifying the key fingerprint:

```bash
sudo rpm --import ./RPM-GPG-KEY-MYKOS
rpm -Kv ./mykos-1.5.20261024-146.el9.noarch.rpm
sudo dnf --setopt=localpkg_gpgcheck=1 install ./mykos-1.5.20261024-146.el9.noarch.rpm
```

On RHEL 10, use the matching `.el10` filename. A checksum alone does
not authenticate the signer. Do not disable signature checks to bypass a failure.

## Build and repository maintenance

Build from the released source, excluding runtime identities/disks and unpublished
work. With the target's build prerequisites installed:

```bash
python3 bin/mykos-package --output /tmp/mykos-1.5.20261024.tar.gz
mkdir -p ~/rpmbuild/{SOURCES,SPECS}
cp /tmp/mykos-1.5.20261024.tar.gz ~/rpmbuild/SOURCES/
cp packaging/rpm/mykos.spec ~/rpmbuild/SPECS/
rpmbuild -ba --define 'mykos_target_el 9' --define 'dist .el9' ~/rpmbuild/SPECS/mykos.spec
```

Use the appropriate target major and approved signing workflow. A local build is
not automatically signed or published. Signing keys are maintained outside source
and packages. Keep MYKOS repositories separate from synchronized Red Hat mirrors;
refresh only the intended custom repository's metadata after approved publication.
Never add arbitrary RPMs to a mirrored upstream tree whose metadata omits them.

## Validation scope

Release package/application/login checks on fresh RH9 and RH10 used isolated RHEL
UBI init systems with repositories available. These checks cover installation,
IP-first setup and authenticated HTTP sessions, not full bare-metal virtualization,
physical NIC routing, CRIU compatibility or VM lifecycle validation. The user's
vanilla RH9 server testing remains independent. See [validation record](VALIDATION.md).

## Repository visibility and production storage

The current deployment serves repository files directly from production
(`prod.myk.sh`, LAN `192.168.222.80`), under `/home/mykos-repositories`.
The trusted `/home/html` and `/var/www/html` aliases point to that same storage.
Publish new accepted MYKOS releases there; the former repository server is no
longer the publication destination.

Public download trees are limited to:

- [MYKOS EL9](https://myk.sh/repos/mykos-1.5-for-x86_64-el9/)
- [MYKOS EL10](https://myk.sh/repos/mykos-1.5-for-x86_64-el10/)

`updateos` is LAN-only, along with the RHEL mirrors and other private trees.
The only public repositories are MYKOS EL9 and MYKOS EL10.

The same policy applies to the legacy `mykol.com` hostname and the
[production repository listing](https://myk.sh/repos/). RHEL mirrors, historical
EL8 MYKOS packages, `support`, `ostools` and the inventory endpoint remain local/LAN
only. The repository root page lists the three public trees. Serving historical
EL8 files on the LAN does not restore EL8 MYKOS host support.

LAN repository clients resolve `repo.myk.sh` to `192.168.222.80`, keeping trusted
HTTPS hostname verification while avoiding a public-address hairpin. Known
production, dev and legacy-server clients use this mapping. Existing HTTP clients
using the old server's `192.168.222.5` address receive a compatibility redirect to
production's LAN IP; old repository data is preserved for rollback. Do not send
new publication jobs to the old host.

Production collects its own repository inventory. Dev reads production's
LAN-only inventory endpoint. The old inventory timer is disabled. Package
checks, metadata reads and repository cutover do not require guest/image builds,
a web application restart, a physical NIC move or changes to customer disks.

## Safely synchronize the RHEL mirrors

Run synchronization on the production repository host. It requires cgroup v2,
root access, installed Podman, GNU `cp` with reflink-capable storage, and valid upstream
RHEL entitlements. Workers use matching upstream definitions when available;
missing standard RHEL 7–10 definitions are authenticated against fixed Red Hat
CDN paths using the host entitlement certificate and CA mounted read-only.
Production and dev use reflink-enabled XFS. Missing upstream access, keys, COW
support or free space stops the job without publishing an incomplete repository.
The public MYKOS-only and LAN-only `updateos` policy above is unchanged by mirror sync.

Start the normal RHEL 7–10 job and follow its journal:

```bash
sudo systemctl start --no-block mykos-repo-sync.service
sudo journalctl -fu mykos-repo-sync.service
# Inspect the final result, including a failed/interrupted run:
sudo systemctl show mykos-repo-sync.service -p ActiveState -p Result -p ExecMainStatus
```

To synchronize one major, for example RHEL 9:

```bash
sudo /usr/local/share/mykos/repo/bin/sync_repos 9
```

The direct command also enters the managed background slice and waits for its
transient `mykos-repo-sync-cli.service`, collecting that unit after completion.
Both entry points share the sync lock; the dashboard recognizes either one and
blocks repository deletion while sync holds the lock. RH6 is rejected.

The entire job receives one CPU of quota, a 2 GiB soft memory threshold and 3 GiB hard limit, CPU/IO weights of 10, nice value 15
and best-effort IO priority 7. Each worker is additionally limited to one CPU,
2 GiB RAM with no additional swap and 256 processes. Background processes have an increased OOM score so they are preferred
for termination under memory pressure. Package-manager downloads
use one parallel transfer and a 20 MiB/s throttle. Workers can write only private
staging storage, not the served repository tree; normal host subscription secrets
remain read-only. Required Red Hat RPM keys are imported inside the disposable
worker, and reposync signature checking and TLS verification are explicitly enabled.

The host verifies every referenced metadata file and package checksum before
publication. Existing repository data is cloned using COW, never writable hard
links; no full-copy fallback or package deletion is used. Prior immutable package
and metadata URLs remain available for clients holding an older manifest. Each
complete repository is switched with Linux atomic directory exchange. A failure
in one component prevents publication of that major's staged components; already
completed repository switches from earlier work remain valid.

For a normal cancellation:

```bash
sudo systemctl stop mykos-repo-sync.service mykos-repo-sync-cli.service
```

The job stops only its recorded, labeled worker and removes unpublished scratch.
A hard kill or power failure can leave a root-private `.mykos-sync-*` workspace.
The next invocation recovers only recognized, root-owned workspaces and verifies
worker ownership before cleanup; unknown entries are retained for inspection.
Do not delete repository roots or shared images to clear a failed job. Worker
images are reusable operational caches, not guest builds. A mirror sync does not
refresh MYKOS application RPMs; publish those through the signed release workflow.

### Control-plane removal

RPM removal uses the canonical installer stop_services action before removing
managed files. It stops/disables the complete maintained timer/service list,
including idle-base, snapshot-preview garbage collection and speed measurement,
and stops matching active control-plane workers. It does not target persistent
Podman runtime scopes or remove guest disks, images, notes or intentional snapshots.


### Repository links from forwarded dashboards

Repository links use the configured download server rather than the browser's incidental host or SSH tunnel port. A root-owned `/etc/mykos/repository-downloads.json` can set `{"base_url":"http://192.0.2.80/"}` to a reachable LAN repository server (replace this documentation address with the actual server). Remote inventory clients retain their configured repository base. Otherwise the dashboard uses the configured MYKOS public origin's document root (`/`), matching a fresh bootstrap installation. Existing deployments serving repositories at `/repos/` or another path must set that exact base URL explicitly. Download configuration does not change Apache access controls: only supported MYKOS EL9/EL10 trees are public; updateos and other private repositories still require LAN access.


## Private RPM download statistics

Administrators can open **RPM stats** in the Repos pane. JSON and HTML reports
are `/mykos/api/web/mykos/rpm-downloads` and the same path plus `/view`.
Viewer and peer-forwarded access is denied. Unconfigured hosts
report that state explicitly. Server commands are:

```bash
sudo /usr/local/sbin/mykos-download-stats report
sudo /usr/local/sbin/mykos-download-stats report --json
sudo systemctl start mykos-rpm-download-stats.service
```

Collection is optional and only configured on the repository host. Review the
Apache, logrotate and JSON examples in `repo/`. The configuration is root-owned
mode0600 `/etc/mykos/rpm-download-stats.json`, with `format: 1`, public
`github_repository: OWNER/REPOSITORY`, and `log_directory: /var/log/httpd/rpm-downloads`.
The global Apache include requires mod_unique_id; use `apachectl configtest`
before graceful reload. Initialize collection before enabling the logging
include, then enable `mykos-rpm-download-stats.timer` (fifteen-minute refresh).
The report page reloads every minute. Displayed timestamps and daily activity use
America/Chicago (CST in winter, CDT in summer). Existing request timestamps are
recovered from retained dedicated RPM logs without changing cumulative totals;
any historical request whose timestamp cannot be recovered stays in a clearly
labeled UTC section instead of being assigned an invented Central date.
Create root-owned mode0700 `/var/log/httpd/rpm-downloads` before enabling the
Apache include; its nested location avoids the existing top-level Apache
logrotate wildcard. Logs use rename/create rotation with fourteen-day retention. The root-only
statistics database is in `/var/lib/mykos/rpm-download-stats/`; the aggregate
snapshot is root-owned and mykosweb-readable at
`/opt/mykos-web/data/rpm-download-stats.json`. Public GitHub assets need no token;
API failures retain last-good counters with an explicit stale status.

Only public EL9/EL10 RPM GET requests are included, with direct and `/repos/`
aliases normalized. Metadata and HEAD requests are excluded. A full-file
response requires HTTP200, positive bytes matching Content-Length, and no
logged connection abort. Partial/range206 responses, aborts and failures are
separate; ranges are not stitched into full downloads. Explicitly marked
MYKOS validation traffic is excluded. Opaque request IDs prevent duplicate
collection through rotation/compression; inode/head checkpoints handle
replacement and truncation. Repository history starts when enabled; general
access logs are not backfilled because they include unmarked deployment tests.

Daily dates are UTC. Byte totals include every matching response body.
Internal/external classification uses the client address Apache observes and
configured internal ranges; adjust trusted proxy handling where applicable.
The example covers loopback and the two established MYKOS LAN subnets. GitHub
RPM asset counters (including source RPMs) are shown separately, with their last
refresh time; they include repeat and validation downloads. Neither source
measures unique users, customers or successful installations, and the sources
are not deduplicated. The dedicated log and report contain no client IPs,
query strings, referrers, cookies or user-agent text. Existing access logs and
public/LAN repository restrictions are unchanged.


## Repos on a new installation

The Repos pane manages **this server's** RPM repository directories under `/var/www/html`; installing MYKOS does not copy the myk.sh mirrors, configure a Red Hat subscription, or publish private downloads. An empty pane inventory is normal until repositories are created. Only directories containing a regular `repodata/repomd.xml` are managed; unrelated website folders are neither listed nor deletable. The `updateos` directory, when present, remains a read-only LAN tree.

A fresh `mykos-bootstrap` Apache site serves `/var/www/html` at the configured hostname's root. For a different virtual host or alias, set root-owned `/etc/mykos/repository-downloads.json` (mode 0600) to the reachable base, for example `{"base_url":"https://packages.example.com/repos/"}`. This setting changes links only. Configure Apache aliases, TLS and access rules separately; never expose entitled RHEL content, updateos or private support trees publicly. No myk.sh LAN address is required.

The pane's Sync action runs the installed low-priority RHEL mirror service on the local host. It requires valid local Red Hat entitlements, network access and disk capacity; follow the synchronization prerequisites above. A separately configured remote inventory stays read-only and requires the remote server's explicit URLs. MYKOS does not stop or start shared Apache from the pane: manage serving and access policy through the host's Apache configuration so the dashboard and other sites stay reachable. RPM download statistics remain opt-in and show “not configured” until configured for that installation.
