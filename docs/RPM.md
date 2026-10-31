# Signed RHEL 9 and RHEL 10 host packages

[Documentation index](README.md)

**Supported MYKOS hosts: RHEL 9 and RHEL 10 (x86_64).** RHEL 8 host support has been dropped. Guest/image support is separate: RHEL 8 guest recipes remain available.

Each supported host major has its own signed binary RPM, source RPM and repository.
Older EL8 package archives are historical artifacts, not a supported host-installation path. Choose the repository
matching the **host**, not the OS you want to run inside a guest.

## Install from the matching repository

| Host | Repository configuration |
| --- | --- |
| RHEL 9 | `https://mykol.com/mykos-1.5-for-x86_64-el9/mykos-el9.repo` |
| RHEL 10 | `https://mykol.com/mykos-1.5-for-x86_64-el10/mykos-el10.repo` |

For example, on RHEL 9:

```bash
sudo curl -fsSL https://mykol.com/mykos-1.5-for-x86_64-el9/mykos-el9.repo -o /etc/yum.repos.d/mykos-el9.repo
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
