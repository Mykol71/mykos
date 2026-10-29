# IP installation and optional domain HTTPS

[Documentation index](README.md)

<!-- contents:start -->
**Contents**

- [Install from the signed repository](#install-from-the-signed-repository)
- [Optional domain prerequisites](#optional-domain-prerequisites)
- [Bootstrap from a source bundle](#bootstrap-from-a-source-bundle)
- [Create the first administrator](#create-the-first-administrator)
- [Repeated runs and removal](#repeated-runs-and-removal)
- [Native container image fixes](#native-container-image-fixes)

<!-- contents:end -->

## Install from the signed repository

With the signed MYKOS repository configured, a fresh installation needs only:

```bash
sudo dnf install mykos
```

The RPM starts `mykos-firstboot.service` after its package transaction to
configure an IP-only Apache proxy and the application. It verifies that MYKOS
is running, then prints `MYKOS is running: http://SERVER_IP/mykos/` in the install
output. This default needs no public domain or Certbot certificate. Existing
configured deployments are preserved during upgrades.

```bash
sudo systemctl status mykos-firstboot.service --no-pager
sudo journalctl -u mykos-firstboot.service --no-pager
sudo systemctl restart mykos-firstboot.service
```

To enable domain HTTPS explicitly after installation:

```bash
sudo mykos-setup --configure-https
sudo mykos-setup --configure-https --domain example.com --email admin@example.com
```

## Optional domain prerequisites

For this optional mode, public DNS must point to this host and incoming TCP 80
and 443 must be reachable. Without `--domain`, the domain is derived from the
last two server hostname labels, preserving the existing domain selection rule.
The final HTTPS URL is printed only after the application responds.

## Bootstrap from a source bundle

From a trusted MYKOS source bundle:

```bash
# Read-only preview; no sudo required:
python3 bin/mykos-bootstrap --plan

# Default IP access and signed RPM setup:
sudo bash bin/install bootstrap
# Optional domain HTTPS, certificate, signed RPM and setup:
sudo bash bin/install bootstrap --configure-https
# Optional certificate account email:
sudo bash bin/install bootstrap --configure-https --email admin@example.com
```

With `--configure-https`, no domain prompt or Expect session is used.
Certbot runs noninteractively,
agrees to the ACME subscriber agreement, and registers without an email unless
`--email` is supplied. The bootstrap uses Certbot's webroot authenticator and
writes a dedicated Apache HTTP/HTTPS virtual host. Public HTTP validation must
succeed; it never substitutes a self-signed or staging certificate. Existing
virtual hosts are preserved. If a conflicting existing site needs HTTPS repair,
it stops and reports that instead of replacing the site's configuration.
Existing trusted Certbot HTTPS sites with the MYKOS proxy can be reused.

In this optional HTTPS mode, it installs Apache/Certbot prerequisites and opens
HTTP/HTTPS in an active
firewalld default zone when needed, obtains the certificate, configures an HTTP
redirect and authenticated `/mykos/` HTTP/WebSocket proxy, then verifies HTTPS
locally with domain and CA validation. In the standalone bootstrap flow, only after this succeeds does it install
the signed `mykos` RPM from the matching EL8/EL9/EL10 repository and invoke:

```bash
mykos-setup --public-origin https://DOMAIN --enable
```

Repository signing keys are SHA-256 pinned in the bootstrap, and package
signature checking stays enabled. The EPEL repository used for Certbot is
normally disabled and enabled only for that dependency transaction. Required
RHEL BaseOS/AppStream repositories must be available; the bootstrap does not
change subscription registration. On SELinux hosts it enables Apache's network
connection permission for the local application proxy.

## Create the first administrator

Create the first administrator after setup:

```bash
sudo /usr/local/sbin/mykos-users create NAME --role admin
```

## Repeated runs and removal

Domain HTTPS ownership hashes live in `/var/lib/mykos-installer/bootstrap.json`;
IP-only setup uses `/var/lib/mykos-installer/ip-bootstrap.json`.
Completed installations are checked and left unchanged: no repeat certificate
request, package reinstall or setup. An interrupted installation resumes at
its unfinished stage and retains an already-issued certificate. A twice-daily
systemd timer renews certificates created by this bootstrap and reloads Apache
only after successful renewal. Existing certificates keep their existing renewal
mechanism. This bootstrap does not perform RPM upgrades; use the normal signed
RPM update process and rerun `mykos-setup` after upgrading.

Final package removal (`sudo dnf remove mykos`) invokes bootstrap cleanup.
RPM upgrades do not invoke it. Final removal first stops/disables automatic setup. Cleanup removes unchanged Apache/proxy files,
MYKOS-owned renewal units and bootstrap repository/key files. A certificate is
deleted only if this bootstrap created it and no remaining Apache, nginx or
HAProxy configuration refers to it. A newly shared certificate keeps its renewal units. Preexisting certificates and ACME accounts,
shared Apache/Certbot packages, guest data, users and API keys are retained.
Administrator-edited files are preserved and reported. Firewall services added
by bootstrap are removed only when no Apache virtual hosts remain. The shared
SELinux permission remains enabled.

If setup failed before the RPM was installed, clean up using the same source:

```bash
sudo python3 bin/mykos-bootstrap --uninstall
```

Never delete `/etc/letsencrypt` wholesale: certificates and accounts can be
shared with other sites.

## Native container image fixes

Generated build copies exclude firmware and microcode packages and prevent boot
tmpfiles from writing `/run/nologin` on the builder. Original kickstarts are
preserved. Restored containers retain an instance-private PAM limits file and
use the native read-only cgroup mount so consoles and memory limits work.
