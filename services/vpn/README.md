# MYKOS Linux VPN tools

A small Ubuntu 24.04 Linux image with Libreswan, ISC dhclient, iproute2, iptables,
and basic network diagnostics. The base image is pinned by digest. The tested
amd64 image is about 158 MB uncompressed. No desktop or systemd is included.

## Windows 11: native WSL Containers

WSL must be installed and active, including any reboot requested when enabling
VirtualMachinePlatform. Run these in Windows PowerShell:

```powershell
wsl --version
wslc version
wslc run --help
```

Microsoft documents native `wslc.exe` with WSL 2.9.3 or later. This deployment also
requires a version whose `run --help` includes `--cap-add`, permission to grant
NET_ADMIN, and working kernel XFRM support. WSL inside a MYKOS Windows VM requires
working nested virtualization. The script checks capabilities before creating its
persistent container. It never changes Windows features or reboots Windows.

Copy this entire `vpn` directory into Windows. In PowerShell, change into that
folder and run:

```powershell
.\deploy-windows.ps1
```

The script builds `mykos-vpn:20261025`, tests XFRM access, starts a container named
`mykos-vpn` with NET_ADMIN, NET_RAW and IPC_LOCK, and verifies that Libreswan is
responding. An existing container of that name is left intact. Failed startup
retains the container and its logs for inspection. If PowerShell execution policy
blocks the script, use your approved script-signing/execution process.

```powershell
wslc exec mykos-vpn ipsec status
wslc exec mykos-vpn dhclient --version
wslc container logs mykos-vpn
wslc container stop mykos-vpn
```

No VPN peer, PSK, routes, port forwarding, automatic DHCP request, or Windows
firewall rule is installed. DHCP operates on the Linux container network, not
Windows' physical adapter. Making Windows use this VPN requires explicit routing
and networking design. Do not run dhclient on an active interface without
planning its effect. Store VPN secrets separately from this image.

## Linux build and verification

```bash
sudo podman build --format=docker -t localhost/mykos-vpn:20261025 .
sudo podman run --rm --network=none --cap-add NET_ADMIN --cap-add NET_RAW localhost/mykos-vpn:20261025 check
```

On the RHEL 9 dev host the image built successfully with Libreswan 4.14 and
isc-dhclient 4.4.3-P1. A disposable container started Pluto and responded to
`ipsec whack --status` using Linux XFRM. The check correctly failed without
NET_ADMIN. This verifies the Linux image; it does not establish WSL compatibility
or an end-to-end VPN tunnel. Use Docker image format if the chosen builder would
otherwise omit the image HEALTHCHECK.

References: [Microsoft WSL Containers](https://learn.microsoft.com/en-us/windows/wsl/tutorials/wsl-containers),
[capability support](https://github.com/microsoft/WSL/pull/41681).

Win11 runtime verification found that WSL 3.0.3 is installed but its native
`wslc run` has no `--cap-add` option. The guarded deployment script stops on
that version. Do not equate a successful WSL install with working nested
virtualization or IPsec capabilities. MYKOS retains explicit `VMX=Y` in a
selected guest's CPU profile; enabling it requires a full stop/recreate/start
and can affect compatibility with older saved RAM states. Existing guest
profiles are not changed automatically.
