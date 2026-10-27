# Global installation, updates, verification, and recovery

This guide replaces the earlier capture/inspect instructions. `bin/install` is the
canonical global installer. It deploys the full operator toolkit plus maintained
web/API sources, collectors, systemd units, helpers, and local identity managers.
It does not read the historical archive during installation.

## Prerequisites and scope

Use a RHEL-compatible **Linux** server with systemd and Python 3.9 or newer with `hashlib.scrypt`/OpenSSL; RHEL 9
is the reference target. RHEL 8 needs a newer Python interpreter installed first.
For example, where your enabled subscription provides it, install Python 3.11 and
invoke `python3.11 bin/mykos-install install --python /usr/bin/python3.11` if the
default `python3` is older than 3.9. RHEL 10 image kickstarts are included, but DHCP
client/image-building package availability must be checked on that host. The
installer does not silently add external repositories to obtain missing packages.

Run `install --dry-run` to inspect the exact file plan and package list.
`--install-deps` invokes `dnf`, creates a venv, installs `services/requirements.txt`,
and records exact resolved packages in `/opt/mykos-web/installed-requirements.txt`.
Direct dependency versions are pinned in source; use the recorded full list to
reproduce a tested site environment. Dependencies are not downloaded during staging-root installs.

Without `--install-deps`, required OS packages must already exist and the installer
creates the venv if needed, then checks imports. On an offline host populate it
from your approved wheelhouse before running install, for example:

```sh
sudo /usr/bin/python3 -m venv /opt/mykos-web/.venv
sudo /opt/mykos-web/.venv/bin/pip install --no-index --find-links /srv/wheels -r services/requirements.txt
```

`--image-build-deps` additionally requests Lorax, Anaconda TUI, and pykickstart;
it requires `--install-deps`. Building POS images remains a separate operator
workflow and needs working repository URLs and actual RTI software. Apache TLS,
firewall policy, SELinux policy, subscription registration, image content, backup
clients, Meraki configuration, DNS, and outbound email must be configured for the
site. The installer does not disable SELinux or change global firewall/TLS policy.

## New installation

Windows, Windows Server, macOS and Ubuntu desktop lifecycle helpers are included
in every installation. See [DESKTOPS.md](DESKTOPS.md) for template creation and
capture. To initialize KVM/storage checks and allow desktop ports from an
explicit trusted LAN, add `--desktop-network 192.168.222.0/24` to `install`
(replace that network for the target host). It creates source-restricted
runtime/permanent firewalld rules after backing up the firewall configuration.
Without the flag, installation does not change desktop firewall access. Guest
disks and templates are created separately and are preserved during updates.

Create a new source-only installation bundle after changes with:

```sh
python3 bin/mykos-package --output releases/mykos-rhel9-desktops.tar.gz
```

The bundle follows the installer's current file list, includes per-file and
archive SHA-256 checksums, and excludes runtime guest disks, hidden POS metadata,
credentials and web identity databases.

From any working directory, using the source copy's absolute installer path:

```sh
sudo /path/to/mykos/bin/install install --dry-run --public-origin https://mykos.example.com
sudo /path/to/mykos/bin/install install --public-origin https://mykos.example.com --install-deps
sudo mykos-users create alice --role admin
sudo mykos-users create observer --role user
sudo /path/to/mykos/bin/install verify
```

Replace the example hostname with your actual HTTPS origin. Do not include `/mykos`
in the origin. New live activation refuses the placeholder hostname unless you
explicitly supply a real origin. The account `mykosweb` is created as a system user
with a nologin shell. There is no default password, browser user, API key, or bearer
token. Existing settings and identity files are preserved.

Edit these files before enabling operations:

| File | Settings |
|---|---|
| `/etc/mykos/web.env` | `MYKOS_PUBLIC_ORIGIN=https://your-host`; `MYKOS_SECURE_COOKIE=1` |
| `/etc/mykos/config.json` | `stage_image`: optional default for older API clients; the dashboard selects an image from running supported OS template containers; `network_device`: optional host interface override (otherwise detected using `ip route get 8.8.8.8`); `terminal_enabled`: boolean |
| `/etc/mykos/pos.env` | Root-only shell assignments: `REPOSERVER`, `MERAKI_API_KEY`, `MERAKI_ORG_ID`, `VPN_PSK` |

For unattended image callbacks, `MYKOS_IMAGE_BUILD_ORIGIN=https://certificate-name`
can select a certificate-matching DNS name separately from a browser origin that
uses a LAN IP. The callback resolves that name to this host and verifies TLS
against the configured image-build CA; certificate checks remain enabled.

macOS first boot creates its administrator through native `sysadminctl` and
initializes the home directory. The private installer package supplies the
password through a hidden prompt, never a process argument. Its temporary
credential and staging files are removed before the installed image is captured.

For example, after validating the image and host NIC:

```json
{"stage_image":"localhost/rti-production:approved","network_device":"ens192","terminal_enabled":false}
```

No image is pulled during API staging (`--pull=never`). A blank image/NIC rejects
staging. Terminal access is disabled by default. Keep config and POS secret files
root-owned with mode 0600, and `web.env` root:`mykosweb` 0640. POS secrets are shell
configuration: quote values safely, and never paste untrusted shell code into it.
Existing VPN secrets are preserved. The source kickstarts lock default accounts;
provision access explicitly after image building rather than using old passwords.

Configure an HTTPS VirtualHost using `services/apache-mykos.conf.example` as a starting
point. Enable proxy, HTTP proxy, WebSocket proxy, and headers modules as needed;
validate with `apachectl configtest`. The example strips `/mykos` for the backend and
handles `/mykos/api/terminal/` WebSocket upgrades. Older Apache versions may require
`mod_proxy_wstunnel`. Configure SELinux proxy-network permissions and labels for
this site's service access; inspect audit logs rather than making SELinux permissive.
Port 8000 remains bound to loopback. Check proxy headers and keep direct backend
access private.

Then activate deliberately:

```sh
sudo /path/to/mykos/bin/install install --public-origin https://mykos.example.com --enable
sudo /path/to/mykos/bin/install status
sudo systemctl list-timers --all | grep mykos-
```

`--enable` enables sockets, collectors, and the web service and restarts the web
service. It does not activate Apache, SSH, firewall changes, billing, repository
sync, blacklisting, TSR publication, or actual customer backups. Existing installed
`web.env` is preserved; changing `--public-origin` on later installs does not rewrite
it. Edit that file directly and restart `mykos-web.service` to change the origin.

## Updating

Use a maintenance window for source or privileged-helper updates. First back up,
stop web/collector activity, and ensure no staging job or terminal is active:

```sh
sudo /path/to/new-mykos/bin/install backup --output /secure/mykos-before-update.tar.gz --include-data
sudo systemctl stop mykos-web.service mykos-collector.timer mykos-details.timer mykos-extended.timer
sudo systemctl stop mykos-collector.service mykos-details.service mykos-extended.service
sudo /path/to/new-mykos/bin/install install --public-origin https://mykos.example.com --install-deps
sudo mykos-users list
sudo mykos-users role alice admin
sudo /path/to/new-mykos/bin/install verify
sudo /path/to/new-mykos/bin/install install --public-origin https://mykos.example.com --enable
```

Do not run two installers concurrently. Each installation snapshots replaced files
and previously absent destinations before changing code. Existing config, inventory,
identity databases, POS state, customer backup data, and unrelated files are not
replaced. An existing `/backups` symlink to a directory (for example,
`/backups -> /home/backups`) is preserved without changing its destination,
ownership, or permissions. Broken links are rejected. The Backup pane allows this
root-controlled alias when its target and parents are trusted directories, while
continuing to reject symlinks beneath individual backup accounts. The old
installer’s recursive deletion behavior is not used. Duplicate
site-specific `mykos-*` collectors are not automatically disabled: inspect them and
resolve duplicates explicitly. Existing accounts migrating from the old application
default to nonadmin; promote intended admins before restarting it.

Installation is not a fully transactional package-manager operation. A dependency
failure can occur after code files have been copied; the reported snapshot is the
rollback point. OS package changes, venv dependency upgrades, created system users,
and service enablement are not rolled back by a file snapshot. Keep the dependency
freeze and machine/image backups needed to reverse those changes.

## Verification and troubleshooting

```sh
sudo /usr/local/share/mykos/bin/install verify
sudo /usr/local/share/mykos/bin/install status
sudo systemctl status mykos-web.service mykos-container-admin.socket mykos-terminal.socket --no-pager
sudo journalctl -u mykos-web.service -n 100 --no-pager
sudo journalctl -u mykos-collector.service -u mykos-details.service -u mykos-extended.service -n 100 --no-pager
sudo journalctl -u mykos-stage@01234567.service -n 100 --no-pager
sudo systemctl cat mykos-web.service mykos-collector.timer
```

`verify` checks managed file presence, code hashes, permissions, root ownership,
Python imports, and unit syntax on Linux. Settings/inventory are mutable and are
not hash-compared. It does not prove VPN reachability or successful POS staging.
`status` reports the manifest and systemd state; inactive units can make it exit
nonzero. `/mykos/api/v2/docs`, authenticated read requests, browser login, fresh collector
timestamps, host storage, and nonadmin denial should be checked through HTTPS.
Perform staging/stop/terminal integration tests on approved staging containers.

The web user must be able to read snapshot files but not write application code.
Socket mode/group and the helper service interpreter must match this installation.
Collectors run in rootful Podman's context. Duplicate eight-digit container prefixes
are rejected for helper targeting; resolve ambiguity instead of guessing a target.

## File snapshots, backup, and restore

```sh
sudo ./bin/install backup --output /secure/mykos-code-settings.tar.gz
sudo ./bin/install backup --output /secure/mykos-with-identities.tar.gz --include-data
sudo ./bin/install restore --snapshot /secure/mykos-with-identities.tar.gz --include-data --dry-run
sudo ./bin/install restore --snapshot /secure/mykos-with-identities.tar.gz --include-data
```

Snapshots use manifest format 2 with SHA-256 file checksums, original file modes and
numeric ownership, plus paths absent before installation. Restores validate paths,
regular archive members, duplicates, sizes, checksums, and symlink destinations
before writes. They create another pre-change snapshot. Restoration removes only
files marked previously absent. Services are stopped for a live restore and remain
stopped; verify before enabling again. Close active terminals/staging workers first.
SQLite databases are captured through SQLite's online backup API, including committed
WAL transactions, rather than by copying a potentially inconsistent live DB file.
A data-bearing restore requires explicit `--include-data`; it can replace identities,
sessions, and revocations. The two identity DBs are individually consistent, not a
cross-database transaction. Freeze identity changes for a coordinated backup.

Default backups contain managed code, settings, and inventory; `--include-data` adds
`/opt/mykos-web/data/`. Snapshots and their parent directory have restrictive permissions.
They can still contain secrets, so encrypt and move them to restricted off-host
storage. File ownership is numeric: same-host rollback is the primary use. On a
replacement server, create the service account first and reconcile UID/GID ownership
before activation.

Snapshots do not capture Podman images/storage, `/backups`, custom Apache/TLS files,
OS packages, venv contents, site-specific units, or the entire POS runtime state.
Back those up independently. Never describe this file backup as a whole-host DR image.

### Legacy format-1 archives

Explicit legacy operations remain available:

```sh
sudo ./bin/install-services package --output /secure/legacy-bundle.tar.gz
sudo ./bin/install-services verify --bundle /secure/legacy-bundle.tar.gz
sudo ./bin/install-services install --bundle /secure/legacy-bundle.tar.gz
sudo ./bin/install-services restore --snapshot /secure/legacy-prechange.tar.gz
```

`--with-data` is the old format's explicit data inclusion option. Use this only for
intentional recovery: installing an old bundle may overwrite maintained code with
its historical version. Run the global installer afterward to return to maintained
sources and explicit role handling. The old `capture`, `inspect`, `--confirm`,
`--restore-auth`, and `status` archive commands from historical documents are not
implemented; global `status` is a host installation status operation.

## Staging without host side effects

On macOS use a real path under `/private/tmp` (not its `/tmp` symlink):

```sh
./bin/install install --root /private/tmp/mykos-stage --public-origin https://mykos.example.com
./bin/install verify --root /private/tmp/mykos-stage
./bin/install uninstall --root /private/tmp/mykos-stage --dry-run
```

`--root` performs file-layout operations only: no dnf/pip, chown, system users,
service activation, collectors, or containers. `--enable` and `--install-deps` are
rejected with staging roots. `--dry-run` writes nothing. Staged scripts contain
production absolute paths and must not be executed as an emulated live installation.

## Removal

```sh
sudo ./bin/install uninstall --dry-run
sudo ./bin/install uninstall
```

Removal first checks hashes and refuses modified managed code, creates a recovery
snapshot, disables/stops managed long-running units and timers, and removes managed
code/unit files only. Stop active per-connection terminals and staging workers first.
It preserves settings, identities, snapshots, inventory, venv, OS accounts, customer
backup storage, POS state, images, and unrelated files. It does not remove Apache
proxy configuration or undo installed packages. Retained directories can be reviewed
and removed separately according to your site's data-retention requirements.


## Rename an existing installation

Use `bin/mykos-migrate` once when upgrading an installation with a previous project
name. Run it from the new source checkout during a maintenance window. Finish
container staging and close terminals first. The command does not modify running
containers, repository contents, backup account names, or customer backup storage.
A working system Python 3.9+ with scrypt support, access to the Python package
index, and root access are required. Migration retains existing system packages
and installs only the Python dependencies for the new virtual environment.
The interpreter running the migration is used unless `--python` selects another.

The migration finds the single previous manifest under
`/var/lib/*-installer/installed.json`. If there are multiple candidates, pass its
absolute filename with `--from-manifest`. It derives the previous names and paths
from that manifest and installed service definitions.

```sh
sudo python3 bin/mykos-migrate --dry-run --public-origin https://mykol.com
sudo python3 bin/mykos-migrate --public-origin https://mykol.com
sudo python3 bin/mykos-install verify
sudo systemctl status mykos-web.service --no-pager -l
```

Before changing settings, the command stops previous services and writes a mode
0600 archive at `/var/backups/mykos-installer/before-migration-TIMESTAMP.tar.gz`.
It preserves original file paths and includes identity databases, previous
configuration, inventory, POS metadata, service drop-ins and affected Apache
configuration. Keep this archive private. The new virtual environment is created
under `/opt/mykos-web/.venv`; the previous environment is retained for recovery.
The web account is renamed to `mykosweb` with its numeric UID retained. Its primary
group is renamed to match. Previous files and units remain on disk but disabled;
they are recovery material, not an active compatibility layer.

After success, open `https://mykol.com/mykos/`. Browser users sign in again with
their existing credentials. API keys keep their original values and scopes, but
clients must use `/mykos/api/v2` and `X-MYKOS-API-Key`. New keys start with
`mykos2_`. Update bookmarks, external monitors, automation, cron entries and any
custom proxy rules outside the standard Apache configuration directories.
The migration updates configured Apache proxy paths while preserving the hostname
and certificate settings; it runs `apachectl configtest` before reloading Apache.

A failure leaves the previous files and the recovery archive available. Do not
rerun blindly over a partially installed destination. Preserve the error and the
archive. After fixing the reported problem, resume using that original archive:

```sh
sudo python3 bin/mykos-migrate --resume-from /var/backups/mykos-installer/before-migration-TIMESTAMP.tar.gz --dry-run --public-origin https://mykol.com
sudo python3 bin/mykos-migrate --resume-from /var/backups/mykos-installer/before-migration-TIMESTAMP.tar.gz --public-origin https://mykol.com
```

Resume validates the archived predecessor manifest, original service definition,
and identity UID before accepting an account already renamed to `mykosweb`.
It keeps the original recovery archive and preserves copied settings and databases.
It also accepts a partial destination manifest from a later verification failure.

For rollback instead, stop the new units, restore the archived files to their
original absolute paths as root, rename `mykosweb` and its group back to the names
recorded by the original web unit and archived ownership, reload systemd, validate
Apache, and re-enable the previous sockets, web service and timers. The archive
contains sensitive configuration and identities; extract it only onto the original
trusted host. Running containers and customer backup storage are outside this
archive and are not rolled back by it.

For subsequent mykos updates:

```sh
sudo python3 bin/mykos-install backup --include-data --output /var/backups/mykos-before-update.tar.gz
sudo python3 bin/mykos-install install --public-origin https://mykol.com
sudo python3 bin/mykos-install verify
sudo systemctl --no-block restart mykos-web.service
```


## Scheduled-command collector

Installation initializes `/var/lib/mykos/commands` as root mode 0700. Activation
with `--enable` enables `mykos-command-collector.timer`, which dispatches due jobs
every 30 seconds. It does not change the performance collector interval.
For an existing source installation updated without `--enable`, activate it with:

```bash
sudo systemctl enable --now mykos-command-collector.timer
```

The queue is mutable data outside the RPM and survives updates. Finish active
jobs before removal/rollback. See API.md for admin-key submission, result access,
at-most-once behavior and Windows guest-agent prerequisites.

### Completed action refreshes and saved-image retries

Container actions refresh only the affected ID's inventory, details and resource
rows at nice -5. VM startup publishes that row as soon as Podman is running,
before memory restoration and ID-specific hooks finish. Host terminal/VPN completion refreshes host data without probing
containers. Backup/repository actions reload only their own pane; privileged
listing work also runs at nice -5. Routine collector intervals are unchanged.

After image capture and commit, builder removal allows up to five minutes for
Podman storage cleanup. If only the generic base restart fails, the committed
image remains ready. Rerun the same Build command to retry that base restart;
installation and memory capture are not repeated. An unusable builder requiring
manual cleanup is reported explicitly, without deleting its disks or image.

The performance chart draws running container and VPN connection counts as translucent paired columns behind the resource lines. Container and VPN columns alternate beside each other in five-second buckets without overlap. Both count series rise from zero; 15 running containers or active VPN connections equals 100%. Historical counts use the same scale, and missing or stale readings leave gaps. The history slider uses a grey accent.

Host has an orange personal-notes icon beside its white change-request icon. Both use the same icon shape and a larger notes dialog. Personal notes persist across updates and are excluded from change-request processing. Changed container/VPN count bars receive a brighter fill and bold edge at the time of the change; other bars remain faint. A subtle diagonal texture overlays the dashboard background.

## Native image builder prerequisite

The standard installer dependency set and RPM require `pykickstart`, which
provides `ksvalidator`. `python3-kickstart` alone supplies only its Python
library on EL8/EL9 and does not provide that command. Existing installations
missing the CLI can install it with `sudo dnf install -y pykickstart`.

All native Linux builds use the existing local Anaconda/Lorax workflow, including
the operator's established RH9-on-RH8 method. The menu validates the prepared
kickstart against the installed parser without imposing a matching host release.
Temporary copies adapt timesource and weak-dependency option spellings to that
parser; saved kickstarts remain unchanged. Repository/package compatibility
still has to succeed during the actual build. Changing repository URLs does not
upgrade the local installer. Using an already built image is a separate path.

