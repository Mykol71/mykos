# mykos
Multi-OS OS

MYKOS is a virtualization and host-management solution for RHEL 8 and RHEL 9.
It combines a web control center, authenticated API, and text administration
menus for native Linux containers and QEMU-based Windows, macOS, and Ubuntu
VMs managed through Podman.

The dashboard includes host performance history, OS-specific container panes,
live snapshots, rsync accounts, repository management, and per-pane notes.
Browser sessions and scoped API keys use separate authentication.

## Installation

After configuring the signed MYKOS repository for your RHEL release:

```bash
sudo dnf install mykos
```

On a fresh host, the RPM automatically starts `mykos-firstboot.service` after
the package transaction. Setup derives the certificate domain from the last
two labels of the host's fully qualified hostname, configures Apache/Certbot
HTTPS, and initializes MYKOS. For example, `prod.example.com` uses
`example.com`; that domain must resolve publicly to the server, with TCP 80
and 443 reachable. Existing configured installations are preserved.

```bash
sudo systemctl status mykos-firstboot.service --no-pager
sudo journalctl -u mykos-firstboot.service --no-pager
sudo /usr/local/sbin/mykos-users create NAME --role admin
```

The application opens at `https://DOMAIN/mykos/`. API documentation is at
`/mykos/api/v2/docs`, and operator guides at `/mykos/docs`.

For a source bundle that configures HTTPS before installing the RPM:

```bash
sudo bash bin/install bootstrap
```

See [automatic setup](docs/BOOTSTRAP.md), [RPMs](docs/RPM.md), and
[installation/recovery](docs/INSTALL_SERVICES.md). KVM is needed for VM guests;
installation checks it without starting a VM. Native Linux containers do not
require nested virtualization.

## Images and containers

An **image** is a generically installed OS plus startup disk, memory, and device
state where required. A **container** is an ID-specific instance with private
storage and identity. A **snapshot** captures a live instance while leaving it
running.

The text menu is available through `sudo mykos`. Modern VM images use a
one-command unattended build, for example:

```bash
sudo /usr/share/mykos/pos/vm build ubuntu22.04
sudo /usr/share/mykos/pos/vm build win10
sudo /usr/share/mykos/pos/vm build wns2022
sudo /usr/share/mykos/pos/vm build mac15
```

Ubuntu uses autoinstall, Windows uses Autounattend.xml, and macOS uses
startosinstall. Completed builds commit the installed payload under a readable
OS image name and leave a generic base running for fast instance creation.
Native RedHat images use the kickstarts in `pos/ks`. VM guests use 2 GiB RAM;
Windows and Ubuntu use 12 GiB pagefile/swap, while macOS manages its swap.
Native Linux containers share host memory without a fixed VM RAM allocation.
See [image builds](docs/IMAGE_BUILDS.md), [desktop lifecycle](docs/DESKTOPS.md),
and [networking](docs/NETWORKING.md).

## Source and validation

- `services/web`: dashboard, API, collectors and identity management.
- `services/helpers`: privileged lifecycle, storage, networking and monitoring.
- `services/systemd`: sockets, services and collector timers.
- `pos`, `back`, `repo`, `data`: operator menus and commands.
- `packaging/rpm`: signed EL8/EL9 RPM build definitions.
- `tests`: Python and browser JavaScript regression checks.

```bash
python3 -m pip install -r services/requirements.txt pytest httpx
python3 -m pytest tests
for test in tests/*.cjs; do node "$test" || exit; done
```

Configuration, browser users, API credentials, VM disks, snapshots, and signing
private keys are runtime state and do not belong in the source repository.
Updates preserve those identities and guest data. Final RPM removal cleans up
unchanged bootstrap-owned HTTPS settings and unshared certificates; shared
certificates, accounts and administrator edits are preserved.

More guides: [architecture](docs/ARCHITECTURE.md), [API](docs/API.md),
[identity management](docs/IDENTITY.md), [snapshots and validation](docs/VALIDATION.md).
