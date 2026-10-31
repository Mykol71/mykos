# MYKOS

MYKOS is a virtualization and host-management solution for RHEL 9 and RHEL 10.
RHEL 8 is no longer a supported MYKOS host; RHEL 8 guest images remain available.
It combines a web control center, authenticated API, and text administration
menus for native Linux containers and QEMU-based Windows, macOS, and Ubuntu
VMs managed through Podman.

The dashboard includes host performance history, OS-specific container panes,
live snapshots, rsync accounts, repository management, and per-pane notes.
Browser sessions and scoped API keys use separate authentication.
See the [screen examples gallery](docs/SCREENSHOTS.md) for illustrated workflows.

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

### All supported VM types

Each row is an alternative example. Run only the build you need.
The commands below use the installed toolkit path.

| Guest type | Unattended build command |
| --- | --- |
| Windows 11 | `sudo /usr/share/mykos/pos/vm build win11` |
| Windows 10 | `sudo /usr/share/mykos/pos/vm build win10` |
| Windows 8.1 Enterprise | `sudo /usr/share/mykos/pos/vm build win8e` |
| Windows Server 2025 | `sudo /usr/share/mykos/pos/vm build wns2025` |
| Windows Server 2022 | `sudo /usr/share/mykos/pos/vm build wns2022` |
| Windows Server 2019 | `sudo /usr/share/mykos/pos/vm build wns2019` |
| Windows Server 2016 | `sudo /usr/share/mykos/pos/vm build wns2016` |
| Ubuntu 20.04 | `sudo /usr/share/mykos/pos/vm build ubuntu20.04` |
| Ubuntu 22.04 | `sudo /usr/share/mykos/pos/vm build ubuntu22.04` |
| Ubuntu 24.04 | `sudo /usr/share/mykos/pos/vm build ubuntu24.04` |
| macOS 12 Monterey | `sudo /usr/share/mykos/pos/vm build mac12` |
| macOS 13 Ventura | `sudo /usr/share/mykos/pos/vm build mac13` |
| macOS 14 Sonoma | `sudo /usr/share/mykos/pos/vm build mac14` |
| macOS 15 Sequoia | `sudo /usr/share/mykos/pos/vm build mac15` |

Legacy Windows versions use interactive setup. Open the returned console, finish
installation and reach the installed desktop, then run the matching Finish command.
These selections use `create` and `prepare`, rather than the unattended builder.

| Guest type | Create image | Finish after setup |
| --- | --- | --- |
| Windows 7 Ultimate | `sudo /usr/share/mykos/pos/vm create win7u` | `sudo /usr/share/mykos/pos/vm prepare win7u` |
| Windows XP | `sudo /usr/share/mykos/pos/vm create winxp` | `sudo /usr/share/mykos/pos/vm prepare winxp` |
| Windows Me | `sudo /usr/share/mykos/pos/vm create winme` | `sudo /usr/share/mykos/pos/vm prepare winme` |
| Windows 2000 | `sudo /usr/share/mykos/pos/vm create win2k` | `sudo /usr/share/mykos/pos/vm prepare win2k` |
| Windows 98 | `sudo /usr/share/mykos/pos/vm create win98` | `sudo /usr/share/mykos/pos/vm prepare win98` |

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
- `packaging/rpm`: signed EL9/EL10 RPM build definitions.
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
