# MYKOS fresh RHEL 9 installation test

Package: `mykos-rhel9-20261024.tar.gz`. Built from the current maintained source,
including the thicker inverted Containers/VPN bars. This is an online source
package, not an RPM or an offline dependency bundle. No production identities,
API keys, settings, inventory, backups, or container images are included.

## 1. Prepare a disposable test server

Use a fully updated RHEL 9 x86_64 VM with sudo access, working DNS/time, a Red Hat
subscription providing BaseOS and AppStream, and outbound access to those repos
and PyPI. Suggested lab sizing: 2 CPUs, 4 GB RAM, 30 GB disk; image builds and
customer backups require additional capacity. Take a VM snapshot before installing.
Do not run these fresh-install steps on production.

Use a separate test hostname. In the commands below replace
`mykos-test.example.com` with that hostname; ensure it resolves to the VM from
your Mac. Do not use `mykol.com` for this test.

```bash
cat /etc/redhat-release
uname -m
sudo subscription-manager repos --list-enabled
sudo dnf upgrade --refresh -y
sudo dnf install -y python3.11 python3.11-pip tar openssl
/usr/bin/python3.11 -c 'import sys,hashlib,ssl; assert hasattr(hashlib,"scrypt"); print(sys.version); print(ssl.OPENSSL_VERSION)'
```

Reboot if the upgrade requires it. Use Python 3.11 throughout: the pinned Uvicorn
0.39.0 requires Python 3.10 or newer, so RHEL 9's default Python 3.9 is insufficient
for this clean install. Do not replace the system `python3` symlink.

## 2. Transfer and verify the package

On your Mac (replace `TEST_SERVER` with the VM hostname/IP):

```bash
scp /Users/mgreen/mykos/releases/mykos-rhel9-20261024.tar.gz /Users/mgreen/mykos/releases/mykos-rhel9-20261024.tar.gz.sha256 mgreen@TEST_SERVER:~/
```

On the RHEL 9 VM, as the ordinary SSH user:

```bash
cd ~
sha256sum -c mykos-rhel9-20261024.tar.gz.sha256
tar -xzf mykos-rhel9-20261024.tar.gz
cd mykos-rhel9-20261024
sha256sum -c SHA256SUMS
export LAB_HOST=mykos-test.example.com
```

Both checksum checks must pass. Retain this extracted directory for verification,
updates, and troubleshooting. It is a fresh source tree, not a live-host backup.

## 3. Preview, install dependencies, and create users

```bash
sudo /usr/bin/python3.11 bin/mykos-install install --dry-run --python /usr/bin/python3.11 --public-origin "https://$LAB_HOST"
sudo /usr/bin/python3.11 bin/mykos-install install --install-deps --python /usr/bin/python3.11 --public-origin "https://$LAB_HOST"
sudo /usr/local/sbin/mykos-users create mgreen --role admin
sudo /usr/local/sbin/mykos-users create observer --role user
sudo /usr/bin/python3.11 bin/mykos-install verify
sudo /opt/mykos-web/.venv/bin/python --version
```

Enter new lab passwords when prompted (at least 12 characters). There is no default
login or API key. Expect Python 3.11 in the venv. Do not add `--image-build-deps`
for this dashboard/API installation test. If DNF reports an unavailable package,
stop and save the error plus `dnf repolist`; do not add random external repos.

Review `/etc/mykos/config.json` and `/etc/mykos/pos.env` using `sudoedit`. Terminal
access defaults to disabled. Leave it disabled for the initial smoke test. Supply
site settings only when testing the corresponding operation. No real POS images,
backup clients, VPN peers, or repository mirrors are created by this package.

## 4. Configure lab HTTPS

### Optional desktop VM support

The current source installer includes Windows/Server, macOS and Ubuntu template
creation and lifecycle tools. After the basic dashboard smoke test, use a host
with KVM passthrough and follow `docs/DESKTOPS.md`. Initialize storage and
trusted-LAN firewall access explicitly:

```bash
sudo /usr/bin/python3.11 bin/mykos-install install --python /usr/bin/python3.11 --public-origin "https://$LAB_HOST" --desktop-network 192.168.222.0/24
```

Replace the network for your lab. Normal installation does not create guests,
download OS installers, accept license terms, or open desktop ports. Older
bundles must be replaced with a newly built bundle to contain these additions.

Prefer a certificate issued by your lab's trusted CA, with the test hostname in its
SAN. The commands below generate a short-lived self-signed certificate for an
isolated lab. Before browser login, explicitly trust this certificate on the test
client through your normal certificate-management process; do not bypass browser
certificate warnings. Use an approved CA-issued certificate for production.

```bash
sudo openssl req -x509 -nodes -newkey rsa:3072 -days 30 \
  -keyout /etc/pki/tls/private/mykos-lab.key \
  -out /etc/pki/tls/certs/mykos-lab.crt \
  -subj "/CN=$LAB_HOST" -addext "subjectAltName=DNS:$LAB_HOST"
sudo chmod 600 /etc/pki/tls/private/mykos-lab.key
sudo tee /etc/httpd/conf.d/mykos-lab.conf >/dev/null <<EOF
<VirtualHost *:443>
    ServerName $LAB_HOST
    SSLEngine on
    SSLCertificateFile /etc/pki/tls/certs/mykos-lab.crt
    SSLCertificateKeyFile /etc/pki/tls/private/mykos-lab.key
    Include /usr/local/share/mykos/services/apache-mykos.conf.example
</VirtualHost>
EOF
sudo restorecon -v /etc/pki/tls/private/mykos-lab.key /etc/pki/tls/certs/mykos-lab.crt /etc/httpd/conf.d/mykos-lab.conf
sudo setsebool -P httpd_can_network_connect on
sudo apachectl configtest
```

The SELinux boolean permits Apache's outbound proxy connections, including the
loopback backend. Keep SELinux enforcing. If firewalld is used, inspect the active
zone and open HTTPS in the zone attached to the VM's client-facing interface:

```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --get-active-zones
export LAB_ZONE=public
sudo firewall-cmd --permanent --zone="$LAB_ZONE" --add-service=https
sudo firewall-cmd --reload
```

Replace `public` if the active interface uses another zone. Keep SSH access allowed.
Do not expose backend port 8000. Existing `ssl.conf` may also contain a default
VirtualHost; the hostname-specific MYKOS VirtualHost is selected by TLS SNI.

## 5. Activate and verify

```bash
sudo /usr/bin/python3.11 bin/mykos-install install --enable --python /usr/bin/python3.11 --public-origin "https://$LAB_HOST"
sudo systemctl enable --now httpd
sudo /usr/bin/python3.11 bin/mykos-install verify
sudo systemctl status mykos-web.service mykos-container-admin.socket mykos-terminal.socket --no-pager -l
sudo systemctl list-timers --all 'mykos-*' --no-pager
sudo ss -ltnp 'sport = :8000'
curl --fail --silent --show-error http://127.0.0.1:8000/ -o /dev/null
curl --fail --silent --show-error --cacert /etc/pki/tls/certs/mykos-lab.crt "https://$LAB_HOST/mykos/api/v2/openapi.json" -o /dev/null
```

Use your issuing CA's PEM file instead of `mykos-lab.crt` for a CA-issued cert.
Expected: verification succeeds, service active, both sockets listening, collector
timers scheduled, port 8000 listening only on 127.0.0.1, both HTTP checks succeed.
The number of managed files depends on this package's contents.

Open `https://YOUR_TEST_HOST/mykos/` in your browser after establishing certificate
trust. Check:

- Admin login succeeds; hostname, time, host metrics, graph and footer populate.
- Graph retains five minutes of history, polls every five seconds, and shows count
  series as downward bars. With zero eligible containers, count bars are absent.
- On a completely empty machine the OS, Backups and Repos panes can all be hidden.
  That is expected. OS panes require matching running template containers, rather
  than merely downloaded Podman images. Do not stage a POS from a generic image.
- API and Docs links open; follow each guide link to confirm installed docs load.
- The white Host notes icon opens; save a lab note and reload to check persistence.
- Log out, log in as observer, and confirm administrative operations are unavailable.
- Log out and confirm protected operations require authentication.

## 6. Test API key authentication

```bash
sudo /usr/local/sbin/mykos-api-keys create rhel9-smoke read
```

Save the one-time key securely. Test it using the following Python prompt so the
key is not placed in shell history or process arguments:

```bash
/usr/bin/python3.11 - <<'PY'
import getpass, json, os, ssl, urllib.error, urllib.request
base = 'https://' + os.environ['LAB_HOST'] + '/mykos/api/v2'
context = ssl.create_default_context(cafile='/etc/pki/tls/certs/mykos-lab.crt')
key = getpass.getpass('Read API key: ')
for route in ('/host/metrics', '/nodes', '/images', '/backups', '/repos', '/notes/host'):
    req = urllib.request.Request(base + route, headers={'X-MYKOS-API-Key': key})
    with urllib.request.urlopen(req, context=context, timeout=30) as response:
        json.load(response)
        print(route, response.status)
try:
    urllib.request.urlopen(base + '/host/metrics', context=context, timeout=30)
except urllib.error.HTTPError as error:
    assert error.code == 401, error.code
    print('Missing key rejected: 401')
else:
    raise RuntimeError('Protected API accepted a request without a key')
PY
```

For a CA-issued certificate change the `cafile` to your CA PEM. Expect six 200
responses and a 401 without a key. Empty lists are normal on a fresh machine.
Revoke the test key by its ID when done:

```bash
sudo /usr/local/sbin/mykos-api-keys list
sudo /usr/local/sbin/mykos-api-keys revoke KEY_ID
```

## 7. Test persistence and collect failures

```bash
sudo systemctl restart mykos-web.service
sudo /usr/bin/python3.11 bin/mykos-install verify
sudo journalctl -u mykos-web.service -n 80 --no-pager
sudo journalctl -u mykos-collector.service -u mykos-details.service -u mykos-extended.service -n 80 --no-pager
sudo journalctl -u httpd -n 50 --no-pager
sudo ausearch -m AVC -ts recent
```

Log in again and verify the saved Host note survives the service restart. Reboot
the VM and repeat the service, HTTPS, login and note checks. If a request fails,
record its URL, time, HTTP status and the relevant logs. Redact keys, cookies,
passwords and private key material before sharing logs. Do not disable SELinux to
work around a failure. Check the exact AVC and file labels first.

A successful empty-host smoke test validates installation, HTTPS, login, API and
collectors. Actual POS staging, VPNs, backup transfers, repo sync and terminals
need separate integration tests with approved lab data and prepared images.

For a repeatable clean-install run, revert the VM snapshot. Installer uninstall
preserves settings, identities, venv and OS accounts; it is not a factory reset.

## Package validation and references

The release includes `PACKAGE.json`, per-file `SHA256SUMS`, maintained source and
tests. Source/staged-installer tests are run on the build Mac; this does not claim
a live RHEL 9 installation was performed. RPMs and Python wheels are downloaded
on the VM, and transitively resolved Python versions are recorded in
`/opt/mykos-web/installed-requirements.txt` after installation.

- RHEL Python stacks: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/installing_and_using_dynamic_programming_languages/index
- RHEL Apache/TLS/proxy setup: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/deploying_web_servers_and_reverse_proxies/index
- Uvicorn 0.39.0 Python support change: https://www.uvicorn.org/release-notes/
- General install/recovery: `docs/INSTALL_SERVICES.md`; identity: `docs/IDENTITY.md`.
