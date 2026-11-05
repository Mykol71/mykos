# MYKOS dashboard UI guide

[Documentation index](README.md)

Use this guide to find a feature, identify an icon, and understand what happens
when you select a control. It describes the current dashboard, including compact peer Host monitoring,
the optional Receiver display, and installed extension panes. Controls depend on your role, host configuration, available
images, and resource state. Hover over an icon for its action name; keyboard users
can move between controls with Tab and activate buttons with Enter or Space.

## Contents

- [Start here](#start-here)
- [Page layout and navigation](#page-layout-and-navigation)
- [Host monitoring and graph](#host-monitoring-and-graph)
- [Host buttons](#host-buttons)
- [Peer hosts](#peer-hosts)
- [OS panes and container rows](#os-panes-and-container-rows)
- [Container button reference](#container-button-reference)
- [Images and base sessions](#images-and-base-sessions)
- [Snapshots](#snapshots)
- [VPN and network information](#vpn-and-network-information)
- [rsync backup accounts](#rsync-backup-accounts)
- [Repositories](#repositories)
- [Notes](#notes)
- [Appearance and footer controls](#appearance-and-footer-controls)
- [Common workflows](#common-workflows)
- [Messages and troubleshooting](#messages-and-troubleshooting)

## Start here

Open `/mykos/` on your MYKOS host and sign in with your **browser account**.
The login page also offers theme, API, and Docs links. Browser accounts, API keys,
backup SSH accounts, and guest OS credentials are separate identities.

A standard user can inspect monitoring and permitted inventories. Administrators
also receive management controls; terminal-dependent actions require the host to
enable browser terminals. Some notes controls are restricted to the designated
operator. Missing buttons can therefore be expected. See [Identity management](IDENTITY.md).
Browser account and API-key creation are host command-line operations, not
buttons in this dashboard.

**Check the target before acting:** the local Host pane, an embedded peer Host
pane, a prepared image row, an eight-digit container ID, and a saved snapshot
represent different resources. A terminal on Host operates the server; a terminal
on a container operates that instance. A peer's terminal operates that peer.

## Page layout and navigation

| Area | What it shows or does |
| --- | --- |
| Header | MYKOS branding, signed-in username, Logout, section links, API, Docs, and server time. Section links scroll to visible panes. |
| Local Host pane | Resource graph, history, host status row, actions, notes, and hardware details. |
| Peer Host panes | Connected MYKOS hosts below the local pane, each with its own graph and actions. Compact panes are slightly narrower and right aligned. |
| OS panes | Red Hat, Rocky Linux, AlmaLinux, Oracle Linux, CentOS Stream, Fedora, Ubuntu, Windows, Windows Server, and macOS; SQL Server has its own pane. |
| Snapshots | Saved states grouped by original container ID. |
| rsync | Backup accounts, storage use, and account/key actions. |
| Repos | Repository storage, dates, latest RPMs, links, and available management actions. |
| Footer | Appearance toggles, project/contact links, and host OS identification. |

Panes appear automatically when relevant resources or image choices are present.
The footer's pane preview control can reveal additional panes with **Sample**
rows. Sample rows are examples with disabled actions; they are not deployed
containers, snapshots, backups, or repositories. Available preview families
also depend on access.

The [screen examples gallery](SCREENSHOTS.md) illustrates the dashboard and
snapshot workflows using example data. Those pictures are illustrative and may
predate the latest peer layout; the control descriptions here cover that layout.

## Host monitoring and graph

Select a metric label to hide or show its series. This changes only the graph,
not the server. Labels follow the selected time window; return to Live for the
latest available readings.

| Metric | Meaning |
| --- | --- |
| CPU | Host CPU utilization. |
| Memory | Host memory use, with usage/capacity details. |
| Load | Host load normalized to available processing capacity. |
| System | Usage of the host's system filesystem. |
| Data | Usage of the data filesystem. System and Data may refer to the same filesystem on some installations. |
| Containers | Running customer instances plus snapshot previews against a 50-slot limit. The graph reaches 100% at 50. |
| VPNs | Reported connected VPNs; the graph's count scale reaches 100% at 15. This scale is not a 15-connection admission limit. |

The graph shows a five-minute window. Drag the history slider left to inspect
older windows within the retained hour; select **Live** to return to now.
Hover or focus the graph to inspect readings. Horizontal scrolling, including
Shift plus the mouse wheel, moves through history. Resource readings are marked
high at 75% and critical at 90%. Count bars have their own scales.

Each peer keeps independent live performance data. Its embedded pane shows an
unlabeled performance graph filling the pane behind its host status and action
buttons, with no
metric labels, table-heading labels, or past-view slider. The small gray hardware
and network information line is an outside footer below the peer pane. Open the full node view to
use the labeled graph, metric visibility controls, and history slider. With the map background on, embedded
peer panes share its translucent appearance. Turning the map off restores their
normal pane fill. The hardware line reports available
machine/CPU information and one **Memory** label followed by installed memory
type, configured speed, and DIMM capacity. Missing hardware values are omitted.
This installed capacity differs from the live used-memory value in the row.

## Host buttons

The Host table columns are **Host, Status, Version, IP, Peer, Uptime, Memory,
Actions**. Host-specific controls appear before Console Log, root terminal, and
reboot on the right. A diagnostic expands below the row; selecting the same
control again, or its **Close** button, closes it.

| Control or icon | What happens |
| --- | --- |
| Host name/cube, or non-button row area | Opens Host Details for that host. |
| IP address — Geographic map | Opens saved geographic locations. Hover over markers for system information; green means up, red down, yellow unknown. |
| Peer **+** — Connect another MYKOS host | Opens the peer invitation dialog when no connection status is shown. |
| Peer status — View host IPsec status | Opens reported host IPsec diagnostics. |
| Actions **+** — Add another peer | Opens the local host's peer enrollment dialog when available. |
| Memory value — View host processes | Opens the host process listing. |
| Red trash can — Stop and remove host VPN | Opens the host VPN removal terminal workflow. Read its prompts to identify the selected site VPN. Peer unlink is a separate control. |
| Green document — Host Console Log | Opens host log output. |
| Blue terminal — Host root terminal | Opens an interactive root shell on the selected server; administrator access is required. |
| Red circular arrow — Reboot host | Confirms the named host, then schedules its reboot. This interrupts all containers and connected users on that host. |

## Peer hosts

A peer is another MYKOS server paired for reciprocal administrator control over
an IKEv2 VPN. Read the hostname and address before using any of its controls.
A disconnected peer displays Offline and loses its actionable embedded pane.
Connected peers also have a labeled background-map box. A dashed green line
connects the local host to a peer when both have distinct saved coordinates.
Peers without coordinates have a Connected box marked Location unavailable;
no geographic position or line is invented. Boxes and lines disappear when the
peer disconnects or its status becomes unavailable.

| Peer control | What happens |
| --- | --- |
| Small MYKOS flower — Open full node | Opens that peer's full dashboard in another tab through the current host, including its OS, snapshot, and repository panes. |
| Red broken-chain icon — Disconnect peer | After confirmation, revokes this peer relationship and removes its MYKOS peer VPN/shared controls. Reachable remote revocation is also requested. |
| **+** — Add another peer on the named host | Opens that host's own HTTPS dashboard for enrollment. Sign in there as its administrator; enrollment requires host-local approval. |
| Console Log, terminal, reboot, IP, Memory | Operate on the named peer, with the same meanings as the Host controls above. |

The compact peer action order is **flower, unlink, plus**, followed by any site
VPN removal control and then the common log, terminal, and reboot controls.

To pair hosts, select the local **Peer +** or **Add another peer**, enter both
reachable IPv4 addresses, and select **Create invitation**. Copy the private
invitation into the other host's dialog and select **Accept and connect** within
15 minutes. **Cancel** dismisses the dialog without validating empty fields. Invitations grant reciprocal admin
control and must stay private. Full prerequisites, certificate handling, and
removal behavior are in [Peer hosts](PEERS.md).

Personal, pane, and shared customer notes are not forwarded through peers.
Browser/API-key administration and further peer management also remain local.

## OS panes and container rows

An **image** is a prepared OS and any startup state required to launch it.
A **container** is a particular ID-specific instance with its own writable state,
IP, and hostname. VM-backed guests also run within MYKOS-managed containers.
A **snapshot** saves an instance at a point in time.

At the top of an OS pane, administrators can enter an **eight-digit ID**, choose
an available **OS image**, and select the blue **+ (Start)**. This creates/starts
an ID-specific instance from that image after confirmation. It does not install
a missing OS image. Unavailable images cannot be selected. Use the row's green
triangle to restart an existing stopped persistent container.

For eligible native starts with an existing backup, MYKOS separately asks whether
to mount the original backup folders **read/write**. Accepting means changes
inside the container can change those backup files.

| Column or clickable value | Meaning or action |
| --- | --- |
| ID | Eight-digit instance/customer identifier. Select the row's non-button area to expand details; select again to collapse. |
| Leaf beside ID | Shared customer notes for that ID; separate from pane notes. |
| Status | Reported online/offline/unknown state. |
| Version | Guest OS release, rather than host OS release. |
| IP | Opens network/interface/gateway information and topology. |
| VPN | Status/diagnostics or Add VPN, when supported for that guest. VM families without this control retain column alignment. |
| Uptime | Time since the instance started. |
| Memory | Used memory; select it for processes. |
| Actions | Available console, terminal, lifecycle, snapshot, and VPN controls. |

Expanded details include the image, OS, resources, addresses, and monitoring
freshness. Unknown or stale data is not proof that a container stopped.

## Container button reference

| Icon / tooltip | Action and effect |
| --- | --- |
| Green document — Console Log | For native containers, opens log output. |
| Green document — HTTP graphical desktop | For desktop guests, opens the interactive graphical console in another window/tab. Despite the document-shaped icon, this is not a read-only log. Requires admin access and a running guest. |
| Blue terminal — su - root | Opens the instance's interactive root terminal for native Linux/Ubuntu. |
| Blue terminal — PowerShell commands | Opens Windows/Windows Server command access. |
| Blue terminal — macOS shell | Opens the macOS guest shell. |
| Green triangle — Start container | Starts an existing stopped persistent instance using its saved disks. |
| Red square — Stop container | Stops a persistent instance while retaining its disks. It does not save a new intentional snapshot. |
| Blue camera — Snapshot container | Captures live state and leaves the instance running after completion; a brief capture pause may occur. |
| Red trash can — Delete container and saved data | Requires typing the exact ID. Removes the container, its writable filesystem, MYKOS disks, and VPN settings. Images, intentional snapshots, rsync backups, and external/shared mounts are retained. |
| **+** — Add VPN / Add another VPN | Opens the interactive VPN setup workflow for an eligible running instance. |
| Red VPN trash can — Stop and remove VPN | Opens the instance's VPN removal workflow. This is different from deleting the instance. |

A saved native Linux memory snapshot currently requires its original container
for restore. Keeping that snapshot does not make deleting its original container
safe for a later restore. Legacy auto-remove containers must be converted to
persistent storage before guarded start/stop/snapshot actions become available.

## Images and base sessions

Image/build rows share OS panes with container rows. Their Status can show
**Queued, Installing, Preparing, Capturing, Failed**, or **Image**. A prepared
image displays its size and production time. A build still installing or
capturing is not a ready image, even if its console is usable.

| Image-row control | What happens |
| --- | --- |
| Green document — Installation console | Opens the selected installation's graphical console when provided. |
| Green document — Desktop console | Opens the prepared desktop/base graphical console when provided. |
| Blue terminal — Edit base image / Connect to running base image | Opens a generic base editing session. An idle base can be reopened on demand. This is not an ID-specific customer container. |
| Amber circular arrow — Reset base to captured image | Requires typing the OS name. Discards uncaptured changes to that generic base and recreates it from the saved image/startup state. Close its console first. |
| Red trash can — Remove prepared image and generic base | Requires the OS name. Removes the prepared image, generic base, and startup captures; dependent containers/snapshots block removal. |
| Red trash can — Delete failed image build and disks | Requires the OS name. Deletes that failed build and its private disks. |

**Base edits must be captured before closing the editing session or logging out.**
A base-session disconnect queues a reset and discards uncaptured edits. To keep
changes, leave the base session connected, open the host's `sudo mykos` text menu
in a separate session, select **f. Capture image**, enter the OS, confirm, and wait
for completion. Capture is a text-menu operation; there is no dashboard Capture
button. Existing ID-specific instances keep their existing state.

Idle generic bases can stop after five minutes without an active session. A
prepared image remains available for new instances even when its generic base
is stopped. Do not confuse a stopped base with a missing image.

To build a missing image, use **c. Build image** in the host text menu and the
correct OS identifier. See [Image builds](IMAGE_BUILDS.md) for supported native
and VM types, media requirements, capture behavior, and recovery.

The small gray hardware line below the Host table includes measured internet
`Download` and `Upload` speeds in Mbps when an official Ookla result is available.
The collector measures twice daily, at midnight and noon Central, with the dev
peer offset by 30 seconds. Hover over the line for the measurement
time; failed or older results carry `(stale)`. These measurements are separate from
current network traffic shown on the graphs.

## Snapshots

The Snapshots pane groups saved states by container. Choose the desired capture
in its date/time selector before selecting an action. The displayed capture time
and disk size follow that selection.

| Icon / tooltip | What happens |
| --- | --- |
| Blue circular arrow — Restore selected snapshot | After confirmation, replaces the original container's current disks/live state and interrupts it. This is a rollback. |
| Red trash can — Delete selected snapshot | After confirmation, permanently removes only the selected saved state; leaves the current container unchanged. |
| Green monitor — Preview selected snapshot | Creates a separate temporary inspection session. The original instance and snapshot stay unchanged. |

Desktop previews resume saved RAM and disks. Native Linux previews open the
saved filesystem in an isolated shell; they do not resume captured RAM or
services. Preview networking is blocked, changes are discarded on close, and
sessions have a 30-minute maximum. **Close preview** explicitly ends the session.
Previews consume capacity within the host's 50 running-instance/preview limit.

See [Snapshot Preview](DESKTOPS.md#snapshot-preview) for storage and isolation details.

## VPN and network information

Select a container **IP address** to see its addresses, interface, networks,
gateway, own VPN status, shared gateway, and default tunnel. **Show connected
guests** expands or reduces the topology drawing. Solid green edges indicate a
reported established SA; dashed gray edges are unconfirmed. Shared-network edges
show membership, not a successful reachability test. A site SA alone does not
prove full-tunnel internet connectivity.

Selecting an **Up VPN status** opens IPsec diagnostics. The setup/removal plus
and trash controls open interactive workflows; follow their prompts and check
the target before accepting. Native direct VPNs, shared gateways, host site VPNs,
and MYKOS peer VPNs serve different purposes. See [Networking](NETWORKING.md).
The geographic background represents saved locations, not a packet-path test.

## rsync backup accounts

Backup account management is delivered by a separately installed private business extension. Its dashboard pane, account-management API, transfer setup and implementation are not included in the base MYKOS distribution. Existing customer backup data is preserved during core updates.

## Repositories

The Repos pane shows repository name, disk use, last modification, and latest
RPM. Select a **repository name** to browse its published directory in another
tab. A configured remote source can make this pane read-only, with management
buttons hidden; its Server label identifies the source.

| Control | What happens |
| --- | --- |
| Up arrow — Enable repositories | Confirms, then starts Apache. |
| Down arrow — Disable repositories | Confirms, then stops Apache. This can also disconnect the dashboard and other Apache sites; recovery may require the host CLI. |
| Circular arrows — Sync repositories | Confirms and queues the background repository sync workflow. It may install dependencies and use substantial disk/network capacity. Disabled while sync is active. |
| Row trash can — Delete | Requires typing the repository name, then permanently deletes that repository and its content. |

The `updateos` download tree appears as a read-only entry; it is not an RPM repository and has no Delete action. Its directory remains LAN-only. The repository download landing page shows an updateos link to permitted LAN clients; public visitors see only MYKOS EL9/EL10.

The status line reports storage, Apache state, sync state, and collection time.
Enabling Apache is different from starting a repository sync.

## Notes

Several small icons resemble one another. Their location and tooltip identify
the kind of notes they open; a document in an Actions row can instead be a log
or graphical console.

| Control | Scope |
| --- | --- |
| White document near a pane heading — Host notes, Windows notes, etc. | Private operator requests/reference for that pane. |
| Orange document in local Host — Personal notes | Personal notes private to the designated operator. |
| Leaf beside a container ID — Shared notes for that ID | Customer/ID notes, separate from the operator work list. |
| Terminal title-bar notes button, where present | Notes associated with the terminal pane. |

**Save** writes the edited text. **Reload** retrieves the server version and asks
before discarding unsaved edits. **Close** dismisses the dialog and asks before
discarding unsaved text. Saving checks the revision so a concurrent edit is not
silently overwritten. If a conflict occurs, keep a private copy of your pending
text before reloading, then reconcile and save again.

On this installation, the authorized hourly maintenance workflow uses personal
and dashboard pane notes as requests. Saving a note does not itself execute a
change. Shared customer notes and the documentation's historical operator notes
are separate from that work list. Notes remain host-local and are excluded from
peer forwarding and public documentation/release content.

## Appearance and footer controls

| Control / tooltip | Effect |
| --- | --- |
| Leaf — Dark theme; switch to light / reverse | Switches the dashboard/login appearance. Documentation retains its permanent light theme. |
| Leaf — Preview EL and utility panes / Preview all panes | Shows additional eligible panes and Sample rows; the tooltip changes to **Use automatic pane visibility** while enabled. Select again to restore automatic visibility. |
| Leaf — Hide background map / Show background map | Hides or shows the geographic background without changing networking or saved locations. |
| Contact address | Opens the mail application. |
| mykos project/version link | Opens the project repository. |
| Logout arrow beside the username | Ends the browser login; it does not stop containers. Closing an active base-editing session can still trigger its reset behavior. |

Appearance and preview preferences are remembered in the browser when local
storage is available. Hover to distinguish the similar leaf icons.

## Common workflows

### Inspect an instance

1. Use the header's OS link to reach its pane.
2. Select the ID row to inspect details; select IP for topology or Memory for processes.
3. Open Console Log for native logs, or HTTP graphical desktop for an interactive desktop.
4. Use the terminal only when you intend to issue commands to that instance.

### Start a new instance or restart an existing one

1. For a new instance, enter an unused eight-digit ID and select a prepared image.
2. Select the pane's blue **+ (Start)**; read any backup-mount prompt, then confirm.
3. Wait for the queued/running operation to finish and inspect the resulting row.
4. For an existing stopped persistent instance, use its green **Start container** triangle instead.

### Save and inspect a point in time

1. On a running persistent instance, select the camera and confirm.
2. Wait for snapshot completion; find the ID in Snapshots and select its capture time.
3. Use the green monitor for a separate preview, then **Close preview** when finished.
4. Use Restore only when you intend to replace the original instance's current state.

### Operate another MYKOS host

1. Locate its compact Host pane and verify the hostname/IP.
2. Use its Host controls for host diagnostics or terminal access.
3. Select its small flower to open the full node for that host's guests and snapshots.
4. Recheck the target name before lifecycle or destructive actions.

## Messages and troubleshooting

| Symptom | What to check or do |
| --- | --- |
| A pane or button is missing | Check role, available images, automatic pane visibility, and whether the pane is a peer view. Sample preview does not grant privileges. |
| A button is gray | Check running/stopped state, an operation in progress, persistence support, image reset/removal state, or capacity. Read its tooltip. |
| “Image storage is busy” / last successful inventory | The UI retains a previous inventory during slow storage reads. Wait for fresh data before assuming an image disappeared. |
| Unknown, stale, or unavailable monitoring | Inspect collection time and connection errors. Avoid interpreting an old value as current state. |
| Container limit reached | There are 50 running customer instances/previews. Close an unneeded preview or deliberately stop an instance before starting another. |
| A console/preview does not open | Allow popups for the MYKOS origin, check admin/terminal permissions and guest state, and retry only after checking the original operation's status. |
| Request returned an error while starting/snapshotting | The action may already be queued. Inspect the row/job status before submitting it again. A failed status read is not proof the action failed. |
| Peer Offline / Status unavailable | Verify the peer host, VPN, HTTPS reachability, and certificate. Do not redirect commands to the local host as a substitute. |
| Base reset/removal failed | Read the image-row error and dependencies. Resolve it before retrying; do not delete dependent customer instances as a diagnostic step. |
| You need to change a browser password or API key | Use the host identity tools described in [Identity management](IDENTITY.md); backup key buttons affect a separate identity system. |

For deeper procedures, use [Images](IMAGE_BUILDS.md), [Desktops and snapshots](DESKTOPS.md),
[Networking](NETWORKING.md), [Peers](PEERS.md), and [Installation/recovery](INSTALL_SERVICES.md).

Pane status summaries appear beside their pane labels after a middle dot, including
live counts, refresh times and errors. They remain gray and update in place; the
ID/start controls and table begin below the heading without a separate status row.


## Receiver appearance

**Receiver** is a personal theme available only after signing in as `mgreen`. Its footer leaf cycles Dark → Light → Receiver. Other logins and the signed-out login screen offer Dark and Light. A saved Receiver preference is restored only after the current-user API confirms `mgreen`; logout/session expiry removes it immediately. Preferences are remembered separately for each signed-in username. Receiver uses a brushed aluminum face, compact blue-lit black VU meters, round metal action buttons and a radio-style recall band with a one-minute tuning knob. The larger flower has a transparent background.

The four VU meters show actual CPU, memory, System and Data percentages from the existing host collector, following the same selected history and freshness rules as the graph. Missing or stale readings show a dim face and a dash; needles stop at 100% while the value still reports overload. Dark/Light retain the detailed history graph, metric controls and all actions. Compact peers inherit the appearance without adding meter height. This is an appearance option, not an audio interface or a change to host resource policy.


### Receiver control bank

Illumination advances ten percentage points per click and wraps from 100% to 0%. Source advances through available OS panes, retaining visibility and permissions. Both controls support Enter/Space. Source is centered beside the time band, with Illumination directly below beside the VU meters. The overlaid container/VPN count meter occupies the brushed-aluminum source-light bank above; VPN bars remain in front. Counts follow selected history and scale to 50 containers and 15 VPNs. Missing or stale readings show a dash.

### Receiver instruments and display power

The upper-left canister button switches instrument illumination on or off: its lamp is green when on and red when off. It affects only the appearance of source lights, count bars, uptime, time graduations and meters. It never starts or stops a host, container, VPN or collector. The hostname and italic environment plaque are at the upper right. Yellow seven-segment uptime sits behind the time graduations; small red bulbs inside VU faces illuminate only for high readings.

Host actions form a row at the lower left and note buttons occupy the lower right. Targets, permissions and confirmations remain unchanged. Dark/Light restore the original control and note-button locations. Light-theme environment labels are black. The full bottom Host status row and hardware line remain hidden only in Receiver.

### Receiver radio controls

The time band is above the meters, with its slider and controls below. At Live it scrolls through the trailing five-minute window; recall browses back up to one hour while the server retains 65 minutes. The large recall knob sits at the upper right below the hostname plaque, with its label below and right. Live is below and left of the knob and reads black when current. Active/focused metal controls have a blue ring. Dragging and keyboard adjustment remain available; the recall knob steps one minute earlier and wraps to Live.

### Receiver component stack

The wood cabinet surrounds a brushed-aluminum face and black source-light bank. Component feet include a thin black bottom ring and an additional metal band. OS and utility panes share the metal face and raised-button treatment. Receiver remains a personal appearance option for mgreen; other users retain Dark/Light.

### Terminal pane width

Interactive terminals fit their text columns to the available pane or window width. Resizing updates the PTY dimensions without writing commands into the shell. Close/replacement disconnects the resize observer. Legacy API/raw terminal clients remain supported.


## PXE desktop provisioning

Full administrators can arm and revoke Windows 11 PXE jobs from the Windows pane. Physical rows show provisioning status only. See [PXE provisioning](PXE.md) for configuration, media imports and explicit erase confirmation.

The environment label uses upright text in Dark and Light themes. Receiver retains its italic label.

## Refresh and startup responsiveness

The session check does not wait for optional external map or terminal libraries.
A failed session request returns a retry message instead of leaving a permanent
Checking session screen. New readings and interactions update the graph immediately;
unchanged readings redraw once per second. Changing readings retain smooth easing.
Compact peer panes collect Host metrics and history without polling hidden guest,
image, repository, backup or PXE sections. Open the full node for those sections.
