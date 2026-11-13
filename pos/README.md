# MYKOS images and instances

Run `sudo mykos`, choose **2. Containers**, then **c. Build image**. To enter the
POS menu directly on an installed host, use:

```bash
cd /usr/local/share/mykos/pos
sudo ./pos
```

Choose a single explicit OS identifier from the [complete image catalog](../docs/IMAGE_BUILDS.md#all-supported-instance-types).
This edition has 34 native image selections and22 VM-backed choices.
An eight-digit instance ID is separate from an OS image; **s. ID** selects the
ID/OS and **2. Start/Stop** operates that instance. Dashboard **+** starts from
an available image and does not install a missing one.

## Build engines and prerequisites

- EL6/EL7 final x86_64 userspaces use `build_legacy_el.py` and a bounded UBI7
  worker with pinned vendor keys, verified RPMs and HTTPS. RHEL requires the
  builder host's existing entitlement. EL6 uses Upstart; EL7 uses isolated
  systemd/cgroup compatibility. These guests share the host kernel.
- Debian13, Arch, SLE BCI Init15 SP7, Amazon Linux2023 and Alpine3.24 use official
  OCI bases through `build_native_oci.py`; selected digests and vendor package
  policy are recorded. Alpine uses OpenRC; the other profiles use systemd.
- Other native EL/Fedora/SQL recipes use `build_rootfs.py`, Anaconda/Lorax and
  temporary parser-adapted Kickstarts. The host OS metadata is not rewritten.
- Windows, Windows Server, macOS, Ubuntu and Fedora GNOME use the VM workflow.
  Modern choices build unattended; Windows7/XP/Me/2000/98 and Android-x86 use
  manual installation. Android needs two Finish steps after reaching its desktop.

RHEL9 is the stable MYKOS host target. RHEL10 host packages are preview-only;
EL6/EL7/EL8 guest choices do not enable those versions as MYKOS hosts. Vendor
licenses, entitlements, upstream availability, KVM and legacy ABI prerequisites
still apply. A menu choice does not certify an arbitrary guest workload.

The `all` build is the fixed16-image EL batch: `rh7`, `rh8`, `rh9`, `rh10`,
Rocky/Alma/Oracle/CentOS8–10. Newly added legacy clones, other Linux families,
Fedora and SQL are explicit individual builds. Never run `all` to validate app
or documentation changes. The retained `ks/rh7.ks` is historical recipe material;
the current `rh7` menu path uses the signed-package legacy worker.

## Images, instances and history

A prepared native image can be used without a generic base running. Base
consoles open on demand and idle completed bases are reclaimed by the idle-base
timer. OCI and legacy workers can finish without leaving a generic base container.
VM images retain matching prepared disks and startup RAM. Updating an image is
separate from changing an already-created instance.

Reopen terminal menus after upgrades to load current shell functions. New native
build and sync attempts appear in Jobs; older native attempts are not backfilled.
Existing VM observations can predate tracked native history. An existing image
can be reused without being rebuilt. See the [UI guide](../docs/UI_GUIDE.md#jobs--recovery).

Keep per-ID hidden state and private VPN data during upgrades. Use the managed
native/VM lifecycle and explicit snapshot/deletion controls. Purge is a separate,
broad destructive workflow, not routine cleanup. Guest root shells are privileged
operator actions; these are rootful containers, not an automatic rootless or
compliance-certification environment.

## Related guides

- [Build commands, versions and recovery](../docs/IMAGE_BUILDS.md)
- [VM consoles and snapshots](../docs/DESKTOPS.md)
- [Networking and VPN scope](../docs/NETWORKING.md)
- [Memory and native checkpoint limits](../docs/BALLOONING.md)
- [Testing and resource cleanup](../docs/TESTING_README.md)
