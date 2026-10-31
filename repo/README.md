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

## Separate repository server inventory

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
