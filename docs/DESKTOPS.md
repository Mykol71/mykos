# Windows, macOS and Ubuntu desktops

[Documentation index](README.md)

MYKOS runs KVM virtual machines inside rootful Podman containers. Windows uses
`docker.io/dockurr/windows`, macOS uses `docker.io/dockurr/macos`, and Ubuntu uses
`docker.io/qemux/qemu`. Upstream instructions:

- <https://github.com/dockur/windows>
- <https://github.com/dockur/macos>
- <https://github.com/qemus/qemu>
- <https://releases.ubuntu.com/>

For new images, use the [unattended image build](IMAGE_BUILDS.md) pipeline.
One command installs and provisions the guest, verifies a clean installed boot,
captures RAM/device state with matching disks, commits the installed payload
under the named local OS image, and leaves one generic base running. The
compressed sparse payload is stored in `/opt/mykos-image/golden.tar.gz`;
external immutable state provides fast reflink-based instance starts.
Manual Finish remains available when updating older installed images.
The memory checkpoint remains paired with its captured disks; the commit updates
the image metadata. The unified build also embeds the compressed installed disk/state payload.
A new instance restores that checkpoint rather than performing a guest boot.
The loader verifies the saved CPU instruction pointer and page-table register
before resuming the instance. The pinned image, CPU profile, RAM and vCPU count
must remain unchanged; memory checkpoints are intended for this same host.

Live capture requires a reflink-capable filesystem such as XFS with reflink
or Btrfs. A memory image contains private guest data: its directory is root-only
and it is excluded from RPMs and installer bundles. The capture saves Windows
and macOS at the actual desktop/login/session state present during Finish.
Existing network connections may need to reconnect after restoring a clone.

Each new instance uses the checkpoint once. Later explicit stop/start operations
boot its current disks normally, preventing stale RAM from being applied to
changed disks. `podman commit` alone does not save this QEMU execution state.

<!-- contents:start -->
**Contents**

- [Requirements and networking](#requirements-and-networking)
- [Text admin workflow](#text-admin-workflow)
- [Web and API](#web-and-api)
- [Storage, backups and installation](#storage-backups-and-installation)
- [Persistent lifecycle and container snapshots](#persistent-lifecycle-and-container-snapshots)
- [Ubuntu networking packages](#ubuntu-networking-packages)
- [Permanent container deletion](#permanent-container-deletion)
- [Public HTTPS browser consoles](#public-https-browser-consoles)
  - [Native snapshot compatibility](#native-snapshot-compatibility)
  - [Host Add VPN](#host-add-vpn)
- [Current memory policy](#current-memory-policy)

<!-- contents:end -->

## Requirements and networking

Use a Linux x86-64 host with usable `/dev/kvm` and `/dev/net/tun`, sufficient
memory for each running guest (2048 MiB policy and 2 vCPUs), and at least 32 GiB of
free storage for creation. Guest disks default to 64 GiB sparse disks. macOS
also requires AVX2. Guest installation and licensing remain the operator's
responsibility. Supported current builds run unattended; legacy Windows releases
retain their manual setup path.

Each image and instance reserves its own browser-console TCP port in
18006–19005 and native desktop TCP/UDP port in 23000–23999. Reservations persist
while stopped. Existing Podman mappings and active TCP/UDP sockets are checked
before allocating ports. Guest creation does not alter firewall or router rules.
Production requires explicit approval to permit these ranges from its local
`192.168.222.0/24` network. The code installer does not open them.
On another host, explicitly allow the chosen administrative network before
opening the browser console or native login. For example, in its active
firewalld zone (replace the source network for that host):

```bash
sudo firewall-cmd --add-rich-rule='rule family="ipv4" source address="192.168.222.0/24" port port="18006-19005" protocol="tcp" accept'
sudo firewall-cmd --add-rich-rule='rule family="ipv4" source address="192.168.222.0/24" port port="23000-23999" protocol="tcp" accept'
sudo firewall-cmd --add-rich-rule='rule family="ipv4" source address="192.168.222.0/24" port port="23000-23999" protocol="udp" accept'
```

Use the same commands with `--permanent` to retain the rules across restarts.
These examples use the default zone; specify `--zone` if the LAN interface
belongs to a different zone.

By default, ports bind to the host's route-selected LAN IPv4 address, and links
use that address. Optional `/etc/mykos/config.json` settings `desktop_bind` and
`desktop_host` are IPv4 addresses for binding and connecting respectively.
Only use reachable addresses on a trusted administrative network; the upstream
browser/VNC consoles provide interactive machine control. Do not expose them
to the public Internet without a separately configured access gateway.

## Text admin workflow

In the POS menu:

- `c`: create an OS image (RHEL keeps its existing build workflow).
- `v`: list desktop image/instance metadata and assigned console/login URLs.
- `f`: finish/capture an installed desktop image, then stopping and committing it.
- `t`: resume or stop a desktop image.
- `s`: select the eight-digit ID and OS.
- `2`: start/stop that code's instance, retaining its disk and ports.
- `3`: show desktop connection URLs; native RHEL still opens its root shell.

Equivalent commands from either production toolkit's `pos` directory:

```bash
sudo python3 ./vm create win11
sudo python3 ./vm create wns2025
sudo python3 ./vm create mac15
sudo python3 ./vm create ubuntu24.04
# Create only the images you need; each is a running VM consuming RAM.
sudo python3 ./vm list
```

These commands now run unattended for current Windows/Windows Server, macOS,
and Ubuntu versions. No separate Finish step is required. Generated credentials
are root-only in `/var/lib/mykos/image-builds/OS/credentials.json`. Builds and
runtime VMs use 2 GiB RAM; Windows/Ubuntu use 12 GiB paging. For details,
resuming a failed build and API operations, see [Image builds](IMAGE_BUILDS.md).

For existing images installed manually, Finish and compatibility commands remain:

Update an existing installed image or start an ID-specific instance:

```bash
# Existing Windows images require one restart with migratable CPU settings:
sudo python3 ./vm checkpoint-enable win11
sudo python3 ./vm prepare win11
# Upgrade an old disk-only macOS capture without restarting its guest:
sudo python3 ./vm prepare mac15
# Explicitly replace an already prepared memory checkpoint after changes:
# sudo python3 ./vm prepare win11 --replace
sudo python3 ./vm start 01234567 win11
sudo python3 ./vm info 01234567
sudo python3 ./vm stop 01234567
sudo python3 ./vm start 01234567 win11
```

Supported names: `win11`, `win10`, `win8e`, `win7u`, `winxp`, `winme`, `win2k`,
`win98`, `wns2025`, `wns2022`, `wns2019`, `wns2016`, `mac15`, `mac14`, `mac13`,
`mac12`, `ubuntu20.04`, `ubuntu22.04`, `ubuntu24.04`.

Do not mark an installer or recovery screen as a completed guest. Images
become available to web staging after capture, including while stopped. Capture
requires a running image; successful capture stops and commits it. A failed capture
resumes the guest and preserves any previous prepared snapshot. Successful
replacement deletes superseded `.golden-previous-*` snapshots after validating
the newly published checkpoint. Only the latest image RAM/disk checkpoint
is retained for starting new instances. Each instance discards its copied RAM
checkpoint after a successful first restore; later starts use its current disks.
New Windows images use `CPU_MODEL=host`, `migratable=on,-invtsc` and `HV=N`
with KVM acceleration. The `checkpoint-enable` operation applies that profile
to existing Windows images by restarting their wrapper on the same disks;
it retains the stopped original container as `*.before-checkpoint-*` and rolls
back if launching the replacement fails. Never start that retained container
while its replacement is running: they reference the same persistent disk.
Use `resume` to start a retained image after an intentional shutdown.

## Web and API

The green console-log icon opens that instance's saved `console_url`. The blue
login icon downloads a Windows `.rdp` connection file, or opens the macOS/Ubuntu
`vnc://` endpoint. Guest services must be enabled and reachable. Windows 98, ME,
and 2000 have no built-in RDP server; use their browser console. No guest
credentials are embedded in URLs or RDP files. Desktop access requires an
administrator account; Linux shell/VPN operations are not applied to VM wrappers.

The pane ID/Ver plus action starts or resumes an instance. The red trash icon
stops it with a two-minute graceful timeout; it does not delete its disk.

API-key clients with admin scope can retrieve endpoints using
`GET /mykos/api/v2/nodes/{code}/desktop` and download the RDP file from
`GET /mykos/api/v2/nodes/{code}/desktop/rdp`. Existing staging and stop APIs
dispatch to the desktop worker for these OS names.

## Storage, backups and installation

Both toolkit folders use the canonical private registry under
`/usr/local/share/mykos/pos/.vm/`. Guest storage, golden disks and Windows
environment files live under `/home/mykos-vms/`, on production's larger home
filesystem. Set `desktop_storage` in `/etc/mykos/config.json` before creating
images to choose another absolute directory. Do not change this setting
after creation without relocating the storage while all guests are stopped.
Do not copy live guest disks for backups. Shut down the relevant guests cleanly
and back up both directories together with the POS metadata files. Routine code
installation preserves these hidden runtime files. Installer code snapshots
are not a backup of guest storage.

Install code changes using the usual installer and restart the web service:

```bash
sudo python3 /home/mgreen/mykos/bin/mykos-install install --public-origin https://mykol.com
sudo python3 /home/mgreen/mykos/bin/mykos-install verify
sudo systemctl restart mykos-web.service
```

Installation does not create guests or accept OS license agreements. Start a
chosen image explicitly through the menu, complete its installer, capture
it, and test one instance before using it for production workloads.

For initial desktop host setup, the installer also provides an explicit network
option. It verifies KVM access and storage capacity, creates the private storage
directory, snapshots firewalld configuration, and applies only source-restricted
runtime and persistent desktop rules. It never opens these ports by default:

```bash
sudo python3 /home/mgreen/mykos/bin/mykos-install install --public-origin https://mykol.com --desktop-network 192.168.222.0/24
```

Preview the plan by adding `--dry-run`. The option is rejected for actual
`--root` staging installs so tests cannot change the host firewall.


## Persistent lifecycle and container snapshots

Start and Stop retain each container's current writable disks, identity and desktop
ports. New native RedHat/Linux containers no longer use `--rm`; the Podman writable
layer persists, and Pipework reattaches the saved eth1 MAC on restart. Mounted VPN
and backup data remain in place. Existing legacy `--rm` containers are protected:
MYKOS refuses to stop them through these controls until they have been deliberately
converted, because stopping them would destroy their writable layer.

Snapshot uses QEMU RAM/device migration plus matching disk copies for Windows,
Windows Server, macOS and VM-based Ubuntu. Native Linux uses CRIU with
`--leave-running`, captures its writable layer and mounted data at post-dump while
source processes are frozen, and then resumes them. CRIU, the OCI runtime, kernel
and workload must support checkpointing. Unsupported workloads fail explicitly;
MYKOS does not publish a disk-only snapshot as a successful memory capture.

The installer creates a private `snapshots` directory under `desktop_storage`
(default `/home/mykos-vms/snapshots`). Each code has timestamp/random-ID directories
containing private metadata and captured state. Intentional snapshots survive RPM
updates and automatic image/checkpoint cleanup. Required runtime images are pinned.
Native capture adds a scoped action callback to `/etc/criu/runc.conf`, preserving
existing lines; the callback is inert unless MYKOS has a matching live request for
that process ID. Runtimes that do not support this callback fail safely. Native
restores require the original persistent container, image, and mount configuration.
Shared mounted data may have external writers: those writers must be quiesced by
the operator for application-level consistency across containers.

The Snapshots pane lists one row per container with its saved states in a dropdown.
It hides when empty; the footer leaf override shows two sample rows whose dropdowns
and restore actions are disabled. Restore requires confirmation, retains temporary
recovery files until verification, and keeps the intentional snapshot available.
If recovery itself fails, the job reports the retained recovery location for inspection.

CLI equivalents:

```bash
sudo mykos-linux start 01234567
sudo mykos-linux stop 01234567
sudo mykos-snapshot capture 01234567
sudo mykos-snapshot list
sudo mykos-snapshot list 01234567
sudo mykos-snapshot restore 01234567 TIMESTAMP-SNAPSHOT_ID
```


Each snapshot row also has a red trash icon beside Restore. Delete requires an
explicit confirmation, permanently removes only the selected saved state, and
leaves the current container and its disks running unchanged. The private snapshot
image reference is untagged only after deleting the saved files; other images and
snapshots retain their references. CLI: `sudo mykos-snapshot delete CODE SNAPSHOT_ID`.
The OS column shows labels such as macOS 15; Taken follows the chosen saved state.

Snapshots appears immediately after the OS container panes, above rsync Backups.

Host and native Linux root terminals open in a centered popup dialog. Close or Escape disconnects the terminal; the dashboard continues refreshing behind it. VM desktop console/login URLs retain their existing behavior.

Ubuntu image builds use a migratable CPU profile for live-state capture.
For an older Ubuntu build blocked by `invtsc`, run `./vm checkpoint-enable ubuntu22.04`
from the installed `pos` directory (substitute the selected Ubuntu version).
The command asks before restarting the build, keeps its installed disk and previous
container, and applies `CPU_MODEL=host` with `CPU_FLAGS=migratable=on,-invtsc`.
After Ubuntu is running normally again, finish the image capture.

## Ubuntu networking packages

Before capturing an Ubuntu image, run this in a terminal **inside Ubuntu**:

```bash
sudo apt-get update
sudo apt-get install -y isc-dhcp-client iproute2 net-tools
```

`isc-dhcp-client` supplies `dhclient`. Its runtime dependencies are resolved by
APT. Ubuntu 24.04 supplies it in `universe`; enable that repository first if APT
reports no installation candidate. The packaged `pos/ubuntu-deps` script performs
the same setup, validates that it is running inside Ubuntu, and checks availability.
Copy it into the guest and run it with `sudo bash ubuntu-deps` if using the script.
The text admin image creation/Finish steps also display the direct APT command.

MYKOS's RHEL host installs its own `dhcp-client` for pipework. Ubuntu runs as a VM
inside a QEMU container: `podman exec` operates on that wrapper, not Ubuntu.
These guest packages do not configure or replace the VM's networking.

For an already captured image, resume its builder with `sudo python3.9 ./vm resume ubuntu22.04`,
install the packages through its console, then recapture with
`sudo python3.9 ./vm prepare ubuntu22.04 --replace`. Substitute the chosen Ubuntu
version. Updating an RPM does not modify an existing guest disk; existing instances
need the same guest package installation separately. Saved snapshots retain their
original software state.

Package references: <https://packages.ubuntu.com/en/jammy/isc-dhcp-client>
and <https://packages.ubuntu.com/noble/amd64/isc-dhcp-client>.

## Permanent container deletion

The red trash icon is separate from start, stop, and snapshot. Type the container
ID to permanently remove the selected container and its own MYKOS-managed disks,
writable layer and VPN settings. Images, rsync backups, external/shared mounts and
intentional snapshots stay in place. Shared storage or unsafe paths block deletion
before shutdown. Only administrators can use this operation; API details are in
[API.md](API.md#delete-a-container-and-its-private-data). Stop keeps saved data.

## Public HTTPS browser consoles

Image-build and running-container browser console links use
`/mykos/console/<OS-or-ID>/` on the dashboard's public HTTPS origin. Assets and
WebSocket traffic are forwarded by MYKOS to the verified container's published
console port. A current administrator browser session is required for every
asset and connection; revocation/logout is checked during an open connection.
NoVNC settings force the public hostname, TLS and the per-image/per-container
WebSocket path, overriding remembered LAN/dev connection settings.

Apache forwards console WebSocket upgrades to MYKOS, where authentication stays
in force. Never configure an unauthenticated public proxy straight to VM ports.
RPM upgrades and `mykos-setup` install the WebSocket rule in existing MYKOS TLS
vhosts, validate Apache configuration and reload the active service. Configuration
backups are saved under `/var/backups/mykos-installer/apache-consoles-*`.
New vhosts should use `services/apache-mykos.conf.example`, including the console
rewrite block. Apache needs proxy, proxy_http, proxy_wstunnel, rewrite and headers.

The API reports the public proxy path as `console_url`; `direct_console_url`
retains the LAN endpoint for diagnostics. Native RDP/VNC login addresses still
require LAN/VPN access; browser consoles need only public HTTPS access and a
MYKOS admin session. Guests and their assigned ports are not restarted or changed.

### Native snapshot compatibility

MYKOS uses a private CRIU RPC relay for each native snapshot. The filesystem copy
runs at the post-dump notification before CRIU resumes source tasks. It does not
install shell action scripts in `/etc/criu/runc.conf`; upgrades remove only the
old MYKOS hook line and preserve other operator settings.

RHEL CRIU 3.x cannot capture every systemd workload. MYKOS checks for BPF program
descriptors and nested hostname namespaces before seizing processes. Unsupported
workloads return a clear error and remain running; no disk-only fallback is
published as a memory snapshot. Privileged containers may also expose host kernel
mounts that CRIU cannot capture. Do not remove arbitrary sandbox protections to
work around these restrictions. A checkpoint-compatible image/runtime is required.

### Host Add VPN

The host VPN plus action opens the same `pos` Add VPN workflow as native
containers. Enter the remote name, public address, IPv4 network without `/24`,
and PSK. Confirm creation, then choose whether to push the peer to Meraki using
credentials from `/etc/mykos/pos.env`. The workflow displays the same remote-peer
setup instructions and VPN policy as container setup. Host connections are stored
under `/etc/ipsec.d/mykos-NAME.*`; existing names are rejected. New host tunnels
are loaded individually, preserving existing tunnels. After creation is confirmed, missing MYKOS-only
config/secret includes are appended to the global files with private backups.

## Current memory policy

All finished VM images and their instances use 2048 MiB RAM. Unattended macOS
installation temporarily requires 4096 MiB for Apple’s second-stage temporary
filesystem; it returns to 2048 MiB before the installed-boot check and capture.
The image creation menu records the allocation so live state captures remain compatible. Existing
4096 MiB captures require recapturing at 2048 MiB before new instances can resume
that state. Existing instances can be cold-resized through `vm memory-configure`;
see BALLOONING.md for recovery, driver and API details.

The Windows/Ubuntu pagefile or swap target is 12 GiB, configured inside the
installed guest before capture. The Ubuntu helper rejects the live installer;
finish installation, boot the installed OS, apply setup, reboot and verify first.
macOS swap stays kernel-managed because a fixed total is not exposed by the
stock OS. Its RAM allocation is 2048 MiB. Windows 11 at this allocation is below
Microsoft's supported minimum.
