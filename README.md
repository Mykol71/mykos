# MYKOS

**MYKOS is a Bring Your Own License (BYOL) virtualization solution. All vendor licensing rules apply.** You are responsible for obtaining and maintaining the licenses, subscriptions and permissions required for the operating systems, applications and other software you run with MYKOS. MYKOS does not grant vendor software licenses or override vendor terms, including virtualization and hardware restrictions.

MYKOS aims to help fill the gap for organizations reassessing virtualization after VMware’s changes to ESXi and vSphere licensing. It offers a RHEL-based platform for managing supported virtual machines and Linux containers, with no MYKOS license charge for permitted personal and internal business use. Evaluate workload compatibility, operational requirements and vendor licensing before migrating. See [Broadcom’s licensing transition guidance](https://knowledge.broadcom.com/external/article/429208) and [current ESXi free-edition availability](https://knowledge.broadcom.com/external/article/399823).

**Older applications on newer hardware**

MYKOS could help businesses get more value from the systems they already own. By bringing compatible older applications onto newer hardware and reusing prepared operating-system environments, it can reduce the need to rebuild, reconfigure, or replace working software simply because a physical server is aging. Retailers, manufacturers, distributors, professional-services firms, and other organizations could benefit from less repetitive setup work, smoother migrations, and fewer disruptions to everyday operations.

MYKOS is a virtualization and host-management solution with RHEL 9 as its first stable-release host target. RHEL 10 packages remain a preview pending dependency and fresh-install validation.
RHEL 8 is no longer a supported MYKOS host; RHEL 8 guest images remain available.
It combines a web control center, authenticated API, and text administration
menus for native Linux containers and QEMU-based Windows, macOS, Ubuntu,
Fedora GNOME, and Android-x86 VMs managed through Podman.

The dashboard includes host performance history, OS-specific container panes,
live snapshots, rsync accounts, repository management, and per-pane notes.
Browser sessions and scoped API keys use separate authentication.
Full administrators can [copy persistent instances to connected peers](docs/PEERS.md#copy-an-instance-to-a-peer),
retaining compatible VM RAM when supported. The source stays in place and the
new copy starts with isolated guest networking.
See the [screen examples gallery](docs/SCREENSHOTS.md) for illustrated workflows.

## Supported images at a glance

**47 image selections: 25 native Linux containers and 22 VM-backed guests.**
Guest image support is separate from host support: **RHEL 9 is the stable host
platform; RHEL 10 remains a preview.** RHEL 8 host support is not offered.

| Environment | Supported guest images |
| --- | --- |
| Native enterprise Linux | RHEL 7, 8, 9, 10; Rocky Linux 8, 9, 10; AlmaLinux 8, 9, 10; Oracle Linux 8, 9, 10; CentOS Stream 8, 9, 10 |
| Other native Linux | Fedora 43, 44; Debian 13; Arch Linux (rolling); SUSE Linux Enterprise BCI Init 15 SP7; Amazon Linux 2023; Alpine Linux 3.24 (OpenRC) |
| Native SQL Server environments | SQL Server 2022 and 2025 on Linux |
| Windows desktop VMs | Windows 11, 10, 8.1 Enterprise, 7 Ultimate, XP, Me, 2000, 98 |
| Windows Server VMs | Windows Server 2025, 2022, 2019, 2016 |
| macOS VMs | Monterey 12, Ventura 13, Sonoma 14, Sequoia 15 |
| Ubuntu VMs | Ubuntu 20.04, 22.04, 24.04 |
| Fedora desktop VMs | Fedora 43 and 44 GNOME |
| Android VM | Android-x86 9.0-r2 (legacy, manual installation, graphical access only) |

See the complete [native image identifiers](#native-el-and-other-linux-containers)
and [VM build commands](#all-supported-vm-types) below. These are accepted
image selections, not a claim that every new installation has been qualified
on every host. Vendor licensing and hardware restrictions still apply. SUSE
uses the SLE BCI Init base rather than a full subscribed SLES installation;
its scope and the native networking/timezone limits are described below.

## Free use and licensing

MYKOS core source is publicly available in the [GitHub repository](https://github.com/Mykol71/mykos). It is **source-available under the MYKOS Free-Use License**, not open source. Public visibility does not add unrestricted redistribution, resale or paid MYKOS management-service rights. The license permits inspection, building, testing and internal modification for its permitted uses; separate platform rights and third-party licenses still apply.

MYKOS is available at no license charge for personal and internal business use,
including running your own customer-facing applications. Optional hosting,
setup, migration and support services are separate. See the
[MYKOS Free-Use License](LICENSE.txt) and [licensing guide](docs/LICENSING.md).
Redistribution of covered MYKOS material and paid access to its management
functions require separate permission. This is a proprietary free-use license,
not an open-source license. [Third-party materials](THIRD_PARTY_NOTICES.txt)
retain their own rights; previously granted rights are preserved.

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

### All supported instance types

MYKOS supports both native Linux containers and VM-backed instances. The native
EL entries are listed here alongside the VM types; host OS support is separate.
EL6, EL7 and EL8 are guest choices; MYKOS hosts require RHEL9 or RHEL10.
These are the current accepted image/instance selections, not a claim that every
fresh guest installation has been retested on every host.

#### Native EL and other Linux containers

Run `sudo mykos`, choose **c. Build image**, then enter one OS identifier from
this table. Native builds use signed RPM rootfs workers, Kickstart/rootfs creation or official OCI bases and Podman, rather than
`pos/vm build` or the VM-only `mykos-image-build` command.

| Native guest/image type | OS identifier entered at the menu |
| --- | --- |
| Red Hat Enterprise Linux 6.10 (legacy) | `rh6` |
| Red Hat Enterprise Linux 7.9 (legacy) | `rh7` |
| Red Hat Enterprise Linux 8 | `rh8` |
| Red Hat Enterprise Linux 9 | `rh9` |
| Red Hat Enterprise Linux 10 | `rh10` |
| Rocky Linux 8 | `rocky8` |
| Rocky Linux 9 | `rocky9` |
| Rocky Linux 10 | `rocky10` |
| AlmaLinux 8 | `alma8` |
| AlmaLinux 9 | `alma9` |
| AlmaLinux 10 | `alma10` |
| Oracle Linux 6.10 / 7.9 (legacy) | `oracle6`, `oracle7` |
| Oracle Linux 8 | `oracle8` |
| Oracle Linux 9 | `oracle9` |
| Oracle Linux 10 | `oracle10` |
| CentOS 6.10 / 7.9.2009 (legacy) | `centos6`, `centos7` |
| Scientific Linux 6.10 / 7.9 (legacy) | `scientific6`, `scientific7` |
| Springdale Linux 6.10 / 7.9 (legacy) | `springdale6`, `springdale7` |
| CentOS Stream 8 | `centos8` |
| CentOS Stream 9 | `centos9` |
| CentOS Stream 10 | `centos10` |
| Fedora 43 | `fedora43` |
| Fedora 44 | `fedora44` |
| SQL Server 2022 on native Linux | `mssql2022` |
| SQL Server 2025 on native Linux | `mssql2025` |
| Debian 13 | `debian13` |
| Arch Linux (rolling) | `archlinux` |
| SUSE Linux Enterprise BCI Init 15 SP7 | `sles15sp7` |
| Amazon Linux 2023 | `amazon2023` |
| Alpine Linux 3.24 (OpenRC) | `alpine` |

Debian, Arch, SUSE, Amazon Linux and Alpine use official vendor OCI bases with a
recorded digest and vendor package verification. Existing locally cached upstream
tags are preserved. They use managed NAT networking; distro-specific VPN creation
is not yet qualified and is unavailable. These images default to UTC. Custom guest
timezone changes have not been qualified for memory snapshot restoration;
snapshot preflight rejects changed timezone files without modifying the guest.
SUSE uses the SLE BCI Init base; it does not include a full SLES subscription or
paid vendor support. Guest support does not add Debian, Arch, SUSE, Amazon or
Alpine as MYKOS host platforms.
These OCI builds produce an image without keeping a generic base running;
its base console opens on demand, and instances start from the stored image.

For a new instance, choose **s. ID**, enter its eight-digit ID and OS selection,
then use **2. Start/Stop**. Build the required base image first if it is not yet
available. Repository access, installation media and applicable vendor licenses
are still required for the chosen guest.

The menu's `all` selection covers the **16 EL images**: RHEL 7–10 plus Rocky,
AlmaLinux, Oracle and CentOS Stream 8–10. The new EL6/EL7 selections other than
the existing `rh7`, Fedora, SQL Server and the five additional native families are individual
selections. Each example is an alternative; do not start a full image batch just
to check application or documentation changes.

#### All supported VM types

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
| Fedora 43 GNOME desktop | `sudo /usr/share/mykos/pos/vm build fedora43vm` |
| Fedora 44 GNOME desktop | `sudo /usr/share/mykos/pos/vm build fedora44vm` |
| Ubuntu 20.04 | `sudo /usr/share/mykos/pos/vm build ubuntu20.04` |
| Ubuntu 22.04 | `sudo /usr/share/mykos/pos/vm build ubuntu22.04` |
| Ubuntu 24.04 | `sudo /usr/share/mykos/pos/vm build ubuntu24.04` |
| macOS 12 Monterey | `sudo /usr/share/mykos/pos/vm build mac12` |
| macOS 13 Ventura | `sudo /usr/share/mykos/pos/vm build mac13` |
| macOS 14 Sonoma | `sudo /usr/share/mykos/pos/vm build mac14` |
| macOS 15 Sequoia | `sudo /usr/share/mykos/pos/vm build mac15` |

Legacy Windows and Android-x86 use interactive setup. Open the returned console, finish
installation and reach the installed desktop, then run the matching Finish command.
These selections use `create` and `prepare`, rather than the unattended builder.

| Guest type | Create image | Finish after setup |
| --- | --- | --- |
| Windows 7 Ultimate | `sudo /usr/share/mykos/pos/vm create win7u` | `sudo /usr/share/mykos/pos/vm prepare win7u` |
| Windows XP | `sudo /usr/share/mykos/pos/vm create winxp` | `sudo /usr/share/mykos/pos/vm prepare winxp` |
| Windows Me | `sudo /usr/share/mykos/pos/vm create winme` | `sudo /usr/share/mykos/pos/vm prepare winme` |
| Windows 2000 | `sudo /usr/share/mykos/pos/vm create win2k` | `sudo /usr/share/mykos/pos/vm prepare win2k` |
| Windows 98 | `sudo /usr/share/mykos/pos/vm create win98` | `sudo /usr/share/mykos/pos/vm prepare win98` |
| Android-x86 9.0-r2 | `sudo /usr/share/mykos/pos/vm create android9` | `sudo /usr/share/mykos/pos/vm prepare android9` (run twice as described below) |

Android-x86 requires two Finish steps: run `prepare android9` once after installation
to switch to the installed disk, then again after that desktop boots to capture
the image. See the
[Android-x86 workflow](docs/IMAGE_BUILDS.md#android-x86-legacy-installer). It is a
legacy compatibility guest with graphical access only.

Ubuntu uses autoinstall, Windows uses Autounattend.xml, and macOS uses
startosinstall. Completed builds commit the installed payload under a readable
OS image name and leave a generic base running for fast instance creation.
Native RedHat images use the kickstarts in `pos/ks`. VM guests default to 2 GiB RAM; modern VM image builds can select 2, 4 or 8 GiB and new instances inherit the recorded image size;
Windows, Ubuntu and Fedora desktop VMs use 12 GiB pagefile/swap, while macOS manages its swap.
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

## Packaging and testing: start here

- [Packaging README](docs/PACKAGING_README.md): source archives, EL9/EL10 RPMs, signing, staged installation and publication.
- [Testing README](docs/TESTING_README.md): environment setup, Python and JavaScript checks, documentation validation, live verification and cleanup.

## Repository downloads

Production now serves the [EL9 MYKOS repository](https://myk.sh/repos/mykos-1.5-for-x86_64-el9/),
[EL10 MYKOS repository](https://myk.sh/repos/mykos-1.5-for-x86_64-el10/) publicly.
[updateos documentation](docs/SCRIPTS.md#updateos) is public, while the updater
downloads, RHEL mirrors and other private repository content remain LAN-only. See [repository access and installation](docs/RPM.md#repository-visibility-and-production-storage)
for current storage, client settings and publication destinations.

Administrators can track repository RPM transfers and separate GitHub release-asset counters through the Repos pane’s **RPM stats** report and the server CLI. Collection is opt-in on the repository host; see [private RPM download statistics](docs/RPM.md#private-rpm-download-statistics) for accounting semantics and setup.

Legacy EL6/EL7 guest details, final upstream archives and explicit LAN mirror sync selections are documented in the [image build guide](docs/IMAGE_BUILDS.md#legacy-enterprise-linux-guests). MYKOS host packages remain EL9/EL10 only.
