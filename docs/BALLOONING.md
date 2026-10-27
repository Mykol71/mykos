# Guest ballooning

Native RedHat containers share the host kernel and do not use VM ballooning.
The operator-selected VM policy is now **2048 MiB RAM per guest**. Removing a
Podman memory limit alone does not reclaim guest RAM. Existing 4096 MiB RAM
captures cannot resume at 2048 MiB; published images must be recaptured at the new
size before creating instances from their RAM state.

Inspect driver support from the installed pos directory:

```bash
sudo ./vm memory 22222222
```

The operation enables five-second guest statistics polling. Statistics may take
five seconds to arrive. Windows needs a working VirtIO balloon driver/statistics
service; macOS live balloon adjustment remains unverified. Unknown/unsupported
statistics do not cause automatic RAM changes. Live requests are constrained to
the 2048 MiB policy, at most 128 MiB per reduction and at least 1 GiB guest memory
headroom. A larger reduction therefore requires a cold restart.

Cold-resize an existing Windows, Ubuntu or macOS instance, confirming its exact ID:

```bash
sudo ./vm memory-configure 22222222 --target-mib 2048 --confirm 22222222
```

The operation verifies its live storage mount, quiesces disk writes for a reflink
recovery copy, then restarts the wrapper against its current disks at 2048 MiB.
It preserves ports, OS images, intentional snapshots and a stopped recovery
wrapper. It rejects paused VMs and active migrations. Recovery copies are under
the instance's `.memory-recovery-*` directory. Windows instances use manual
installation mode to avoid unattended reinstallation during restart.

Admin-key API controls:

- `GET /mykos/api/v2/nodes/{id}/memory` reports configured RAM and driver status.
- `PUT .../memory` with `{"target_mib":2048}` requests a guarded live balloon target.
- `PUT .../memory/allocation` with `{"target_mib":2048,"confirmation":"ID"}` queues a cold restart.

The pagefile/swap provisioning target is **12 GiB**. New supported Windows builds
receive an OEM PowerShell setup script. Ubuntu creation/capture instructions
include the fixed setup download `/mykos/image-setup/ubuntu.py`; run it inside the
installed Ubuntu OS, reboot and verify swap before capture. It rejects live
installers and RAM-backed roots and checks free disk space. These settings are
not proof that an unfinished or unavailable guest has applied its pagefile/swap.
Stock macOS manages swap in its kernel and does not expose a fixed-total setting.
Its configured RAM is still 2048 MiB. Windows 11 at 2048 MiB is below Microsoft's
published supported minimum; installation success is not guaranteed.

## Native snapshot compatibility

Before a native live snapshot, MYKOS removes `/dev/hugepages` (hugetlbfs) and
`/sys/kernel/config` (configfs) only when they are empty mounts and the kernel permits an ordinary unmount. This includes
private mount copies held by services in the same container PID namespace. Host
and unrelated container namespaces are excluded. It then masks
the corresponding systemd mount units in that container so the optional mount stays absent across
restarts. Nonempty or busy mounts stop capture; no force/lazy unmount is used.
Existing workload services and the container stay running.

The per-capture CRIU RPC request supplies the D-Bus policy-directory inode scan hint
for overlay-backed file watches, including NetworkManager plugin/firmware and polkit policy directories. Global CRIU configuration remains unchanged.

A headless native container may also run systemd-logind with an open input-event
device. CRIU cannot save these device handles. MYKOS reports the process/device
before capture instead of stopping services automatically. An operator must
review active sessions and approve stopping/masking that service if appropriate.
