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

Business-specific integration is provided by a separately installed local extension.

## Prioritized findings

Business-specific integration is provided by a separately installed local extension.

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

Business-specific integration is provided by a separately installed local extension.

## Implemented optimizations (release 51)

Business-specific integration is provided by a separately installed local extension.

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
