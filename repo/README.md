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
mgreen@teleflora.com

## Current production repository storage

Production now owns the repository data at `/home/mykos-repositories`, with
`/home/html` and `/var/www/html` as trusted aliases. Use production for future
MYKOS package publishing and existing repository operations. Public access is
limited to MYKOS EL9, MYKOS EL10 and `updateos`; mirrors, EL8 MYKOS, `support`,
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
or pass one major, for example `./bin/sync_repos 9`. RH6 synchronization is
removed, including its entitlement fallback and credential mounts; passing `6`
returns a usage error. Existing repository files are not deleted by this change.
