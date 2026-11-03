# Validation record and remaining integration work

## Release224 — RHEL 8 RTI guest profile

Adds a separate rh8rti native image and exact requested package groups/list with updateos. Native selection, EL8 platform/RPM database handling, image identity, staging/snapshots and dashboard classification are included. The normal rh8 recipe, published releases, host support matrix and existing workloads stay preserved. Package/group availability, a real dev image build and installed-package checks, native instance lifecycle, full regressions and signed dev/prod validation gate publication. The user explicitly selected publication with RH8RTI RAM snapshot/restore actions disabled pending a later CRIU compatibility fix. Disabled actions are enforced in the backend as well as the UI; other OS snapshots remain unchanged.

## Release223 — LAN-only updateos listing

The dashboard inventory includes the updateos download tree as read-only while the destructive repository path continues to reject it. The download landing page selects a protected LAN index only for loopback and the existing two LAN ranges. Direct private-index requests and updateos downloads remain forbidden to public clients. Focused inventory/UI checks and isolated Apache LAN/public tests gate dev/prod promotion. Existing updater fixes and all release222 behavior are retained.

## Release222 — Fedora GNOME desktop VMs

Adds separate `fedora43vm` and `fedora44vm` selections, preserving both native
Fedora recipes and existing instances. The GNOME desktop includes Xwayland for
X11 applications. Console opens the authenticated MYKOS graphical console;
Fedora retains its normal account login. The serial console also requires normal
guest login. SELinux stays enforcing; only a non-secret readiness marker receives
the vendor QGA runtime-file label.

Both official Fedora Cloud images were built on RHEL9 dev with 2 GiB RAM,
2 vCPUs and 12 GiB swap. Clean installed boot, committed startup RAM, independent
instance start, live RAM snapshot/restore and isolated memory preview passed.
A guest tmpfs sentinel returned to its saved value after restore. GNOME login
and an X11 GNOME Console application were checked directly. Active-desktop
snapshot checks, final regressions, signed staged installation and live dev/prod
verification gate publication; exact receipts remain with the release.

The standard-VGA GNOME/X11 desktop was checked after restore through the actual
browser console. Fedora retains its normal idle blanking and screen lock: wake
the console and unlock the account to return to the saved application session.
Interrupted disk conversion cannot publish a partial VM disk. No guest disks,
RAM, credentials or generated seeds enter the application packages. Existing
updateos 2.2.7 repairs, terminal fitting, repository LAN restrictions and all prior
release221 behavior remain. Fedora desktop direct guest VPN setup is not enabled;
native Fedora's existing VPN path is unchanged. Live EL10 host/native-CRIU fresh
installation and existing macOS provenance limitations remain unverified.


## Release212 — Review repairs

- Server-side terminal reauthorization before acceptance/input and every five seconds while idle; session/API-key revocation, expiry, role loss and invalid filesystem-preview leases close the transport.
- Persistent bounded login admission before password hashing:20 attempts/source/minute,10 attempts/account/five minutes,429 plus Retry-After, automatic recovery, hashed bucket identities and bounded storage. Valid login resets only the account bucket. Username/password inputs are bounded; existing password minimum policy remains.
- Native builds hold admission through final commit and use private generated kickstarts, archive destinations and Docker contexts, cleaned on every exit. Lifecycle protection recognizes both private and legacy kickstart paths. Source kickstarts are preserved.
- RPM and source removal share the canonical complete control-plane stop/disable list, including idle-base, preview GC and speed timers, without targeting persistent guest scopes.
- GET/PUT personal-note OpenAPI operations now declare the required owner browser-session authentication.
- Speed documentation matches midnight/noon Central scheduling and13-hour successful-cache freshness; immediate staleness on failure remains. Identity/security/build/removal guides updated.

Includes release210 behavior. Publication is gated on focused regression/concurrency/security tests, the full Linux/dev Python suite, JavaScript and docs checks, signed staged installation and live dev then production verification. Exact results and cleanup receipts accompany the release.

Limits: no new guest OS image build or snapshot restore certification. EL10 application packages are signed/published without a live EL10 or complete native CRIU dependency installation test. Existing macOS provenance limitations remain. Login throttling is not MFA or comprehensive external edge protection.



## Release210 — Add Peer cancellation

Fix Add Peer cancellation: Cancel closes the dialog even when required IPv4 fields are empty. It does not submit the form or send a pairing request. The private invitation is cleared on close. Cancel/Escape remain blocked while a pairing operation is in flight, avoiding misleading cancellation of a running operation.

The original failure was reproduced in a real browser: Close triggered required-field validation and left the dialog open. The fixed dialog closed with empty required fields and cleared its synthetic invitation. A regression covers Cancel, invitation clearing, no backend request and busy/Escape protection.

Includes all release209 behavior. Full regression, documentation, signed staged-install and live dev/prod checks gate publication; exact receipts are retained with the release. Running guest identities/processes, peers, host settings, notes and repository restrictions are preserved. Test/build/deployment scratch is cleaned.

Limits: no new guest image build or snapshot restore certification; EL10 app RPMs are signed/published without live EL10/native CRIU dependency validation; existing macOS provenance limits remain.



## Release209 checkpoint — November1,2026

This release packages all accepted changes since signed release197.

### Dashboard
- Larger, aligned CentOS and Oracle Linux marks; CentOS uses transparent vector artwork with provenance. Oracle keeps the approved20px size with independently aligned icon and label columns.
- Compact peer panes have an unlabeled performance graph behind the status/actions row, without the past-view slider. Gray hardware details sit outside the pane as a footer. Local/full-node graph controls retain their layout.
- Peer hover remains transparent. Peer backgrounds are slightly more transparent in dark/light themes, with the map either on or off; text/actions remain fully visible.
- Footer leaves alternate with text: theme, email, pane preview, version, map, host OS. Contact link is mgreen@mykol.com.
- Container-count bar opacity is unchanged; the requested opacity change was canceled.

### Network speed
- Official Ookla checks run at midnight and noon America/Chicago; dev is staggered by30seconds. Prior minute/boot scheduling is cleared by the established host-local overrides.
- Cached results remain available between checks, with an appropriate stale window and a separate short duplicate-attempt guard. Reading the dashboard never launches a speed test.

### Packaging and operations
- Signed application and source RPMs for RHEL9/10 hosts; RHEL8 remains a guest target only.
- Existing containers, guest RAM, peers, VPNs, private notes, host-local credentials, network and repository access restrictions are preserved during promotion.
- Only MYKOS EL9/EL10 repositories are public; updateos and other protected repositories stay LAN-only.
- Includes source archive, public signing key, checksums and maintained documentation. Prior published releases remain immutable.

Validation evidence accompanies this release: full Python/JavaScript regressions, documentation checks, signed staged installation, live dev then production installation/endpoint checks, repository metadata and GitHub asset verification, and test/staging cleanup. Exact results are recorded in the private release receipts; packaging is gated on their passing.

### Known limits
- No new guest OS image build or snapshot restore certification in this application release.
- EL10 application RPMs are cross-target built and signed; no live EL10 host or complete native CRIU dependency installation has been validated.
- Existing macOS upstream licensing/provenance limitations remain; bundled notices are preserved.
- Twice-daily speed checks do not provide continuous bandwidth measurements; an unavailable/failed cached result remains marked stale.




[Documentation index](README.md)

## Historical source validation — 23 October 2026

The following historical record describes the original source/staging delivery,
not the current deployment status.

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

## Historical Linux integration checklist

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

## Released baseline and this documentation review — 28 October 2026

The reviewed baseline is **1.5.20261024-146**, source archive SHA-256
`912a4ab3b98cce44b8d44ada7521215ac44cc6a03604a8d7a05e75eb43664cbb`.
The installed production RPM was queried directly. Both production documentation
folders were fetched, compared by relative path and SHA-256, and preserved before
editing; their 41 files matched the clean released source. Historical Word and
`KEY_ISSUES.md` remain byte-for-byte unchanged. The Markdown administrator reference
also remains a historical edition; current task guides take precedence.

Source review covered VPN prompts/configuration/hooks, host and guest namespace
boundaries, shared policy/firewall ownership, DNS, SA detection, reconnect/removal,
backup/private identity mounts, PAM/cgroup fixes, generated kickstarts, console
capture/reset semantics, setup/bootstrap, RPM target selection and cookie behavior.
The unpublished PXE development tree was excluded.

Read-only production observations: release146 installed, shared gateway list empty,
optional gateway timer/guard disabled, main WAN and private bridge routes present,
no MYKOS policy-routing rules selected. Helper `--help` confirmed documented action
names. No Add Gateway reproduction, Meraki API write, VPN/routing change, guest
restart, snapshot operation, identity change or customer-data test was performed.
An empty configured-gateway list does not validate active gateway traffic.

Prior release validation evidence records fresh RH9/RH10 signed package installs,
IP-only application startup and HTTP login/session checks on isolated RHEL UBI
init systems. That evidence is not full bare-metal virtualization, NIC, CRIU or
VM lifecycle validation. The user's vanilla RH9 server test is separate and not
claimed complete here. Site-specific real Meraki negotiation, remote DNS/internet
forwarding, host-only selectors and live reconnect/fail-closed behavior were not
re-tested in this review. Local SAs are insufficient to prove remote egress.

The documentation build validates portable local links/anchors, regenerates SVGs
and light HTML from maintained inputs, and can check generated-file drift. Detailed
commands/results belong with the review's verification artifact; do not mistake
historical test counts above for checks rerun by this documentation task.

During final review, the user separately removed the licensing page and index
entry in production and the local drafts. Both production copies were fetched
again and matched after that removal (39 files). This revision preserves that
operator change; the initial 41-file snapshot remains archived as evidence.


## Release213 — Receiver appearance

Receiver adds an optional silver-face dashboard appearance. Validation covers three-way theme preference/controls, real and stale meter readings, overload display and unchanged history controls. Meters consume existing chart samples with no extra polling. Browser checks and signed install/promotion evidence are recorded with release213. This app release does not recertify OS image builds or restore guest state.


## Release214 — Receiver layout correction

Restores the approved receiver control bank and continuous faceplate, including functional illumination/damping/display controls. Validates original history-node preservation across themes, real count-history bounds, no fake stale readings, keyboard knob/fader interaction and warm count bars. No guest lifecycle changes.


## Release215 — Receiver instrument and source dial refinements

Covers black/white VU faces with bottom blue lighting, smaller meter placement inside the performance window, thicker light-blue/green count columns and the outside royal-blue hostname. Source-dial tests verify enabled-pane filtering, stable selection, safe fallback, equivalent dropdown/range behavior and original visibility preservation across themes. Existing real history/freshness, display/lighting/damping and server permission behavior remain unchanged.


## Release216 — Receiver performance enclosure

Verifies that Display, recall, Illumination and Source controls are inside the enlarged black enclosure, with dark-background label contrast and Receiver-only container-bar fading. Illumination advances in10-point steps and wraps100→0, with marker/readout/storage synchronization. The source knob is a click-to-advance button; the marker, OS label and dropdown update together, wrapping safely through available panes. Browser checks cover knob/fader behavior, source selection and restoration of original history controls on other themes. No server lifecycle changes.

## Release217 — Receiver radio controls

Fixed container/VPN signal indicators replace the Receiver rolling count bars. Tests cover scale boundaries, zero/missing/stale/future readings, one-minute tuning steps/wrap and existing illumination steps. Browser checks verify count/VU/illumination placement, quarter-minute tick spacing, round response/Live buttons, recall/Live behavior, source selection and return to the original controls in other themes. Signed staged installation and guarded dev/prod live checks preserve workloads and host settings. No OS image builds or new guest lifecycle tests are part of this appearance release.

## Release218 — Personal Receiver availability

Receiver is offered only to authenticated `mgreen`. Regression tests cover signed-out/default rendering, saved Receiver preferences before identity verification, owner restoration, other usernames including other admins, direct selector rejection, account switching, logout and identity-generation guards against a late current-user response. No authentication policy, guest lifecycle, host build or repository access rules change.

Receiver218 also standardizes the finish of Source, recall and Illumination knobs; browser computed-style checks compare all three faces/rims.

## Release219 — Receiver count order and recall alignment

Browser checks compare actual meter/recall band center coordinates and VPN/container shared geometry and SVG layer order at desktop and narrow widths. Existing count, history, source and identity regressions remain unchanged; guarded UI hotfix installation preserves workloads and host settings.

Final219 layout checks also verify the82px recall knob is directly beneath Illumination, the response row is above the band, and white radio-band graduations and green numerals use the intended computed colors.

Amber source-lamp checks verify left-to-right choice order, click/dropdown synchronization, one active lamp and fallback when a selected pane becomes unavailable.

The final response bank is32px and its right edge matches the recall band at desktop and narrow widths.

The final signal plot draws the container group first and VPN group second with identical bar coordinates; inactive VPN segments are transparent so the container meter remains visible beneath them.

Component219 checks cover black beveled cases/round feet across visible Host/OS/utility panes, hidden-pane preservation and a seven-segment Host uptime reading. Boundary tests verify days/hours/minutes/seconds, unavailable data and fractional seconds.

Release219 packages the final component layout and one-hour recall. Validation covers JavaScript controls, history retention/API authorization, the complete development Python suite, signed staged installation and guarded live promotion. No guest lifecycle or OS image-build tests are used for this theme release.

One-hour recall tests verify65-minute persistence/read coverage, the oldest full window, bounded sample count, sixty one-minute steps/wrap and scrolling tick motion. History API authorization remains unchanged.

## Updateos and terminal width repair221

Regression tests reproduce a styled Apache stage listing with harmless synthetic scripts, reject404/HTML downloads and traversal, and preserve a cached good script on failed downloads. Terminal tests cover fragmented/coalesced frames, dimension bounds, real PTY window sizes, authorized WebSocket forwarding and legacy raw input. Browser checks use the real xterm/FitAddon display with a synthetic transport, avoiding live user shell input.
