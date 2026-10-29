# Host-side monitoring review

[Documentation index](README.md)

Reviewed collectors, web/API handlers, native/VM lifecycle, snapshots, POS menu,
pipework, scheduled commands, backup helpers, repository synchronization, and WSL
VPN scripts. Existing container state was inspected through host Podman metadata;
this review did not execute diagnostic commands inside guests.

<!-- contents:start -->
**Contents**

- [Production observations](#production-observations)
- [Prioritized findings](#prioritized-findings)
- [VPN replacement requires protocol validation](#vpn-replacement-requires-protocol-validation)
- [Calls that remain intentional](#calls-that-remain-intentional)
- [Changes completed with this review](#changes-completed-with-this-review)
- [Implemented optimizations (release 51)](#implemented-optimizations-release-51)
- [Memory and networking follow-up](#memory-and-networking-follow-up)

<!-- contents:end -->

## Production observations

Five managed containers were running: one native Linux container and four desktop
VM wrappers. Last observed basic/details/extended collector durations were about
1.9/4.2/9.8 seconds. Those are single observed runs, not benchmark medians.

Before application-monitoring removal, configured 15/30/30-second intervals imply
up to 56 container execs/minute for that workload. OnUnitInactiveSec waits for the
previous run to finish, so actual rates are lower. Removing RTI polling eliminates
12 of those nominal calls/minute, leaving up to 44. These counts estimate command
frequency, not CPU savings or guaranteed latency improvement.

## Prioritized findings

| Priority | Location | Recommendation |
| --- | --- | --- |
| High | collect-nodes.py, collect-extended.py | Skip Linux VPN and eth1 queries for Windows/macOS/VM Ubuntu wrappers. After RTI removal, these still account for up to 32 unnecessary execs/minute with four VMs. Wrapper state is not guest state. |
| High | All three collectors | Collect native VPN status once and publish a shared timestamped result. Do not query each tunnel independently in every collector. |
| High | collect-details.py | Use image labels, selected OS metadata, and cached image tags for OS identity. Avoid repeated cat/cp of os-release and per-container image inspection. Preserve immutable image IDs for correctness and readable names for display. |
| High | collect-extended.py | Batch Podman inspect and non-streaming stats rather than spawning each command per container. StartedAt already exists in inspect data. |
| Medium | collect-extended.py, mykos_linux.py, pos/pos | Read IP/MAC/interface existence with host iproute tools in the selected network namespace. Podman inspect alone does not reliably describe pipework eth1. Guest OS binaries are unnecessary for these checks. |
| Medium | Native lifecycle, pos/pos | Replace filesystem existence/permission checks with trusted host metadata or mount-aware file access. Preserve bind mounts, symlink boundaries, and user namespace ownership mappings. |
| Medium | mykos-stage-pos, backup/menu helpers | Legacy staging still knows RTI paths/services and backup types. Move these into explicit application adapters; the generic virtualization core must not assume their presence. |
| Lower | repo/bin/sync_repos | Keep distribution-specific sync workers isolated. Build reusable worker images or use a single worker invocation per job instead of repeated setup execs; do not assume host yum tools can replace every release-specific workflow. |

## VPN replacement requires protocol validation

Libreswan supports a Unix control socket and status queries selecting that socket.
A host-side client could query the container daemon without podman exec, provided
the installed client and daemon protocol versions are compatible and socket paths
are resolved safely. Validate this before switching. Network-namespace XFRM state
is useful for kernel counters, but SA counts alone are not equivalent to named
Libreswan connection state, especially during rekeying.

References:
- https://libreswan.org/man/ipsec-status.8.html
- https://libreswan.org/man/ipsec-whack.8.html
- https://docs.podman.io/en/v3.4.2/markdown/podman-stats.1.html
- https://www.man7.org/linux/man-pages/man1/nsenter.1.html

## Calls that remain intentional

Interactive root terminals, explicitly scheduled guest shell/PowerShell commands,
and requested in-guest service/application actions need guest execution. Replacing
those with namespace entry or chroot merely changes the mechanism. Keep them out
of periodic polling. Image-build scripts and guest agents are separate from
host-side monitoring.

QEMU monitor access, balloon statistics, VM disk/memory capture, Podman lifecycle,
host graphs, snapshot-file management and backup filesystem listings already use
host interfaces. QEMU monitor access does not run a guest command. The QEMU guest
agent executes inside Windows only for explicit scheduled PowerShell commands.

## Changes completed with this review

- Removed RTI/POS application probing from both collectors, GUI/text detail views,
  current and stale monitoring responses, and the RTI-specific monitoring route.
- Kept generic CPU/memory, OS, network, VPN, uptime and snapshot information.
- Corrected stage image selection to prefer the readable image name over ImageID.
- Preferred native OS tags over snapshot pin tags when resolving legacy hashes.
- Resolved native snapshot OS from host-side image tags when its stored name is a
  hash. OS detection for this fix does not execute commands inside the guest.

## Implemented optimizations (release 51)

- Basic, details and extended monitoring no longer execute guest programs.
- Each collector batches container inspection. Extended batches all running
  container statistics and uses inspected StartedAt timestamps.
- Basic queries native Libreswan through the host client and selected container's
  control socket once per interval. Details/extended reuse timestamped results,
  bound to container ID/start time and expired after 60 seconds. VM wrappers are
  excluded. The production client/control protocol was verified; unsupported or
  inaccessible sockets report unknown, without falling back to guest execution.
- Native IP/MAC/interface checks use host iproute tools in the selected network
  namespace, with process lifetime checks. They do not enter its mount namespace
  or execute its binaries. Pipework remains in place pending a separate migration.
- OS/image identity uses batched host image metadata, readable tags and explicit
  VM labels. Immutable image IDs remain available for correctness. Native builds
  label their selected OS. This intentionally reports image identity, not an OS
  upgraded after the instance was created.
- Text-menu native staging now uses the same helper as web/API staging. Redundant
  guest filesystem tests and blanket ownership/SSH-key permission changes were
  removed. No replacement host chmod/chown is needed by generic staging; bind
  mounts retain their existing ownership and data.
- Generic staging does not assume an application stack or a running VPN service.
  Legacy restore mounts/services and backup classification are isolated in
  mykos_profiles.py, enabled only by root configuration
  `"application_adapter": "rti"`. The default is `none`; enabling an adapter is
  an explicit operator choice. Existing backup accounts/data are preserved.
- Repo sync uses one isolated worker invocation per release, an exclusive host
  lock, and each release's own tools. It no longer starts privileged workers,
  executes a CLI command for every repo or replaces another worker's container.
  Installation of sync tools happens within that single job. Large network syncs
  have not been triggered as part of validation.
- Interactive terminals, requested service actions, image build preparation and
  explicitly scheduled guest shell/PowerShell commands remain intentional.

A production shadow run on the same five managed instances measured approximately
0.115 / 0.099 / 0.127 seconds for basic/details/extended. These are observed runs,
not controlled benchmark medians or guaranteed latency. The native LAN IP was
preserved and all OS families remained correctly classified.

## Memory and networking follow-up

Native RedHat containers share host memory rather than reserving 4 GiB. VMs use a 2048 MiB allocation through a guarded cold
restart; old memory captures cannot be restored at a different RAM size. Guest
pagefiles/swap can help at smaller allocations but do not themselves lower the
QEMU allocation. The operator-selected policy is 2048 MiB for all VMs and a 12 GiB pagefile/swap target where configurable. Windows 11 is below its published supported minimum; macOS keeps kernel-managed swap.

Production currently has both a Podman bridge address and a pipework LAN address.
Replacing pipework requires a Podman macvlan/ipvlan LAN network, appropriate IPAM,
and host-to-container connectivity. Removing eth1 alone would remove LAN/VPN
reachability. No live networking migration is included in this release.
