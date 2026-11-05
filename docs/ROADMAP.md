# MYKOS Public Roadmap

Updated November 5, 2026. MYKOS is preparing a focused first stable public release. The priorities are reliable installation, clear support boundaries and tested everyday operations. Planned capabilities below are future work, with delivery based on validation rather than promised dates.

## Preparing the first stable release

The clean-install pilot starts with RHEL 9 and assesses RHEL 10 separately. It will test installation from signed RPM repositories, firstboot, administrator setup, login, dashboard and documentation access, reboot behavior, a representative guest lifecycle, upgrade and removal. A platform will be advertised for the stable launch only after its required installation and runtime checks pass.

The next public package is being reduced to verified capabilities. Existing published release archives remain unchanged.

## Features deferred from the initial public scope

These capabilities remain on the roadmap. Deferral does not mean that they have been abandoned.

| Capability | Planned direction | Required before broader availability |
| --- | --- | --- |
| PXE desktop provisioning | Keep provisioning in the separate RTI business package rather than public core. Continue Windows desktop provisioning and recovery work there. | Validate media, target confirmation, interrupted installs, firmware boot and complete physical desktop installation. |
| Shared guest VPN gateways | Revisit one gateway serving multiple guests after complete traffic and recovery validation. | Verify site access, internet and DNS behavior, multiple guests, failure isolation, reconnect and clean removal. |
| Host-shared VPN gateways | Revisit shared routing for the host and guests after management and recovery checks. | Preserve host management access and verify guest routing, DNS, fail-closed behavior and rollback. |
| Shared site-only VPN for older routers | Develop compatible site access without forcing internet traffic through the remote site. | Validate router firmware, traffic selectors, multiple guests, reconnect and safe removal. |
| Other VPN and LAN attachment modes | Validate manual host VPNs, VM-agent VPNs, direct full-internet VPNs and LAN-DHCP/macvlan separately. | Test the actual topology and document prerequisites, failure handling and recovery before public inclusion. |

## UI two-factor authentication

Planned: add two-factor authentication (2FA) to MYKOS UI sign-in, including enrollment, recovery codes and a secure account recovery process. Validate sign-in, recovery and session behavior before release.

## Peer connectivity across networks

The current peer design requires directly reachable, distinct IPv4 host addresses. Different routed networks can meet that requirement. Automatic NAT traversal and public-to-private endpoint mapping are outside the initial validated topology.

Future work will assess internet-facing peer connections, servers behind NAT, endpoint changes and overlapping private networks. Broader support requires repeatable pairing, identity and certificate checks, firewall guidance, reconnect and data-path tests across the intended topology.

## Provisioning through business extensions

The public core and optional business packages have separate scopes. PXE provisioning will remain in the RTI package while its deployment flow is validated. The roadmap includes DHCP-to-network-boot handoff, routed client access, Windows media handling and installation recovery.

Provisioning remains opt-in. Physical disk erasure must require explicit target selection and confirmation.

## Reliability and platform validation

The pilot will turn fresh-install findings into tested fixes and clearer installation instructions. Work includes complete host dependency resolution, representative native container and VM operations, scoped backup and restore checks, responsive status reporting and upgrade behavior that preserves customer resources.

RHEL 10 fresh-install readiness includes validation of the native checkpoint dependency. Staged package checks alone do not establish full host or guest compatibility.

## Release criteria

A stable release needs signed and verifiable packages, a successful clean-install path for every advertised host platform, documented support limits, relevant regression and integration checks, and a tested rollback path. Optional features will enter the public scope when their own validation criteria pass.

The roadmap will identify capabilities as planned, in validation or released as work progresses.
