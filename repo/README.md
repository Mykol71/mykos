------
repo
------
- Solution for serving multiple major OS versions of mirrored repos on one RedHat server (Apache) instance.

10/02/2026  3:03 PM
┏━━━━━━━━
┃ @ Mike's Store
┃ )~ repo  1.1
┃━
┃  prod.mykol.com
┃ Space    : 474G
┃ Status   : (running)
┃
┃-Repos
┃ 1. List
┃ 3. Delete
┃ 4. Enable/Disable
┃ 5. Sync Repos
┃
┃-System
┃ r. Readme
┃ x. Exit
┗━
Select: 1
https://prod.mykol.com/ostools/
https://prod.mykol.com/rhel-10-for-x86_64-appstream-rpms/
https://prod.mykol.com/rhel-10-for-x86_64-baseos-rpms/
https://prod.mykol.com/rhel-7-server-extras-rpms/
https://prod.mykol.com/rhel-7-server-optional-rpms/
https://prod.mykol.com/rhel-7-server-rpms/
https://prod.mykol.com/rhel-8-for-x86_64-appstream-rpms/
https://prod.mykol.com/rhel-8-for-x86_64-baseos-rpms/
https://prod.mykol.com/rhel-9-for-x86_64-appstream-rpms/
https://prod.mykol.com/rhel-9-for-x86_64-baseos-rpms/
https://prod.mykol.com/support/
https://prod.mykol.com/updateos/
Press enter to continue..



---
mgreen@myk.sh

## Current production repository storage

Production now owns the repository data at `/home/mykos-repositories`, with
`/home/html` and `/var/www/html` as trusted aliases. Use production for future
MYKOS package publishing and existing repository operations. Public access is
limited to MYKOS EL9 and MYKOS EL10; `updateos`, mirrors, EL8 MYKOS, `support`,
`ostools` and inventory stay LAN-only. See the maintained
[repository access and installation guide](../docs/RPM.md#repository-visibility-and-production-storage).
The old server's HTTP endpoint redirects to production; its data remains for
rollback, and its inventory timer is disabled. No physical NIC migration is part
of this change.

## Optional separate repository server inventory

The following describes the optional remote-inventory arrangement for other
deployments. The current production host uses its own local repository storage;
dev consumes production's LAN inventory.


A MYKOS application host can display mirrors stored on a separate repository
server. Install `services/helpers/mykos-repo-inventory` as
`/usr/local/sbin/mykos-repo-inventory` on that server, together with the
`mykos-repository-inventory.service` and `.timer` units. The timer publishes
`/var/www/mykos-repository-inventory/inventory.json` atomically every two minutes.
Serve this file only to the application host's LAN address, and deny its URL in
all public reverse proxies. It contains repository sizes and host storage totals.

On the application host, create root-owned `/etc/mykos/repository-source.json`
(mode 0600), for example:

```json
{
  "host": "repo.example.com",
  "inventory_url": "http://192.0.2.5/_mykos-inventory.json",
  "public_base_url": "https://example.com/repos/"
}
```

The pane uses the remote collection time, repository names, latest RPMs, service
states and storage figures. Inventories older than 15 minutes are rejected.
Requests have an eight-second timeout, a 1 MiB limit, and do not follow redirects.
Remote panes are read-only: sync, service and deletion operations must be performed
on the repository server. Public download links use `public_base_url`.

## Repository synchronization scope

Run `./bin/sync_repos` from this directory to synchronize RHEL 7, 8, 9 and 10,
or pass one major, for example `./bin/sync_repos 9`. Explicit `6` selects final
RHEL6.10 with valid host entitlements. Clone keys such as `centos6`, `oracle7`,
`scientific6` and `springdale7` select the corresponding final vendor archive.
Legacy selections are opt-in; the default stays RHEL7–10. Existing repository
files are preserved.

## Verified publication and background budget

The [maintained synchronization instructions](../docs/RPM.md#safely-synchronize-the-rhel-mirrors)
cover service/CLI invocation, prerequisites, limits, cancellation and recovery.
Downloads and RPM signature checks occur in private COW staging. Only complete,
checksum-validated repository trees are published, using atomic exchange while
preserving older immutable URLs. Service and CLI workers share the configured
background slice; the live tree is never a writable worker mount.

The [Apache access policy example](apache-repository-access.conf.example) keeps
`updateos` LAN-only across direct and `/repos/` aliases, including index assets.
Only the EL9 and EL10 MYKOS trees are public.

DNF workers for RHEL 8, 9 and 10 keep general plugins disabled in their private configuration, then enable only `reposync` for the actual download command using `--setopt=plugins=1 --disableplugin=\* --enableplugin=reposync`. Leaving all plugins disabled makes DNF report that the `reposync` command is unavailable. The allowlist does not activate subscription-manager or change the host DNF configuration.

## Serving optional legacy mirrors

Generate the reviewed Apache fragment with `python3 bin/legacy_aliases.py`.
The example `legacy-repositories.conf.example` defines explicit direct and
`/repos/` aliases for the legacy trees and restricts them to the two trusted
LAN networks. Include it inside each repository virtual host, before any
generic `/repos/` catch-all alias. Review the network ranges for your site,
run `httpd -t`, reload gracefully and check both allowed and denied clients
before publishing mirrors. The generator does not modify Apache automatically.
Do not add public grants for legacy mirrors or `updateos`.
