# MYKOS RHEL 9 installation qualification

This is the current lab checklist. Use the [bootstrap guide](docs/BOOTSTRAP.md)
for exact package/repository installation commands and the [source installer
guide](docs/INSTALL_SERVICES.md) when deliberately installing from source.
The older `mykos-rhel9-20261024.tar.gz` walkthrough and workstation-specific
transfer paths are historical; use the reviewed package name, checksums and
signature delivered with the release you are testing.

## 1. Reserve a disposable lab host

Use a separate RHEL 9 x86_64 host or VM with sudo access, working DNS/time,
BaseOS/AppStream entitlement and the required outbound dependency access.
A control-plane smoke test can use 2 CPUs, 4 GiB RAM and 30 GiB disk; guest
builds and workloads need separately sized storage, RAM and KVM access.
Record its identity and take a lab recovery snapshot. These are fresh-install
steps, not production upgrade instructions. RHEL 9 is the stable host target;
RHEL 10 remains preview-only, and guest EL6/EL7/EL8 choices do not enable those
releases as MYKOS hosts.

Use Python 3.11 for the RHEL 9 application environment. Do not replace the
system `python3` symlink. The console legacy builder's Python 3.9 compatibility
does not change the web service's runtime requirements.

## 2. Verify and install the selected release

Follow [signed RPM installation](docs/BOOTSTRAP.md#install-from-the-signed-repository),
or verify both source archive and internal SHA256 manifests before following
[source installation](docs/INSTALL_SERVICES.md#new-installation). Use the
canonical installer and explicit `--python /usr/bin/python3.11` for source work.
An online package is not an offline dependency bundle. Stop and preserve an
unavailable-dependency error rather than adding unreviewed repositories.

Set the intended hostname/public origin and choose a network configuration for
this lab. A `--public-origin https://...` argument configures application URLs;
it does not provision an Apache certificate. Use the documented optional domain
setup, or a lab CA trusted by the test client, before sending login credentials.
Keep SELinux enforcing, retain SSH access, and keep backend port8000 private.
Do not bypass TLS validation to make a smoke test pass.

## 3. Create the lab administrator and verify services

There is no default browser password or API key. Create new lab-only credentials
through the [identity commands](docs/IDENTITY.md); do not copy production identity
state. Keep terminals disabled until a test specifically requires them.

```bash
sudo /usr/local/sbin/mykos-users create labadmin --role admin
sudo /usr/bin/python3.11 /usr/share/mykos/bin/mykos-install verify
sudo /opt/mykos-web/.venv/bin/python --version
sudo systemctl status mykos-web.service mykos-container-admin.socket mykos-terminal.socket --no-pager
sudo systemctl list-timers --all 'mykos-*' --no-pager
sudo ss -ltnp 'sport = :8000'
```

Expected: verification passes, the application uses Python3.11, the service and
required sockets/timers are active, and the backend listens only on loopback.
For a source-only layout, use the reviewed source copy's absolute installer path
instead of `/usr/share/mykos/bin/mykos-install`. Verify the public HTTPS endpoint
with the test hostname and its trusted issuing CA.

## 4. Check the current dashboard and documentation

- Sign in as the lab administrator. Check hostname, host metrics, footer and Docs.
- Host history records one-second samples over65 minutes; the visible graph shows
  five minutes. Count bars appear only when eligible instance/preview counts exist.
- An empty lab can have no OS inventory panes. Prepared native images are
  discovered directly from storage and need not have a running generic base.
  Disabled Sample rows are previews, not real deployed instances.
- Open the [complete image catalog](docs/IMAGE_BUILDS.md#all-supported-instance-types).
  Verify legacy EL6/EL7, modern native Linux, the additional OCI families and
  VM/manual-install choices. Image availability is separate from host support.
- For a needed build, use `sudo mykos` → **2. Containers → c. Build image**.
  Dashboard **+** starts from a prepared image. Do not build all OS images merely
  to validate application installation or documentation.
- **Jobs** is a full local administrator window; old native attempts are not
  reconstructed. Viewer/limited-user controls and private extension features have
  different permission boundaries.

## 5. Record evidence and remove only lab-owned resources

Use the [testing checklist](docs/TESTING_README.md), including baseline identity,
exact commands, relevant live results and cleanup on success/failure/interruption.
Preserve user containers, images, snapshots, VPNs, accounts and data. Never use
Purge as an application smoke test. Guest image, networking and restore
qualification are separate tests, not implied by a successful dashboard install.
