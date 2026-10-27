# Users, passwords, roles, and API keys

Browser accounts, API keys, and backup SSH accounts are three independent identity
systems. Creating one does not create the others. All examples below run on the
installed Linux host. Passwords are prompted on the terminal; do not pass them in
command arguments. The root wrappers switch to the `mykosweb` account internally.

## Browser accounts

```sh
sudo /usr/local/sbin/mykos-users create alice --role admin
sudo /usr/local/sbin/mykos-users create observer --role user
sudo /usr/local/sbin/mykos-users list
sudo /usr/local/sbin/mykos-users passwd alice
sudo /usr/local/sbin/mykos-users role observer admin
sudo /usr/local/sbin/mykos-users role observer user
sudo /usr/local/sbin/mykos-users revoke-sessions alice
sudo /usr/local/sbin/mykos-users delete observer
```

Usernames are 1–64 ASCII letters, digits, underscores, or hyphens. Newly set
passwords must contain at least 12 characters and match the confirmation. Passwords
are salted with random 16-byte salts and hashed using scrypt (`n=16384`, `r=8`,
`p=1`). The database stores hashes, not plaintext passwords. Sessions last eight
hours; only SHA-256 session-token digests are stored. HTTPS-only, HttpOnly,
SameSite=Strict cookies are enabled by the installed configuration.

`create` refuses existing users. Use `passwd` to reset a password while keeping the
role. Password resets, role changes, deletion, and explicit revocation delete that
user's existing sessions immediately. Existing users must log in again. Role changes
apply to subsequent requests, but an already connected terminal is not reauthorized
on every byte; close its session/service if immediate terminal disconnection is
needed. Deleting the final admin is permitted: a root operator can create another.

| Capability | `user` | `admin` |
|---|---|---|
| Dashboard, snapshots, node details | Yes | Yes |
| Container list, recent logs, processes, IPsec status | Yes | Yes |
| Execute Checkit diagnostic | No | Yes |
| Stage/check job status/stop POS | No | Yes |
| Browser terminal (when host enables it) | No | Yes |
| List backup accounts and repositories | Yes | Yes |
| Manage backup SSH accounts/keys/subscribers and repositories | No | Yes |
| Create browser accounts or API keys through HTTP | No | No |

Local root or `mykosweb` access is required for browser/API identity management; no HTTP management
endpoint is provided. Administrators have operational privileges, not OS account
management rights. The legacy `/api/admin/containers` path name does not mean all
its read-only operations are restricted to administrators.

### Upgrading an existing installation

The `users` table receives a `role` column without replacing password hashes or
other identity files. Existing accounts default to `user`. The former name-based
allowlist (`admin`, `kpugh`, `mgreen`) is intentionally not a source of new privileges.
Before restarting the updated web service, explicitly promote intended operators:

```sh
sudo /usr/local/sbin/mykos-users list
sudo /usr/local/sbin/mykos-users role YOUR_EXISTING_OPERATOR admin
```

The old `/opt/mykos-web/create-admin.sh NAME` entry point creates a **new** admin
through the same manager. It does not reset existing accounts; use `mykos-users passwd`.
No restart is needed for ordinary password or role changes.

## API v2 keys

```sh
sudo /usr/local/sbin/mykos-api-keys create monitoring read
sudo /usr/local/sbin/mykos-api-keys create automation admin
sudo /usr/local/sbin/mykos-api-keys list
sudo /usr/local/sbin/mykos-api-keys revoke KEY_ID
```

Labels accept 1–64 ASCII letters, digits, periods, underscores, and hyphens. Scopes
are exactly `read` or `admin`. Creation prints the complete `mykos2_...` key **once**.
Keys migrated from an earlier project name retain their exact values and remain valid with the new API URL and header.
Save newly created keys in a restricted secrets store immediately. Listing returns ID, label, scope,
creation time, and revocation time, never the secret or its digest. Times are Unix
seconds. Only SHA-256 key digests are stored in `data/api_keys.db`.

A `read` key can inspect monitoring snapshots, logs, processes, IPsec, and inventory.
An `admin` key also permits Checkit, stage eligibility/status, staging, and stopping.
Keys are global to this host's API; there are no per-shop restrictions. Keys have
no automatic expiration. API v2 operations accept only API keys; browser sessions
do not authorize them. API keys do not authorize browser login or browser-session
terminals. Admin keys can manage backup accounts/SSH keys/subscribers and repos,
retrieve backed-up client keys, and open the separate API v2 terminal (when the
host enables it). Read keys can inspect Host metrics and Backup/Repos inventories.

Rotation is create–switch–verify–revoke:

1. Create a replacement with the same required scope and a new label.
2. Store it and update consumers without logging the secret.
3. Exercise an intended read request (and authorized job behavior if required).
4. List keys, find the old key's ID, and revoke it.
5. Confirm the old key returns HTTP 401. Revocation affects subsequent checks.

There is no reveal, un-revoke, or in-place secret change. Create a replacement after
loss or exposure. Revoking a key does not cancel an already running staging job.
Keep separate keys for integrations so revocation affects only the intended consumer.

The older `/api/monitor/*` API uses a separate bearer token in `data/api-token`. It is
retained for compatibility but fresh installations do not generate this token. Prefer
API v2. If an existing integration requires it, create a random restricted token file
as `mykosweb`, update that consumer, and replace the file to rotate it. Do not copy the
historical archive's token. Legacy tokens have read-only access to monitor endpoints.

## Backup SSH keys

Use `mykos` → Backup to create an account and install its client public key. This is
separate from API keys. `change_account_key` replaces its authorized key; it does not
rotate API credentials. The legacy `get_cloud_key` script retrieves backed-up private
SSH key material into `/home/tfsupport/keys`; its claimed nightly cleanup is not
configured by this installer. See the script catalog before using it.

Identity backups need explicit `backup --include-data`; store them encrypted off-host.
Restoring one can replace passwords, roles, sessions, and revocations. Ordinary
source installs preserve identity databases and never import the original archive.
