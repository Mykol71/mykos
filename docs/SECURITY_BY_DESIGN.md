# MYKOS security by design

[Documentation index](README.md) · [Architecture](ARCHITECTURE.md) · [Identity and access](IDENTITY.md) · [Developer’s guide](DEVELOPERS_GUIDE.md)

**How MYKOS separates trust, limits routine privileges, protects identities, and preserves customer state.**

Implementation baseline: release **1.5.20261024-154**, reviewed for this guide on
29 October 2026. This document explains implemented controls and their limits.
It is not an independent security assessment, penetration-test report, or
certification. Publishing it adds documentation, not new security enforcement.

## The design starts with explicit trust boundaries

MYKOS manages operating systems, customer data, networking, and administrative
consoles. Those operations require substantial host privileges. Its design
separates the component receiving HTTP requests from the components that perform
host operations, then checks identity and authority at the appropriate boundary.

| Boundary | Implemented control | What it does not establish |
| --- | --- | --- |
| Browser to web application | Account authentication, session checks, route permissions, and configured-Origin checks on browser mutation paths | A visible button or hidden pane is not authorization |
| Integration to API v2 | Hashed API keys with `read` or `admin` scope | Keys are host-wide, not restricted to an individual customer ID |
| Web service to root helper | Unix socket permissions, kernel peer credentials, structured requests, and action/argument validation | The helper identifies the calling service account, not the human user |
| Host to managed environment | Explicit guest identity, storage ownership, lifecycle locks, and native or VM-backed execution | Rootful containers and a shared host are not a complete hostile-tenant isolation guarantee |
| Source and packages to installed host | Signed RPMs, checksums, managed-file records, preservation checks, and rollback archives | A valid signature does not prove the signed code is free of vulnerabilities |
| Runtime state to recovery material | Private state paths and deliberate inclusion of sensitive data | Backups and snapshots are not automatically encrypted or safe to publish |

The [architecture diagram](diagrams/architecture.svg) shows the service and
observation paths. Read the boundaries as cooperating checks: a root helper’s
peer check does not replace HTTP authorization, and an authenticated HTTP request
does not remove the need to validate a host operation’s arguments.

## 1. Keep the HTTP process out of ordinary root execution

The web service runs as `mykosweb`, not root. Its systemd unit applies
`NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, and
`PrivateTmp=true`, with `/opt/mykos-web/data` as its declared writable data path.
These settings reduce what the HTTP process can modify directly and keep its
normal filesystem access separate from host administration.

Privileged actions cross a Unix socket to dedicated root helpers. The shared
runtime reads Linux `SO_PEERCRED` and accepts root or the `mykosweb` UID. Requests
are newline-delimited JSON with a size bound and socket timeout. The dispatch
layer validates the selected operation and its arguments before performing it.
Many operations use fixed subprocess argument arrays rather than constructing a
shell command from browser text.

This is privilege separation, not a claim that a compromised web account has no
power. The allowed root operations remain powerful. Administrative terminals and
the administrative scheduled-command feature intentionally expose broader
execution capability. Treat those permissions as host-administration privileges;
they are not a safe sandbox for untrusted users.

**Source:** `services/systemd/mykos-web.service`,
`services/web/container_admin.py`, `services/helpers/mykos_runtime.py`, and
`services/helpers/mykos-container-helper`.

## 2. Separate human sessions from integration credentials

Browser accounts and API v2 keys serve different callers. Guest OS accounts,
backup SSH keys, and VPN pre-shared keys are separate identity systems again.
Creating or rotating one does not automatically create or rotate the others.

Browser passwords use randomly salted scrypt hashes. The identity database stores
password hashes rather than plaintext passwords. Browser session tokens are
random values; the database stores their SHA-256 digests and expiration times.
Sessions expire after eight hours. Cookies are HttpOnly and SameSite=Strict.
The Secure cookie flag follows the configured HTTP/HTTPS deployment mode.

Password resets, role changes, account deletion, and explicit session revocation
invalidate that account’s stored sessions. This affects later authorization
checks; an already connected terminal is not reauthorized on every byte and may
need to be closed explicitly for immediate disconnection.

API v2 keys use random secrets and stored SHA-256 digests. Authentication compares
the supplied key’s digest with the stored value using a constant-time comparison.
The complete key is shown at creation, while listings omit both the secret and
its digest. Keys can be revoked, but have no automatic expiration. Rotation is
create, switch consumers, verify, then revoke the old key.

Hashing reduces the exposure of usable credentials in a database copy. It does
not make the database nonsensitive: password hashes can still be attacked offline,
and a stolen live session or API key can still be used until it expires or is
revoked. Protect the running host and credential stores as well as the hashes.

**Read next:** [Identity management](IDENTITY.md).  
**Source:** `services/web/mykos_users.py`, `services/web/mykos_api_keys.py`, and
`services/web/app.py`.

## 3. Enforce permissions on the request, not the page layout

The released browser roles are `user` and `admin`; API v2 scopes are `read` and
`admin`. HTTP handlers enforce the required authority. Disabling an action button
or hiding an OS pane is a usability choice, not a security control.

Read access still exposes operational information such as inventory, logs, and
process details. Administrative access includes substantial lifecycle and
configuration authority. API keys are scoped across this host, with no per-ID
restriction. Identity management itself uses local tools rather than a general
HTTP account-management endpoint.

Some data has an additional ownership rule: private operator notes require the
named owner’s browser session, including through the corresponding v2 routes.
An API key cannot prove that person’s identity. Shared customer-ID notes are a
different feature: signed-in users can edit them, with revision checks to detect
stale updates. Do not infer a private customer boundary from an ID or POS label.

The proposed application-specific role grid and fully scoped customer delegation
are not established released capabilities. Plan access around the permissions
that exist, rather than the permissions a deployment would like to have.

## 4. Check browser origin and control what is rendered

Browser login/logout and operational mutation paths check the configured Origin
alongside their applicable authentication and permission checks. Browser terminal
connections check their expected origin and session before accepting the
connection. This helps reject requests initiated from an unintended website.
HttpOnly and SameSite cookies provide additional, different browser protections.
These checks do not replace encryption or make an authorized administrator safe
to treat as untrusted.

Dynamic values must be escaped for the context in which they are displayed. For
example, the environment title is HTML-escaped before insertion into the login
page. Client-side rendering also uses escaping for dynamic labels and values.
Continue reviewing each new rendering path; an existing escape helper is not a
blanket guarantee against script injection elsewhere.

Supporting documentation is deliberately public, pre-rendered, and served through
an explicit file allowlist. The viewer rejects symlinks and paths outside its
root, sets a restrictive documentation-specific Content Security Policy, and sends
`X-Content-Type-Options: nosniff`. That policy belongs to the documentation viewer;
it is not a claim that the dashboard or Swagger page has the same policy.
API documentation and schema visibility do not authorize the operations they
describe. Do not put secrets in public guides or examples.

**Source:** `services/web/app.py`, `services/web/api_v2.py`, and
`services/web/supporting_docs.py`.

## 5. Treat network exposure and encryption as deployment decisions

Default IP-based installation uses HTTP. Domain HTTPS is an explicit setup mode,
and the Secure cookie setting must match that mode. A password hash in storage
does not protect credentials or session tokens while they cross an unencrypted
connection. Use the [HTTPS procedure](BOOTSTRAP.md) when deploying across networks
that require transport protection.

Guest console mappings, administrative access, Apache content permissions, VPN
selectors, and firewall/router rules need explicit configuration. Creating a
guest does not automatically establish the intended external access policy.
The current deployment’s local-only Apache webroot policy is a site configuration,
not an isolation guarantee for every MYKOS installation or proxied application.

VPN state also needs careful interpretation. A reported security association is
not proof of complete application connectivity, correct remote authorization,
or protected DNS and return traffic. Verify the selected mode and actual traffic
path using the [networking guide](NETWORKING.md).

## 6. Protect lifecycle identity, storage, and customer data

Lifecycle code validates IDs and resolves the intended managed environment.
Ambiguous identity is an error, not a reason to operate on the first matching
container. Locks and persisted job states coordinate work so that concurrent
requests do not independently manipulate the same instance or capture. Atomic
state replacement reduces the chance of readers accepting partially written
metadata. These controls protect integrity and recovery as well as availability.

Native containers share the host kernel. VM-backed environments have a guest
kernel but still depend on a privileged wrapper, host devices, storage, and
networking. Evaluate the actual workload and host configuration before using a
shared server for mutually untrusted parties.

Compatible native backup composition can mount customer trees read/write.
Application writes can change those trees. Use a separate working copy when a
support, rehearsal, or training environment must preserve the original backup.
Instance-private account, hostname, and configuration handling must not become a
host-wide ownership rewrite or a modification of a shared customer backup.

Intentional snapshots are separate from automatic startup state. VM memory/device
captures must remain paired with their corresponding disks and compatible runtime
settings. Memory captures can contain credentials and other private application
data; keep them protected and outside source archives and RPMs.

**Read next:** [Installation and recovery](INSTALL_SERVICES.md),
[image lifecycle](IMAGE_BUILDS.md), and [VM storage](DESKTOPS.md).

## 7. Make updates verifiable and recovery deliberate

MYKOS releases use signed binary and source RPMs. Verify the package signature
with an independently trusted MYKOS public key and keep signature checking
enabled. SHA-256 manifests detect byte changes; an untrusted checksum delivered
with an untrusted file does not authenticate its publisher.

The installer tracks managed files and preserves customer runtime state during
ordinary source updates. Publication procedures compare operator documentation
and POS sources, retain rollback material, and check that existing guest identity,
start times, notes, accounts, and Apache configuration survive the update.
A source rollback archive is not a complete backup of customer disks or every
external application dependency.

Identity-data restoration deserves its own decision: restoring a database can
restore old passwords, sessions, permissions, or revocation state. Source-only
recovery and data recovery are different operations. Store sensitive backups
with appropriate encryption and access controls; the existence of a backup
command does not establish encrypted storage by itself.

**Read next:** [Signed RPM installation](RPM.md) and
[installer recovery](INSTALL_SERVICES.md).

## 8. Know what this design does not claim

The controls above reduce specific risks. They do not establish all of the
following protections automatically:

- Strong isolation between hostile tenants on a shared privileged host.
- A complete per-customer or per-application authorization model.
- MFA, comprehensive login throttling, or every desired edge/network protection.
- Automatic guest OS patching or remediation of an old application’s vulnerabilities.
- Encryption of every disk, backup, capture, or secret store at rest.
- A complete tamper-evident audit trail, independent security certification, or
  continuous compliance scanning.

The current architecture includes shared host observations, job records, logs,
and selected revision/editor metadata. Those are useful operational evidence;
they are not interchangeable with a dedicated security audit system. Planned
compliance scanning and configurable application alerts remain future work.

## Preserve the design when extending MYKOS

For a new capability, identify the caller, required authority, data owner, and
privileged side effects before choosing the UI. Validate both the HTTP request
and the helper operation. Keep guest identity and storage selection explicit,
use the existing locks, bound long operations, and record failure without
silently granting broader access.

Verify rejection paths as well as successful actions: insufficient permissions,
wrong origin, invalid IDs, ambiguous ownership, stale revisions, and requests
from the wrong local socket peer. Use fixtures or an allocated test environment
for those checks. A passing mocked test supports the tested boundary; it is not
proof that host, network, backup, and guest isolation are all secure in production.

When handing the change to another human, record the boundary it crosses, why the
caller is allowed to cross it, what state must survive, how failure is reported,
and how access can be revoked or the change recovered. See the
[developer’s guide](DEVELOPERS_GUIDE.md) for the source map and change workflow.
