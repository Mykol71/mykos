# Managed container networking

Both dev and production use the rootful Podman bridge `mykos-lan` with private NAT, subnet `192.168.22.0/24`, gateway `192.168.22.1`, and container addresses `.2` through `.254`. The same subnet on two separate hosts does not connect those networks together. The host LAN addresses remain unchanged. This works through Ethernet or Wi-Fi.

Set `native_network_driver` to `bridge`, `native_network` to `mykos-lan`, and `native_network_subnet` to `192.168.22.0/24` in `/etc/mykos/config.json`. Initialize with `sudo /usr/local/sbin/mykos-network-setup` or `mykos-setup --native-network`. A new network is refused if the subnet overlaps an existing host route. Existing mismatched networks are left unchanged and must be replaced explicitly while unused.

New native Linux instances, native image build containers, and Windows/macOS/Ubuntu VM wrappers use this network. Each gets a fixed `--ip` address and deterministic MAC, recorded in its Podman configuration and MYKOS labels. A shared lifecycle lock serializes assignment; stopped containers retain their address claims. Addresses exclude the gateway/network/broadcast, and a full pool fails before creation. Stop/start preserves the assigned address. Deleting the container releases its claim. VM-internal guest NICs retain their QEMU-managed networking; the displayed Podman NIC belongs to the VM wrapper.

There is no pipework or guest dhclient dependency. NAT guests do not have individual `192.168.222.x` LAN addresses. Direct inbound LAN services need explicit publishing or routing; MYKOS terminals, guest-agent commands and HTTP desktops remain server-proxied. IPsec tunnel configuration and NAT-T need validation for the chosen peer/subnets; existing LAN-specific VPN selectors are not automatically rewritten.

Optional `native_network_driver: macvlan` retains LAN DHCP mode on a wired parent selected with `network_device`. CNI uses `cni-dhcp.socket`; Netavark uses `netavark-dhcp-proxy.socket` for that optional mode. Private bridge mode uses neither. The primary interface is eth0, selected by labels; legacy eth1 monitoring and `eth1_ipv4` API aliases remain for compatibility with old instances. API clients should use `network.ipv4` and `network.interface`.

Existing running containers are not silently rewired. Recreate legacy pipework or older-network containers after preserving any needed data. New instances use the configured bridge.

## Linux root terminals

Native RedHat terminal actions execute `/bin/su - root` through Podman. Ubuntu VM terminal actions connect directly to a private QEMU serial Unix socket and its guest ttyS0 service running `/bin/su - root`. They use no SSH, LAN IP, wrapper shell, or public console port. Only one direct console may be open per Ubuntu instance. Browser graphical console remains available through the separate console-log icon.

Run the Ubuntu image setup script inside the installed guest before capture; it configures both swap and the direct serial root-console service. Use `sudo python3 /tmp/mykos-ubuntu-setup.py --console-only` to configure only the terminal. The service grants root to holders of the VM serial console, matching the admin-only native root terminal. Existing images need this setup and the updated QEMU serial configuration before the new terminal works.

## Windows PowerShell action

The Windows/Windows Server login icon opens an admin PowerShell command console through QEMU Guest Agent, without RDP or a guest network connection. Install and start the Windows QEMU Guest Agent before capture. Each entered line runs separately under that service account, with a 120-second timeout and bounded output; interactive applications and persistent PowerShell session variables are not supported. The console-log icon remains the graphical browser desktop; existing RDP API links remain available for clients that still need them.

## macOS shell action

The macOS login icon opens SSH through the VM's private Podman bridge address, prompts for the macOS username/password, and confirms the guest host key. Enable Remote Login for the intended user in System Settings > General > Sharing before capture. The account's normal shell is opened; no SSH key or password is stored by MYKOS. No additional host-public SSH ports are opened. The console-log icon opens the HTTP graphical desktop through the existing Apache-authenticated browser-console proxy, as it does for Windows and Ubuntu.


## Native OS minor versions

Native image builds store full VERSION_ID (for example 8.10 or 9.8) in the `io.mykos.os.version` image label. For older images without that label, the collector reads os-release from a host-mounted immutable image once and stores a root-owned cache keyed by its image digest under `/var/cache/mykos/image-os`. Future polls use the cache, never guest execution. New image content gets a new cache key; an unavailable version is retried after one hour. Purge removes this cache. The API and container Version column use the same full image version.


### Meraki peer updates

The optional Meraki upload first downloads the organization's complete third-party
VPN peer list, then appends the new peer and sends the combined list. Existing
peer objects and their settings are preserved. Failed downloads or invalid JSON
stop the update. An identical named peer is a no-op; a same-name peer with different
settings is rejected rather than overwritten. New peers do not reuse a fixed peer
ID. API headers and downloaded configuration use private temporary files which
are removed when the operation ends. Avoid simultaneous changes in Dashboard or
other API clients during an update: this endpoint replaces the complete peer list.

## VPN modes and persistence

New native Linux VPN setup offers independent guest tunnels or one shared default
gateway. Independent mode supports multiple distinct remote site subnets, multiple
guests to one site, and one remote site per guest. The first successfully connected
tunnel owns that guest's default traffic. IKEv2 liveness checks run every 30 seconds with a 45-second retransmit timeout;
host fallback waits until the local full-tunnel SA is actually removed, rather
than assuming a peer is down from one lost packet. Rekeying does not change ownership; when
the primary connection is removed, the earliest remaining established connection
is promoted. Internet traffic and non-loopback DNS use that selected full tunnel.
The DNS resolver is `8.8.8.8`. Generated configurations explicitly use `pfs=no` to match the existing Meraki
child-SA policy. Default stanzas are rendered independently because overriding
`also=` values is rejected by the tested Libreswan 4.x parser. See the
[Libreswan configuration reference](https://libreswan.org/man/ipsec.conf.5.html).
A separate default CHILD_SA uses `0.0.0.0/0`; a site
SA alone is not evidence of working full-tunnel internet access.

Shared mode supports either the MYKOS host or one native Linux guest as gateway.
It routes all MYKOS guest types through that gateway and routes host internet
traffic through it only after a matching full-tunnel SA is confirmed. Local host
management replies and outer IKE/NAT-T/ESP transport are exempt. During an outage,
protected guest traffic remains blocked; ordinary host WAN internet is restored.
Only one shared default gateway may be selected. Combining this mode with other
independent VPN configurations is currently rejected before local setup or a
Meraki upload; existing tunnels are left unchanged.

The existing customer `pos/.ID` folder remains mounted read/write at guest
`/etc/ipsec.d`. Connection, secret, numeric endpoint, reconnect hook and default
ownership/order files live there. Native setup checks the running guest's mount
identity and required executables from a fixed host Python process, without
executing guest code. The actual systemd reload/enable still runs inside the guest.
Missing dependencies fail before the optional Meraki upload. Existing global
IPsec configuration is preserved, with narrow include globs appended and a private
backup before the first change. Secrets remain in mode-0600 per-connection files.

The shared controller stores root-only profiles under
`/var/lib/mykos/vpn-gateways`. Its boot guard protects configured guest routes
before Podman automatic restart; its timer reconciles only explicitly configured
profiles. Installing/updating MYKOS does not select a gateway, rewrite existing
VPNs or enable these optional units. Removing the last profile disables the timer
and guard. Uninstall deactivates owned routing/firewall changes while retaining
operator profiles; explicit container-state purge removes profiles too.

The IP address expansion shows network metadata, VPN connections and an icon
map. **Show connected guests** adds shared-network members and gateway dependents.
Green links require reported SA state; grey/dashed links are unconfirmed. A network
membership edge does not prove connectivity. Read models use saved host metadata
and cached kernel observations, never PSKs or extra guest polling.

Ubuntu-specific new VPN/shared-folder work is deferred. Its released VM creation,
RAM capture and existing individual VPN paths are unchanged. Shared host/native
routing can still carry traffic from its wrapper, as for Windows and macOS.

### Meraki update checks

The chosen remote public endpoint must uniquely identify one network in the
configured Meraki organization. New peers are restricted to that network; MYKOS
does not fall back to all networks. Network-ID binding and route-based peer settings
require MX 19.1 or newer. See Cisco's
[third-party VPN peer API](https://developer.cisco.com/meraki/api-v1/update-organization-appliance-vpn-third-party-vpn-peers/).

MYKOS downloads and preserves the complete peer list, rechecks for concurrent
changes immediately before PUT, and verifies the returned public peer settings.
An invalid/unverifiable response aborts local configuration and activation; an
ambiguous remote result must be checked in Dashboard before retrying. There is
no atomic compare-and-swap provided by this workflow, so avoid simultaneous
Dashboard/API edits even with that recheck. No whole-organization rollback or blind
retry is attempted. Router-side internet forwarding/NAT and actual MX negotiation
still need validation for the selected site; local SA state alone does not prove
remote internet egress.


## Geographic map

Click the host IP address to expand the existing geographic map beneath the host
row. It uses saved inventory latitude/longitude values and marks current node
status. Nodes without valid saved coordinates are omitted rather than placed at
an invented location. Closing the pane retains the map for reopening; no extra
guest collection or IP geolocation lookup is performed.


Map points are IDs and saved host locations. Hovering a point shows the available
shop name, hostname, configured POS type, OS/IP/status, and saved application
information. Green means up, red means down, and yellow means unknown. Optional
`shop_name`, `pos_type`, and `app_info` node metadata survives collection. Host
locations can be saved in the inventory `hosts` list with `hostname`, `lat`, and
`lon`; the current host's cached status is joined by hostname. No saved location
means no map pin. POS permission enforcement is separate from these display fields.
