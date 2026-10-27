# Validation record and remaining integration work

Validated on macOS on 23 October 2026. No real systemd service, OS user, customer
account, deployed API key, container, VPN, firewall, or reverse proxy was changed.
The source directory contains no Git metadata, so this delivery has no commit or
branch identifier.

## Automated checks

The isolated Python 3.12.14 environment used FastAPI 0.128.8, Pydantic 2.13.5,
Uvicorn 0.39.0, Starlette 0.52.1, HTTPX 0.28.1, and pytest 9.1.1. Runtime direct
dependencies are pinned in `services/requirements.txt`. Apple system Python 3.9
lacks `hashlib.scrypt` on this machine; password tests therefore used the bundled
OpenSSL-enabled Python runtime and real scrypt hashes, without weakening hashing.
The live installer checks for scrypt support in the selected interpreter.

Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/venv/bin/python -m pytest -q -p no:cacheprovider tests
```

Result: **84 tests passed**. A Starlette/AnyIO test-client deprecation warning is
present; it does not indicate an application test failure.

Covered behavior:

- Dry-run creates no staging root. Staging rejects dependency installation and host
  activation, and rejects symlink destinations.
- Fresh install and manifest verification; source update preserves settings and
  identity-file contents. Historical identities, tokens, and archive are not shipped
  in the installed source copy.
- Code/settings snapshots, snapshot dry-run, rollback, modified-file detection,
  uninstall refusal for modified code, and preservation of settings/identities.
- SQLite data backup/restore and explicit data-restore opt-in; rejection of unsafe
  archive paths before code writes.
- Browser admin/nonadmin permissions, invalid keys, API-key-only operational access,
  read/admin key scopes, revocation, cookie/API separation, and public OpenAPI docs.
- Password reset/session invalidation, deleted-user denial, explicit admin roles,
  and legacy-user migration without implicit elevation by username.
- Local CLI creation, password reset, promotion, deletion, key listing, and revocation
  using only temporary identity databases and mocked password prompts.
- API input validation, stale snapshots, generated OpenAPI paths/public prefix,
  image OS details through browser/API responses, and container OS parsing.
- Privileged helper action allowlist, ASCII shopcode validation, ambiguous container
  rejection, staging protocol, and rejection of invalid image configuration before
  host commands. Podman/systemd operations are mocked.
- Running/stopped-container image OS collection, unavailable-file fallback, and
  treating os-release as data rather than executable shell content.

Shell sources were checked with `bash -n`; Python sources and extensionless Python
helpers were parsed with `ast.parse`. The rebuilt source-only format-1 archive's 37 entries
passed `install-services verify --bundle bin/mykos-web.img`. Archive verification
reads/checks the archive; it does not install it or import its credentials.

`docs/openapi.json` was generated from the maintained route definitions, without
reading deployed identities or calling host operations. Live `/mykos/api/v2/openapi.json`
is authoritative if source routes change later.

API parity coverage also checks Host metrics, Backup/Repos inventory and action
forwarding, forced cache refresh, API-key-only access without Origin or cookies,
read/admin scope separation, private-key response caching, and revoked-key denial.
Terminal authorization tests mock the PTY transport and verify admin API-key
handshakes separately from browser session/Origin checks. Swagger security and
new route discovery are checked without invoking any production helper.

Rename checks also cover predecessor discovery, a migration dry-run without writes,
copying existing SQLite identity databases, preserving secret values while changing
environment variable names, inventory and POS metadata, rewritten Apache routes,
private recovery archives, rejecting unsafe symlinks and repeat migrations, and
API key digest compatibility. Browser checks confirmed the dashboard and dark API
documentation under `/mykos/`. The renamed historical Word guide was rendered and
all three pages inspected. The deployment archive was rebuilt from maintained
sources without runtime identities, and its checksums were verified.

Interrupted migration tests simulate installation and verification failures, then
resume from the original snapshot without overwriting copied identities/settings.
Resume also rejects public or mismatched archives and accepts an already renamed
service account only when its UID matches the original archived databases.
Migration installs Python dependencies without rerunning the system package plan;
optional mail clients are excluded from the required system package list.

## Linux integration still required

This is a source/staging validation, not a claim that a production deployment ran.
Before activation, validate on an approved compatible Linux staging host:

1. Package availability, selected Python/venv, ownership, and `systemd-analyze verify`.
2. HTTPS proxy prefix, Origin, cookies, WebSocket routing, TLS, and SELinux permissions.
3. Collector freshness, rootful Podman discovery, snapshot ownership/readability,
   duplicate-shop handling, and actual image OS metadata.
4. Both socket-activated helper services, bounded live diagnostics, and authorization
   denials for nonadmins/read keys.
5. Prepared RTI image, correct NIC/DHCP behavior, IPsec configuration and reachability,
   an approved staging job with and without read/write backup mounts, and stop behavior.
6. Terminal opt-in, PTY behavior, disconnect cleanup, and session restrictions.
7. Code rollback and identity-data recovery on a disposable host, including service
   account numeric ownership and independent dependency/image/data backups.

The historical helper source was absent. Supplied helpers are compatible replacements
based on the app's protocol and existing POS commands; their integration behavior
must be tested. The global installer does not fabricate RTI software, backups,
images, subscription entitlements, networks, Meraki configuration, TLS certificates,
or in-container Checkit. Legacy billing/TSR/blacklist jobs remain manual and unscheduled.
