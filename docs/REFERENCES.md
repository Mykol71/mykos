# References

[Documentation index](README.md)

MYKOS brings together the projects below for container management, image creation,
VMs, snapshots, networking, and its browser interface. Each entry explains the
connection to MYKOS. The compatibility section distinguishes a credited patch
source from technical documentation that explains the behavior behind a repair.

## Operating system vendors and projects

MYKOS hosts run **RHEL 9 or RHEL 10 on x86_64**. Guest recipes have their own
version list; see [image builds](IMAGE_BUILDS.md) and [desktop guests](DESKTOPS.md).

| OS family | Vendor or project main site | Related product or documentation |
| --- | --- | --- |
| Red Hat Enterprise Linux | [Red Hat](https://www.redhat.com/) | [RHEL developer resources](https://developers.redhat.com/products/rhel/overview) |
| Rocky Linux | [Rocky Linux](https://rockylinux.org/) | [Rocky documentation](https://docs.rockylinux.org/) |
| AlmaLinux | [AlmaLinux OS Foundation](https://almalinux.org/) | [AlmaLinux wiki](https://wiki.almalinux.org/) |
| Oracle Linux | [Oracle](https://www.oracle.com/) | [Oracle Linux](https://www.oracle.com/linux/) |
| CentOS Stream | [CentOS Project](https://www.centos.org/) | [CentOS Stream](https://www.centos.org/centos-stream/) |
| Fedora Linux | [Fedora Project](https://fedoraproject.org/) | [Fedora documentation](https://docs.fedoraproject.org/) |
| Ubuntu | [Canonical](https://canonical.com/) | [Ubuntu](https://ubuntu.com/) |
| Windows and Windows Server | [Microsoft](https://www.microsoft.com/) | [Windows](https://www.microsoft.com/windows/) · [Windows Server documentation](https://learn.microsoft.com/windows-server/) |
| macOS | [Apple](https://www.apple.com/) | [macOS support](https://support.apple.com/macos) |

The SQL Server image family also uses [Microsoft SQL Server](https://www.microsoft.com/sql-server/).
[Debian](https://www.debian.org/) is an upstream distribution used by desktop
wrapper images; it is distinct from MYKOS's selectable guest recipes.

## Containers, VMs and snapshots

| Project | Role in MYKOS |
| --- | --- |
| [Podman](https://podman.io/) · [command documentation](https://docs.podman.io/) | Rootful container lifecycle, local images, builds, and checkpoint integration |
| [CRIU](https://criu.org/Main_Page) | Native Linux process and RAM checkpoint/restore; the MYKOS CRIU package carries compatibility patches |
| [QEMU](https://www.qemu.org/) · [KVM](https://www.linux-kvm.org/) | Hardware-assisted desktop guests; QEMU supplies VM RAM and device-state capture |
| [Dockur Windows](https://github.com/dockur/windows) | Upstream Windows VM wrapper and installation assets |
| [Dockur macOS](https://github.com/dockur/macos) | Upstream macOS VM wrapper, recovery workflow, and installation assets |
| [QEMUS QEMU wrapper](https://github.com/qemus/qemu) | Upstream QEMU container wrapper used for Ubuntu desktop images |
| [noVNC](https://novnc.com/info.html) | Browser display for desktop VM consoles |
| [Linux kernel](https://www.kernel.org/) · [kernel documentation](https://docs.kernel.org/) | Namespaces, cgroups, networking, storage, and virtualization interfaces |
| [systemd](https://systemd.io/) | Host and guest services, sockets, timers, transient workers, and resource groups |

For MYKOS's own separation of native filesystem previews and desktop RAM
previews, see [Snapshot Preview](DESKTOPS.md#snapshot-preview).

## Image creation and installation

| Project | Role in MYKOS |
| --- | --- |
| [Lorax and livemedia-creator](https://weldr.io/lorax/) | Produce native Linux root filesystem archives |
| [Anaconda installer](https://anaconda-installer.readthedocs.io/en/latest/) | Executes native installation workflows and Kickstart instructions |
| [Pykickstart](https://pykickstart.readthedocs.io/en/latest/) | Kickstart command definitions and validation |
| [libblockdev](https://github.com/storaged-project/libblockdev) | Storage plugins required by the host installer stack |
| [RPM](https://rpm.org/) | Build, install, and verify MYKOS host packages |
| [DNF](https://dnf.readthedocs.io/en/latest/) · [createrepo_c](https://github.com/rpm-software-management/createrepo_c) | Package retrieval, repository synchronization, and RPM metadata |
| [GnuPG](https://www.gnupg.org/) | Release and RPM signing tooling |
| [EPEL](https://docs.fedoraproject.org/en-US/epel/) | Additional Enterprise Linux packages used by optional bootstrap steps |

## Application and browser interface

| Project | Role in MYKOS |
| --- | --- |
| [Python](https://www.python.org/) | API services, collectors, privileged helpers, installer, and build orchestration |
| [FastAPI](https://fastapi.tiangolo.com/) | Web/API routes, dependency handling, and generated API schema |
| [Starlette](https://starlette.dev/) · [Uvicorn](https://uvicorn.dev/) | ASGI web framework infrastructure and application server |
| [Pydantic](https://docs.pydantic.dev/latest/) | Request models and validation |
| [websockets](https://websockets.readthedocs.io/) | WebSocket protocol support |
| [SQLite](https://www.sqlite.org/) | Local application database |
| [Apache HTTP Server](https://httpd.apache.org/) | HTTPS front end, reverse proxy, and repository delivery |
| [xterm.js](https://xtermjs.org/) | Browser terminal rendering |
| [Leaflet](https://leafletjs.com/) | Dashboard location map and markers |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) · [CARTO](https://carto.com/attribution/index.html) | Map data attribution and basemap tiles |
| [Simple Icons](https://simpleicons.org/) · [Devicon](https://devicon.dev/) | Brand icon sources; see the retained [icon credits](THIRD_PARTY_ICONS.txt) |

The exact Python runtime dependency set, including transitive libraries, is
recorded in `services/requirements.txt` in the source tree. That pinned list is
the installation input; this page explains the major components.

## Networking, VPNs and administration

| Project or service | Role in MYKOS |
| --- | --- |
| [Libreswan](https://libreswan.org/) | IPsec/IKE VPN configuration and tunnel status |
| [Cisco Meraki Dashboard API](https://developer.cisco.com/meraki/api-v1/) | Organization/site information and third-party VPN peer integration |
| [Netavark](https://github.com/containers/netavark) · [CNI plugins](https://github.com/containernetworking/plugins) | Podman networking backends, depending on the host configuration |
| [NetworkManager](https://networkmanager.dev/) | Host/guest network configuration and compatibility with managed interfaces |
| [OpenSSH](https://www.openssh.org/) | Remote administration and SSH-based guest access |
| [rsync](https://rsync.samba.org/) | Backup transfer and synchronization |
| [OpenSSL](https://www.openssl.org/) | TLS certificates and cryptographic tooling |
| [Certbot](https://certbot.eff.org/) · [Let's Encrypt](https://letsencrypt.org/) | Optional trusted domain certificate issuance and renewal |
| [firewalld](https://firewalld.org/) · [SELinux Project](https://github.com/SELinuxProject/selinux) | Host firewall and mandatory-access-control infrastructure |
| [dmidecode](https://www.nongnu.org/dmidecode/) | Physical host model, serial number, and BIOS information |

## Compatibility fixes and troubleshooting references

These entries connect specific MYKOS repair areas to upstream source or primary
technical references. The source paths identify where the corresponding MYKOS
implementation lives.

| Repair area | Upstream source or technical reference | MYKOS connection |
| --- | --- | --- |
| Overlay filesystem and inotify checkpoint restore | [AlmaLinux CRIU patch commit](https://git.almalinux.org/rpms/criu/commit/850fe769aab961cf25e420edc39ec3935f136a67) | Credited source of the directory-walk patch adapted in `packaging/criu/overlay-fsnotify.patch`, with a MYKOS overlay-path extension |
| Native cgroup checkpoint compatibility | [Kernel cgroup v2 reference](https://docs.kernel.org/admin-guide/cgroup-v2.html) · [CRIU source](https://github.com/checkpoint-restore/criu) | Background for cgroup layout handling in `services/helpers/mykos_criu_cgroups.py` and the local `4.2.1-subtree-control.patch` |
| Desktop RAM restore and matching virtual devices | [QEMU migration framework](https://www.qemu.org/docs/master/devel/migration/main.html) · [QMP specification](https://www.qemu.org/docs/master/interop/qmp-spec.html) | Saved RAM must match the guest device configuration; implemented in `services/helpers/mykos_vm.py` |
| Installer storage-module failures | [libblockdev source](https://github.com/storaged-project/libblockdev) · [Anaconda troubleshooting](https://anaconda-installer.readthedocs.io/en/latest/) | The EL9 package dependencies and preflight in `packaging/rpm/mykos.spec` and `pos/build_rootfs.py` keep the storage plugin stack available |
| Legacy guest Kickstart compatibility and repository selection | [Pykickstart reference](https://pykickstart.readthedocs.io/en/latest/) · [Lorax documentation](https://weldr.io/lorax/) | `pos/prepare_ks.py` prepares generated installer copies; the checked-in guest recipes remain the source |
| Image builders competing with the running app | [systemd resource controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html) | Low CPU/disk priority in image workers and builder VMs; bounded read-only inventory and last-good UI data handle temporary Podman stalls |
| Older Meraki MX site-only tunnels | [Meraki Dashboard API](https://developer.cisco.com/meraki/api-v1/) · [Libreswan](https://libreswan.org/) | Compatibility handling in `pos/pos` and the VPN helpers retains existing peers and supports site-only routing |
| DHCP across VPN-connected networks | [RFC 2131: DHCP](https://www.rfc-editor.org/rfc/rfc2131) · [Meraki DHCP relay guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Design_and_Configure/Configuration_Guides/DHCP/Configuring_DHCP_Relay) | Design references retained in [Performance by design](ENGINEERING_LESSONS.md) |

## Development, documentation and testing tools

| Tool | Use in the project |
| --- | --- |
| [Git](https://git-scm.com/) · [GitHub](https://github.com/) | Source history, review, and release publication |
| [OpenAI Codex](https://openai.com/codex/) | Assisted development, investigation, documentation, and validation |
| [pytest](https://docs.pytest.org/en/stable/) | Python regression and component tests |
| [Node.js](https://nodejs.org/) | Documentation generation and JavaScript checks |
| [Marked](https://marked.js.org/) | GitHub-flavored Markdown rendered into the bundled HTML guides |
| [Graphviz](https://graphviz.org/) · [Viz.js](https://viz-js.com/) | Diagram layout and generated SVG output |
| [Mermaid](https://mermaid.js.org/) | Editable companion diagram notation stored with generated diagrams |

For exact documentation build inputs and checks, see
[Documentation build](DOCUMENTATION_BUILD.md). For the MYKOS implementation,
start with the [Developer's guide](DEVELOPERS_GUIDE.md) and
[architecture guide](ARCHITECTURE.md).

When adding a dependency or adapting an external fix, add its official project
link, describe its role, and record any patch provenance beside the source.
