# Unattended image builds

Run one command as root, with no account/password or Finish prompts:

```bash
sudo /usr/local/sbin/mykos-image-build ubuntu22.04
sudo /usr/local/sbin/mykos-image-build win10
sudo /usr/local/sbin/mykos-image-build wns2022
sudo /usr/local/sbin/mykos-image-build mac15
```

From the text administration directory, `./vm build OS` and the existing
Create Image menu use the same pipeline. Repeating the command resumes an
unfinished unattended build using its existing disks and credentials. `--replace` explicitly archives a
stopped previous attempt before building again. An active base builder is
never stopped automatically by replacement. Existing ID-specific containers
are not modified by an image build.

Optional ID-specific host scripts live at `/etc/mykos/instance-hooks/OS`. They
run once after the new instance restores its base state, before startup is
reported successful. Hooks must be executable root-owned files without
group/world write access. Arguments are SHOPCODE and SHOPCODE.hostname;
MYKOS_SHOPCODE, MYKOS_CONTAINER_NAME and MYKOS_OS are also set in the environment.
The running generic base is never passed to these hooks.

A successful build installs and provisions the OS, verifies the installed
system, detaches installation-only devices, captures matching RAM/device/disk
state, commits the installed disk payload under the readable local OS image
name, and leaves one generic base container running. The image payload is
in `/opt/mykos-image/golden.tar.gz` as a compressed sparse archive, with an
`image.json` manifest; it is not just the small VM wrapper. Commit temporary
files use the VM storage filesystem instead of filling `/var/tmp`. Fast
ID-specific startup clones the immutable prepared state into private storage
and restores RAM. Container names remain `SHOPCODE.hostname`, for example
`12345678.prod.mykol.com`. Changes made by an instance do not modify the base.

Runtime guests and their startup captures use 2048 MiB RAM. Unattended macOS
installation temporarily uses 4096 MiB because Apple mounts a 2.5 GiB temporary
filesystem during its second stage. The builder returns to 2048 MiB and verifies
an installed boot before capture. Windows and Ubuntu use 12 GiB pagefile/swap. macOS manages its own swap. Native RedHat containers are not
assigned a memory cap. The QEMU image already supplies the ISO tools, and the
macOS package generator uses Python's standard library. No additional helper
containers or host package dependencies are needed for unattended installation.

Accounts default to `mykos` with a generated complex password. Credentials are
stored, root-only, in `/var/lib/mykos/image-builds/OS/credentials.json` and reused
for that OS on subsequent builds. They are not printed in progress messages.
The authenticated API can optionally supply a different local account/password.

Ubuntu uses Canonical live-server media with autoinstall and installs the
minimal desktop on its first installed boot, avoiding installer-chroot Snap
setup failures. This supports 20.04, 22.04 and 24.04 consistently. Provisioning
includes the direct `su - root` serial console, swap, Libreswan and QEMU guest
agent. The ISO checksum is checked against Canonical's SHA256SUMS before use.

Windows uses the official version-specific Dockur XML asset, with MYKOS account
settings, through its normal Autounattend.xml/media-generation pipeline.
Driver, OEM and first-logon hooks are retained. QEMU Guest Agent is extracted
from the `virtio-win` data RPM available in the configured RHEL repositories,
after verifying its signature against the host RPM key database. The data RPM
is downloaded but never installed on the host. Guest-side hashes bind the staged
installer to that authenticated payload; there is no unsigned-download fallback. A complex Administrator
password and non-expired local account prevent mandatory password-change
screens. Current unattended builds support Windows 10/11/8.1 and Server
2016/2019/2022/2025. Older Windows releases retain their legacy creation path;
XML is not the setup format for Windows 98/ME/2000/XP.

macOS uses Apple's `startosinstall` from Recovery with a pinned MIT-licensed
Dockur bootstrap (see `pos/install/macos/UPSTREAM` and `LICENSE`). Account
packages are generated privately for each build. The bootstrap is passed through
`startosinstall --installpackage`, so setup suppression and first-boot files are
applied after Apple creates the final system/data volumes. A scoped, expiring completion
token reports that the installed OS has reached WindowServer after account
setup and again after the clean boot before capture. The one-use build daemon
removes itself; this token grants no administrative API access. The configured
HTTPS public origin must be reachable from the guest. For a private CA or
self-signed endpoint, place the trusted PEM certificate in
`/etc/mykos/image-build-ca.pem` (root-owned, not group/world writable). The guest
uses it for certificate and hostname verification; TLS verification stays enabled.

## API

Use an admin API key for `PUT /mykos/api/v2/images/OS/build` with optional
`username`, `password` and `replace` JSON fields. The request starts the build
in `mykos-image-build@OS.service`. Read progress with an API key at
`GET /mykos/api/v2/images/OS/build` or from the command:

```bash
sudo /usr/local/sbin/mykos-image-build OS --status
sudo journalctl -u mykos-image-build@OS.service --no-pager
```

Image build workers are included in maintenance checks and explicit Purge.
RPM upgrades do not start builds, remove stored images, or restart guests.

## SQL Server on native Linux

Operator kickstarts such as `pos/ks/mssql2022.ks` use the existing native
Linux image build and commit workflow in the text menu. The Microsoft SQL
Server pane recognizes `mssql2022` and `mssql2025`, showing only available
images by default. The pane uses native Linux lifecycle, root console and
snapshot operations; it does not create a Windows VM. SQL product versions
come from the image name, without probing the running guest. The show-all
leaf includes disabled choices and sample rows when no SQL images exist.
