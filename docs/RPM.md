# RHEL 8 and RHEL 9 RPMs

The RPM owns runtime code, text menus, helpers, units, and installer sources
under `/usr/share/mykos`. Configuration, identities, inventory, venv and guest
disks are excluded from the payload and retained on package removal.

## Install or upgrade

Fresh `dnf install mykos` installations now queue automatic HTTPS and MYKOS
setup through `mykos-firstboot.service`. The domain is the final two hostname
labels. Existing configured installations are skipped. Public DNS and TCP
80/443 must reach this host. See [BOOTSTRAP.md](BOOTSTRAP.md) for prerequisites,
progress, retries and cleanup.

```bash
sudo rpm --import ./RPM-GPG-KEY-MYKOS
rpm -Kv ./mykos-1.5.20261024-46.el8.noarch.rpm
sudo dnf --setopt=localpkg_gpgcheck=1 install ./mykos-1.5.20261024-46.el8.noarch.rpm
sudo mykos-setup --public-origin https://YOUR_HOST --enable
# Optional trusted-LAN desktop setup:
sudo mykos-setup --public-origin https://YOUR_HOST --desktop-network 192.168.222.0/24 --enable
sudo python3.9 /usr/share/mykos/bin/mykos-install verify
```

Replace the hostname and network. `mykos-setup` installs the pinned Python
requirements into the Python 3.9 venv, so PyPI or a configured pip mirror must
be reachable. Fresh RPM installation automatically starts HTTPS/setup after the package transaction;
that setup downloads Python requirements, configures Apache, and opens HTTP/HTTPS
when necessary. It creates no guests and provisions no user passwords. Existing
configured deployments skip this automatic setup. Installation and upgrades
also verify KVM by opening `/dev/kvm` and creating an empty VM (no guest starts).
If unavailable, RPM prints a warning and continues for RedHat-only use. Rerun
`sudo /usr/local/sbin/mykos-kvm-check` after enabling host virtualization. Create browser users
with `sudo /usr/local/sbin/mykos-users create NAME --role admin`.

Rerun `mykos-setup` after an RPM upgrade to snapshot/configure the deployment
and restart services. Existing settings and identities are preserved. See
[DESKTOPS.md](DESKTOPS.md) for template creation/capture. The RHEL 8 release RPM and source RPM are signed. Import the supplied
`RPM-GPG-KEY-MYKOS` public key and verify the package before installation.
The signing fingerprint is `BC14191DE284100A246DD552F4384FD1AD974308`.
SHA-256 checksums reflect the signed files. The dedicated signing key is kept
in `/home/mgreen/.mykos-signing` on the dev build host, outside source and packages;
keep that private directory protected and backed up. Ordinary builds and installs
do not create signing keys.

## Build

On RHEL 8 with `rpm-build`, `python39` and `python39-pip` installed:

```bash
python3.9 bin/mykos-package --output /tmp/mykos-1.5.20261024.tar.gz
mkdir -p ~/rpmbuild/{SOURCES,SPECS}
cp /tmp/mykos-1.5.20261024.tar.gz ~/rpmbuild/SOURCES/
cp packaging/rpm/mykos.spec ~/rpmbuild/SPECS/
rpmbuild -ba --define 'dist .el8' ~/rpmbuild/SPECS/mykos.spec
```

## Independent custom repository

Publish custom RPMs under `/var/www/html/mykos-rpms/el8/`, served at
`https://mykol.com/mykos-rpms/el8/`. Generate metadata only for this directory
with `createrepo_c --update`. The sync script names only Red Hat mirror
directories, so this sibling repository remains independent. Do not add RPMs
inside a downloaded Red Hat mirror: upstream metadata will not list them and
sync can replace local metadata. The `/mykos/` URL is reserved for the app proxy.

The RHEL 8 package uses the existing pipework networking implementation. It does
not replace pipework or migrate container networking. Runtime file timestamps
are excluded from RPM verification because host setup regenerates those files;
content, permissions and ownership remain verified.


Release 17 adds the single `/usr/bin/mykos` launcher (also `/bin/mykos` on
RHEL), mode 0755. It runs `MENU` from `/usr/share/mykos`; upgrades remove
`/usr/local/bin/mykos`. `MENU` is installed executable. It includes current timezone/NTP syntax and removes obsolete Anaconda sections
from RHEL 8/9 kickstarts. Staging detects the local builder parser and uses the
legacy NTP spelling only in build copies when timesource is unsupported. Kickstart files in both the RPM source and
runtime directories use `%config(noreplace)`, preserving local edits during
upgrades; changed package defaults may be installed as `.rpmnew`. Release 16 adds a private scheduled-command API and 30-second dispatcher, with
Windows PowerShell through QEMU Guest Agent (see API.md for prerequisites). It includes persistent native lifecycle controls and the live Snapshots pane/API.
CRIU is an RPM dependency; `mykos-setup` initializes private snapshot storage and its
scoped native capture hook. Stored guest images, container disks and snapshots are
runtime data outside the RPM payload and remain across upgrades. The current RPM
still targets RHEL 8; a separate RHEL 9 build and Pipework validation are pending.


## RHEL 9 repository

The RHEL 9 build uses the distribution's `python3`/`python3-pip` packages and
requires `system-release(releasever) = 9`. It retains `dhcp-client` for pipework
networking. RHEL 8 continues to use `python39`/`python39-pip` and releasever 8.
Build the same source/spec for RHEL 9 with:

```bash
rpmbuild -ba --define 'mykos_target_el 9' --define 'dist .el9' ~/rpmbuild/SPECS/mykos.spec
```

The signed RHEL 9 repository is `/home/html/el-9-for-x86_64-mykos-1.5` on prod,
served at `https://mykol.com/el-9-for-x86_64-mykos-1.5/`. Refresh its metadata
with `createrepo_c --update /home/html/el-9-for-x86_64-mykos-1.5`.
Keep it separate from Red Hat synced repositories.

Install the repository configuration on a RHEL 9 client:

```bash
sudo curl -fsSL https://mykol.com/el-9-for-x86_64-mykos-1.5/mykos-el9.repo -o /etc/yum.repos.d/mykos-el9.repo
sudo dnf install mykos
sudo mykos-setup --public-origin https://YOUR_HOST --enable
```

The client configuration uses the existing MYKOS signing key with `gpgcheck=1`.
The RHEL 9 repo is disabled in production's local repo configuration while that
host runs RHEL 8. Enable it only on RHEL 9 clients.


Release 18 applies full-width aesthetic glyphs to the environment name on the
dashboard and login page. The `.envtype` file, browser title, accessible name
and API data retain the original text.


Release 19 restores vendor icon colors: Red Hat red, Ubuntu orange, Windows
blue and Apple white. Existing icon sizes and alignment remain unchanged.


Release 20 changes visible container/account labels from Code to ID, including
forms, column headings, confirmation dialogs, API error text and text menus.
Existing API field names, endpoints and stored identifiers remain compatible.
