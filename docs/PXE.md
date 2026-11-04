# PXE desktop provisioning

MYKOS can provision Windows 11 x64 desktops using UEFI, iPXE/wimboot, Windows PE and an imported Windows image. Installing MYKOS does not enable a boot listener, change DHCP, arm a desktop, or erase a disk. The Windows 11 provisioning flow has passed an isolated VM installation and first-boot validation. Physical firmware, Secure Boot and routed-VPN deployments still need separate site testing; the optional Tiny11 build has not been retested in this batch.

## Configure and import

Media import requires `wimlib-imagex`; building the guard requires a Windows x64 cross compiler. Use an HTTPS certificate chain trusted by iPXE, curl in WinPE and the installed Windows system. For a private PKI, enroll its issuing CA in the appropriate trust stores.

Use official Windows 11 x64 client media. Import an extracted directory containing `sources/boot.wim` and `sources/install.wim`. Windows 10, Windows Server and ARM images are rejected. The optional pinned regular Tiny11 builder retains servicing, Defender, recovery and shared runtimes; Core is unsupported. A Tiny11 image requires its matching MYKOS build report. Media and tool binaries remain runtime data and are excluded from source/RPMs.

```sh
sudo /usr/local/sbin/mykos-pxe configure --origin https://mykos.example.com --allow-network 192.168.222.0/24
sudo /usr/local/sbin/mykos-pxe-fetch-tools
sudo /usr/local/sbin/mykos-pxe import-media /path/to/extracted/windows11 --index 6
# For Tiny11, also supply --tiny11-report /path/to/MYKOS-Tiny11-build.json
```

The root operator imports the compiled `mykos-pe-guard.exe`, pinned curl, CA bundle and wimboot plus a publisher-verified Chrome MSI using `mykos-pxe asset NAME FILE --sha256 DIGEST`. Build the guard from `pos/install/pxe/mykos-pe-guard.c` with a Windows x64 cross compiler. The pinned downloader writes tools under `/home/mykos-pxe/tools`; changed upstream bytes fail closed. Never substitute an unverified executable to bypass a download failure.

Prepare `/home/mykos-pxe/boot` with the verified signed iPXE handoff files and an `autoexec.ipxe` chaining to `https://mykos.example.com/mykos/pxe/autoexec.ipxe`. Restrict the HTTP handoff port 8088 to the staging subnet before explicitly enabling `mykos-pxe-http.service`. The listener serves only bootstrap files, not job credentials. No DHCP server is installed. Site DHCP must advertise the correct UEFI HTTP boot URL; local DHCP broadcasts do not traverse a routed VPN. Do not enable this on a production site before validating its firmware and network path.

## Dashboard and operator controls

A full administrator can use **PXE desktops** in the Windows pane to arm an identified desktop or revoke an active lease. Choose the imported WIM index, eight-digit customer ID, MAC, hostname, password and expiry. Existing partitions require both an explicit erase option and an exact repeated target MAC. The local Windows password is delivered over HTTPS in the private answer, never in a URL or public job listing. The regular Windows profile creates a local `mykos` administrator in `WORKGROUP`.

The root CLI can also arm a job with `mykos-pxe arm --mac MAC --customer EIGHT_DIGIT_ID --profile windows11`; its password prompt is hidden. `status` lists sanitized jobs, and `revoke JOB_ID` closes a lease. No password argument is accepted. Wipe authorization is optional and explicit with `--allow-wipe`.

The WinPE guard verifies the active MAC, internal non-USB disk 0 and a minimum 64 GiB disk before partitioning. The job consumes its applying phase before any erase, preventing automatic reinstall after a PXE reboot. Failed, expired, revoked and completed leases cannot fetch more private job data. Expired configuration and credentials are scrubbed on access and by a one-minute cleanup timer; completion/failure/revocation immediately invalidates the lease. Finish or revoke active jobs before changing media, staging networks or upgrading MYKOS.

## Status and limitations

A PXE row reports provisioning progress, not live physical power, console, VPN or RAM state. A completed row means the first-boot script reported success. Hosted Windows VMs retain their normal MYKOS lifecycle controls; physical machines do not receive those VM actions. MAC enrollment is an operator allowlist, not cryptographic device authentication; use a trusted staging LAN/VPN and short leases. Windows licensing remains the operator's responsibility.

First boot installs signature-checked Chrome, keeps its updater, enables Defender and Firewall, retains printing/SMB/ODBC and Windows servicing, and removes temporary auto-logon credentials. Provisioning fails if a required step fails. A failed disk installation requires deliberate operator review and rearming; it is never retried destructively by discovering a machine.

References: [iPXE Windows PE boot](https://ipxe.org/wimboot), [iPXE Secure Boot](https://ipxe.org/secboot), [Microsoft Windows deployment](https://learn.microsoft.com/windows/deployment/), [regular Tiny11 builder](https://github.com/ntdevlabs/tiny11builder).

The Provision desktop button always opens its dialog. If the server is unconfigured or missing an asset, the dialog names the blocker and keeps Arm disabled. Once setup is complete, it reports PXE ready and uses the imported WIM index by default. Opening the dialog alone never arms a machine. Root setup repairs existing private cache-directory traversal permissions for the web service. The service mounts only PXE assets read-only and hides other home directories. Restart mykos-web after initial media import to attach this mount; unreadable media is reported and cannot be armed.
