# Automatic HTTPS and RPM installation

With the signed MYKOS repository configured, a fresh installation needs only:

```bash
sudo dnf install mykos
```

The RPM queues `mykos-firstboot.service` after its package transaction. It
automatically derives the domain, obtains/configures HTTPS, and runs MYKOS
setup. Running setup outside the RPM transaction lets Certbot dependencies
install without a nested DNF lock. The service is skipped when an existing
MYKOS installation manifest is present, including prod/dev upgrades.

```bash
sudo systemctl status mykos-firstboot.service --no-pager
sudo journalctl -u mykos-firstboot.service --no-pager
# Retry after correcting DNS/network/repository problems:
sudo systemctl restart mykos-firstboot.service
```

The service also retries on a subsequent boot until setup completes. Package
installation can finish before HTTPS/setup does; use its status/log to check
completion. Normal updates preserve existing configuration.

On a new RHEL 8 or RHEL 9 host, set its fully qualified hostname, make the
public domain point to this host, and allow incoming public TCP 80 and 443.
The bootstrap takes exactly the last two hostname labels: `prod.mykol.com`
becomes `mykol.com`. This is intentional; it does not calculate public suffixes
such as `co.uk`. The resulting domain must be the domain you control and want
on the certificate. A private dev host cannot obtain the production domain's
certificate while that domain points to production.

From a trusted MYKOS source bundle:

```bash
# Read-only preview; no sudo required:
python3 bin/mykos-bootstrap --plan

# HTTPS, certificate, signed RPM and setup, in that order:
sudo bash bin/install bootstrap
# Optional certificate account email:
sudo bash bin/install bootstrap --email admin@example.com
```

No domain prompt or Expect session is used. Certbot runs noninteractively,
agrees to the ACME subscriber agreement, and registers without an email unless
`--email` is supplied. The bootstrap uses Certbot's webroot authenticator and
writes a dedicated Apache HTTP/HTTPS virtual host. Public HTTP validation must
succeed; it never substitutes a self-signed or staging certificate. Existing
virtual hosts are preserved. If a conflicting existing site needs HTTPS repair,
it stops and reports that instead of replacing the site's configuration.
Existing trusted Certbot HTTPS sites with the MYKOS proxy can be reused.

It installs Apache/Certbot prerequisites, opens HTTP/HTTPS in an active
firewalld default zone when needed, obtains the certificate, configures an HTTP
redirect and authenticated `/mykos/` HTTP/WebSocket proxy, then verifies HTTPS
locally with domain and CA validation. In the standalone bootstrap flow, only after this succeeds does it install
the signed `mykos` RPM from the matching EL8/EL9 repository and invoke:

```bash
mykos-setup --public-origin https://DOMAIN --enable
```

Repository signing keys are SHA-256 pinned in the bootstrap, and package
signature checking stays enabled. The EPEL repository used for Certbot is
normally disabled and enabled only for that dependency transaction. Required
RHEL BaseOS/AppStream repositories must be available; the bootstrap does not
change subscription registration. On SELinux hosts it enables Apache's network
connection permission for the local application proxy.

Create the first administrator after setup:

```bash
sudo /usr/local/sbin/mykos-users create NAME --role admin
```

## Repeated runs and removal

State and ownership hashes live in `/var/lib/mykos-installer/bootstrap.json`.
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
