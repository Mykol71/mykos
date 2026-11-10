# MYKOS peer hosts

A peer connection gives administrators reciprocal control of two MYKOS hosts over
an independent IKEv2 VPN. Both dashboards show a complete Host pane for each
node, labeled by hostname and address. The embedded pane retains its own compact live performance
graph, status, diagnostics and hardware details. The full node also exposes metric
toggles and the history slider.
Use the small MYKOS flower in the peer Host's **Actions** column for guest,
image, snapshot and repository controls.
All operations stay bound to that node, including terminals and desktop consoles.
The peer + opens the selected host’s own signed-in dashboard for enrollment.

## Pair two hosts

1. Sign in as an administrator on each host. Both must have this peer-capable
   MYKOS version, HTTPS on port 443, Libreswan, and reachable IPv4 host addresses.
2. Select the Host **Peer +**, or **+** in its **Actions** column when connected.
   Enter this host's address and the other host's address, then select **Create invitation**.
3. Copy the private invitation to the other host's **Connect a MYKOS host** dialog
   and select **Accept and connect** within 15 minutes. Protect the invitation
   like an administrator credential; it grants reciprocal administrator controls.
4. Confirm both hosts show **Up** under **Peer** and their distinct hostnames,
   graph readings and hardware. A node with a disconnected VPN displays Offline;
   its previous controls are removed instead of being redirected to this host.

Only the two host /32 addresses enter this VPN. Default routes, guest subnets,
NIC assignments and existing guest/site VPN definitions remain unchanged.
Firewalld rules, when needed, accept IPsec only from the selected peer address.
Existing site VPN inspection/removal remains separate from peer disconnect.

## Trust and removal

The invitation binds a stable node ID, the expected address, one-time pairing
material and the host's exact public TLS certificate. Each direction has its own
revocable machine credential. Credentials stay in the servers' private peer
registries and never appear in browser proxy requests. Browser administrators
must remain signed in locally for every proxied operation. Long-lived streams
recheck authorization every 15 seconds. Mutating requests are sent once.

Personal notes and dashboard pane notes remain local and are excluded from the
peer API, as are browser login/logout, API-key administration and further peer
management. Shared customer notes are also excluded. Peer credentials cannot
forward through a second peer. An administrator of either paired machine has
broad operating control of the other; pair only hosts you trust equally.

**Disconnect peer** immediately revokes local trust and removes this peer's tunnel and
owned firewall rules. It also requests revocation on the other node when that
node is reachable. If the other node is offline, disconnect it there separately.
Expired invitations and revoked connections are cleaned by the web service's
maintenance worker. Teardown failures retain ownership records for retry.

Pending, expired and offline pairings have a local **Remove pairing** button beside their status, even when the embedded peer pane cannot load. Confirming revokes that pairing through the same local administrator endpoint. A failed teardown remains visible and can be retried. Removing a failed pairing does not affect other peer connections.

## Current network and certificate limits

The initial supported topology uses directly reachable, distinct IPv4 host
addresses, with HTTPS on port 443 and IPsec UDP 500/4500 and ESP allowed between
them. Automatic NAT traversal/topology discovery, overlapping private networks,
IPv6 peers and relay connections are not provided. Verify routing before pairing
hosts at different sites. RHEL system crypto policy selects the IKEv2 algorithms.

A changed or expired HTTPS certificate causes the connection to fail closed.
Disconnect and pair again after verifying a replacement certificate. Do not
bypass certificate validation to recover a peer connection. VPN setup failures
leave existing VPNs and customer instances intact; review the dedicated
`mykos-peer-<connection-id>` Libreswan connection and service logs without
sharing its PSK or the private peer database.

### Cancel enrollment

Cancel closes Add Peer without validating the address fields or sending a pairing request, and clears the invitation text. Cancel and Escape are disabled while an invitation or acceptance request is in progress; wait for its result before closing.

### Pairing window completion

A successful Accept and connect closes the pairing window immediately and clears its private invitation text. Creating an invitation keeps the window open for copying; after the other host connects, the normal peer refresh closes the window for that matching invitation. A failed or still-pending connection keeps its message visible. Other existing peer connections never close an unrelated invitation window.

## Compact pane refresh

The embedded pane monitors only its Host. It does not poll hidden guest details,
image inventory, repositories, backups or snapshots sections. Its controls
attach when the document is ready, even if an optional map or terminal library
is still loading. The flower opens the complete node dashboard with its normal
refreshes and controls. Metrics, graph history and command targets remain bound
to the selected peer.

## After a host operating-system upgrade

Use the MYKOS RPM for the new host release after a supported Red Hat Leapp
upgrade. An EL8 Python environment can survive the operating-system upgrade;
the current RPM checks and migrates it to the target runtime. A Python 3.9
`ssl.VERIFY_X509_PARTIAL_CHAIN` error during Accept indicates an old web runtime.
See [RPM installation and upgrades](RPM.md) for the automatic migration and
rollback behavior. Existing scoped IPsec includes such as
`include /etc/ipsec.d/mykos-*.secrets` are supported when they cover peer files.

## Copy an instance to a peer

A full administrator signed in to the source host can select **Copy instance to a
peer** in a persistent instance's Actions. This creates an independent instance;
the original stays on its host. Peer-forwarded views and scoped operator accounts
do not offer this control.

1. Connect the peer first. Both hosts need a version supporting instance copies
   and any extension required by the selected instance.
2. Select the destination peer and an eight-digit destination ID. An existing
   destination container or saved data is never overwritten.
3. Select **Check copy** and read the proposed capture mode. **Copy with RAM
   state** is offered only for a running VM with a compatible CPU and runtime.
   Otherwise **Copy and restart** captures disks/filesystem and starts a fresh
   guest process. Native Linux copies currently use restart mode because their
   existing CRIU snapshots are bound to the original container.
4. Confirm the proposed copy. The source can pause during consistent capture;
   it resumes before the encrypted upload. Native capture has a two-minute work
   budget and an independent recovery timer. Closing the dialog leaves the host
   worker running. Reopen Copy to see recent transfer states.
5. Open the destination host to inspect the new instance through its console or
   root terminal. Both its data and lifecycle are independent of the source.

**Copies start with guest networking isolated.** This prevents a copied VPN or
guest network identity from becoming active alongside the source. VM console
replies remain available; native copies have no attached network. Isolation
survives container restarts. This release does not provide an automatic network
activation button: configure a distinct guest/VPN identity and have a full host
operator explicitly provision networking before using the copy on the network.

Memory mode retains the captured running RAM only for the first destination
start. Later starts boot its current disks normally. CPU model/vendor/features,
architecture, exact runtime image, RAM settings and checkpoint integrity are
checked; a failed RAM restore is reported as a failure, never described as a
successful memory copy. Cold-copy mode does not require a portable RAM stream.

The transfer includes the pinned runtime image, VM disks or native writable
filesystem, and supported exclusive instance mounts. Shared or unmanaged mounted
data is rejected rather than silently omitted. Host accounts, browser sessions,
peer credentials, host configuration and personal/pane notes are not copied.
Paired hosts retain their existing administrator trust and pinned TLS checks.

Two transfers may be active per host. Space reservations, startup capacity and
available RAM are checked before work and again before import. SHA256 checks
cover chunks, the capsule and image configuration. Retries are idempotent and a
lost response cannot cancel an importing or verified destination. If a peer goes
offline after import begins, the source reports an unknown destination outcome
until it can reconcile the receipt; check the destination before retrying with
another ID.

Incomplete uploads expire after 24 hours. An owned cleanup timer removes their
staging and stale upload chunks, and recovers interrupted source captures.
Failed recovery keeps its ownership records and original checkpoint backup for
operator review; it never guesses at unrelated containers or files. Operational
receipts are root-private under `/var/lib/mykos/instance-transfers`; transfer
staging is under the configured VM storage's `.peer-transfers` directory.
