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
