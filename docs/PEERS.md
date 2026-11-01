# MYKOS peer hosts

A peer connection gives administrators reciprocal control of two MYKOS hosts over
an independent IKEv2 VPN. Both dashboards show a complete Host pane for each
node, labeled by hostname and address. Each pane retains its own live performance
graph, metric toggles, history slider, status, diagnostics and hardware details.
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
