%{!?mykos_target_el:%global mykos_target_el 8}
%if 0%{mykos_target_el} == 9
%global mykos_python /usr/bin/python3
%global mykos_python_packages python3 python3-pip
%else
%if 0%{mykos_target_el} != 8
%{error:MYKOS RPM supports mykos_target_el 8 or 9}
%endif
%global mykos_python /usr/bin/python3.9
%global mykos_python_packages python39 python39-pip
%endif
%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}
%global __brp_mangle_shebangs %{nil}
Name: mykos
Version: 1.5.20261024
Release:        138%{?dist}
Summary: MYKOS infrastructure dashboard and operator toolkit
License: LicenseRef-Proprietary AND MIT
URL: https://mykol.com/mykos/
Source0: %{name}-%{version}.tar.gz
BuildArch: noarch
BuildRequires: python3 >= 3.9
AutoReqProv: no
Requires: system-release(releasever) = %{mykos_target_el}
Requires: %{mykos_python_packages} podman httpd mod_ssl
Requires: openssh-server openssh-clients rsync iproute net-tools netavark containernetworking-plugins criu libreswan
Requires: curl wget jq util-linux tar gzip cronie chrony firewalld
Requires: policycoreutils-python-utils ksh unzip expect nmap time ipset iptables git pykickstart
Requires(pre): shadow-utils
Requires(post): systemd %{mykos_python_packages}
Requires(preun): systemd
Requires(postun): systemd

%description
MYKOS web/API, text administration menus, collectors, and Windows, macOS,
Ubuntu and RHEL lifecycle tools. Run mykos-setup after installing to configure
the public origin, Python environment and optional trusted desktop network.
Guest storage and identities are initialized separately and persist on removal.

%prep
%setup -q

%build
# Host-specific configuration happens in mykos-setup.

%install
mkdir -p %{buildroot}/usr/share/mykos %{buildroot}/usr/sbin
cp -a . %{buildroot}/usr/share/mykos/
/usr/bin/python3 packaging/rpm/install_payload.py %{buildroot} %{mykos_python}
cat > %{buildroot}/usr/sbin/mykos-setup <<'SETUP'
#!/bin/bash
set -e
exec %{mykos_python} /usr/share/mykos/bin/mykos-install install --python %{mykos_python} --python-deps "$@"
SETUP
chmod 0755 %{buildroot}/usr/sbin/mykos-setup %{buildroot}/usr/share/mykos/MENU

%pre
getent group mykosweb >/dev/null || groupadd --system mykosweb
getent passwd mykosweb >/dev/null || useradd --system --gid mykosweb --home-dir /opt/mykos-web --shell /sbin/nologin mykosweb

%post
rm -f /usr/local/bin/mykos
%{mykos_python} /usr/local/sbin/mykos-kvm-check || :
systemctl daemon-reload >/dev/null 2>&1 || :
%{mykos_python} /usr/share/mykos/bin/mykos-console-proxy --apply || echo 'MYKOS console proxy setup needs attention; run mykos-setup.'
echo 'MYKOS installed. Fresh installations automatically configure HTTPS and MYKOS after the RPM transaction.'

%posttrans
if [ -d /opt/mykos-web/data ]; then
  systemctl enable mykos-host-history.service >/dev/null 2>&1 || :
  systemctl restart --no-block mykos-host-history.service || :
fi
if [ ! -f /var/lib/mykos-installer/installed.json ]; then
  systemctl enable --now --no-block mykos-firstboot.service || echo 'Automatic MYKOS setup needs attention: systemctl status mykos-firstboot.service'
  echo 'MYKOS initial setup queued. Follow progress: journalctl -fu mykos-firstboot.service'
fi

%preun
if [ "$1" -eq 0 ]; then
  systemctl disable --now mykos-firstboot.service >/dev/null 2>&1 || :
  %{mykos_python} /usr/share/mykos/bin/mykos-bootstrap --uninstall || echo "MYKOS bootstrap cleanup needs attention; see /var/lib/mykos-installer/bootstrap.json"
  %{mykos_python} /usr/local/sbin/mykos-snapshot unhook || :
  systemctl stop mykos-host-history.service mykos-web.service mykos-container-admin.socket mykos-terminal.socket mykos-collector.timer mykos-details.timer mykos-extended.timer mykos-command-collector.timer >/dev/null 2>&1 || :
  systemctl disable mykos-host-history.service mykos-web.service mykos-container-admin.socket mykos-terminal.socket mykos-collector.timer mykos-details.timer mykos-extended.timer mykos-command-collector.timer >/dev/null 2>&1 || :
fi

%postun
systemctl daemon-reload >/dev/null 2>&1 || :

%files -f runtime.files
%defattr(-,root,root,-)
%attr(0755,root,root) /usr/sbin/mykos-setup
%dir %attr(0750,root,mykosweb) /opt/mykos-web

%changelog
* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-79
- Retain 30 minutes of shared host metrics and expose authenticated history APIs
- Browse five-minute graph windows without losing data on page refresh

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-78
- Link the green footer project name to its GitHub repository
- Use a circular refresh icon for repository synchronization

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-77
- Support macOS Recovery copy validation when cmp is unavailable
- Automatically configure Apache/Certbot HTTPS and MYKOS after fresh RPM transactions
- Preserve existing deployments and stop first-boot setup on final package removal

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-76
- Replace the host globe with the supplied green apple icon at 23px

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-75
- Enlarge the host globe by 3px and render continent silhouettes

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-74
- Use the four-color Microsoft mark for the SQL Server pane

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-73
- Shorten the SQL Server pane and navigation label
- Add an idempotent HTTPS-first Certbot bootstrap followed by signed RPM installation
- Remove bootstrap-owned Apache, renewal and certificate resources on final RPM removal

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-72
- Fill the footer moon icon solid white and use a blue globe beside the certificate domain
- Derive the host heading from Apache Certbot certificate metadata without guest calls

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-71
- Make the light-theme footer sun bright yellow

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-70
- Remove leading separators from all pane headings

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-69
- Shorten the backup pane and navigation label to rsync

* Mon Oct 26 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-68
- Add one-command unattended Ubuntu, Windows and macOS image builds and API operations
- Commit installed sparse disk/state payloads and leave a generic base running
- Use 2 GiB VM RAM with 12 GiB Windows/Ubuntu paging and retain guest integrations

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-64
- Add Ubuntu VM VPN setup, private-agent status and admin-key API configuration
- Use Podman IPs for Ubuntu and show unconfigured native VPNs correctly
- Align backup/repository Disk and Last Modified columns

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-63
- Restore the inventory collector to a fifteen-second interval

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-62
- Build Windows desktop images at 8 GiB with a 3x installation pagefile
- Restart at 2 GiB and Windows-managed paging before final live capture

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-61
- Give Windows image builders nice -5 CPU priority without changing regular instances

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-60
- Poll startup jobs every second; collect inventory every five seconds and after startup
- Clear successful container creation output automatically; retain errors

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-59
- Align host, all container families and rsync backups on shared fixed columns

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-58
- Preserve full RedHat minor versions using build labels and cached host image metadata
- Align container IDs left, status values centered and action columns right

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-57
- Use private NAT and fixed 192.168.22.0/24 addresses for managed containers
- Support Wi-Fi hosts without a physical LAN interface or DHCP client

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-56
- Replace native pipework networking with mykos-lan host DHCP and eth0
- Add direct Ubuntu root console, Windows PowerShell and macOS shell actions

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-55
- Reconcile desktop and ballooning guides with the final memory policy
- Reject incompatible old image RAM captures before creating instance state

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-54
- Reject Ubuntu live/RAM filesystems and check disk space before swap allocation

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-53
- Verify live storage identity and keep disk recovery copies before RAM resizing
- Disable unattended Windows installation for existing instances

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-52
- Support guarded VM RAM reconfiguration on production Podman 4.x
- Remove consumed one-shot RAM restore hooks on subsequent container boots

* Sun Oct 25 2026 Mike Green <mgreen@teleflora.com> - 1.5.20261024-51
- Host-only batched monitoring, optional application adapters and repo workers
- Downward availability lines and guarded cold VM memory allocation

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-50
- Remove RTI/POS application monitoring from collectors, detail views and APIs.
- Prefer readable image references and resolve native snapshot OS from host image tags.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-49
- Resolve NetworkManager and polkit overlay inode watches during native live capture.
- Promote operator kickstarts enabling the headless systemd mount/login profile.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-48
- Remove only unused optional hugetlbfs/configfs mounts before native live snapshots.
- Resolve overlay-backed D-Bus file watches through a scoped CRIU RPC inode hint.
- Add guarded guest balloon inspection/control to the text menu, CLI and admin API.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-47
- Display the server hostname in the Host pane heading.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-46
- Reuse the container VPN workflow for host VPN setup, including PSK entry and the Meraki option.
- Load new host tunnels individually and preserve existing connection files.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-44
- Rename the snapshot Taken column to Most Recent.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-43
- Capture native filesystems through scoped CRIU RPC notifications on RHEL 8/9.
- Remove the incompatible global shell hook and report unsupported CRIU 3.x process state before freezing tasks.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-41
- Route managed browser consoles through authenticated public HTTPS and WebSocket proxies.
- Install Apache console routing during RPM/setup upgrades without changing guest ports or restarting VMs.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-40
- Use uniform gaps between all panes and between pane headings and content.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-39
- Tighten the space before the rsync Backups heading.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-38
- Add the leading grey footer separator before the contact email.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-37
- Simplify the footer to the application version, contact email and pane-toggle leaf.
- Keep host uptime in the host status row.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-36
- Add a blank line above rsync Backups regardless of snapshot pane visibility.
- Remove kernel information from the dashboard footer.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-35
- Match backup, repository and snapshot pane text, actions and controls to the smaller container panes.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-34
- Scale container-pane text, action icons and creation controls to two-thirds size.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-33
- Replace the text-menu purge with a confirmed complete container-state reset.
- Preserve browser/API identities, rsync backups, repositories and installed sources.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-32
- Add a distinct confirmed container-delete action and admin API endpoint.
- Remove private container data while retaining images, snapshots and shared backups.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-31
- Retain explicit VMX configuration for selected Windows guests using nested WSL.
- Preserve the latest operator documentation.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-30
- Package Ubuntu guest DHCP/networking dependency setup and image-build guidance.
- Add a minimal Libreswan/dhclient container and guarded native WSL Containers deployment.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-29
- Use migratable CPU defaults for Ubuntu image builds.
- Enable CPU-profile recovery for existing Ubuntu image builds.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-28
- Align active image installation rows and console icons with container table columns.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-27
- Pause and flush outgoing VMs for snapshot replacement without a full OS shutdown wait.
- Show snapshot date/time choices without the capture-mode suffix.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-26
- Isolate persistent Podman monitors from short-lived worker service cleanup.
- Recover snapshot restores from lost monitors only after verifying guest stop.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-25
- Brighten the footer leaf while show-all pane preview is enabled.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-24
- Refresh real snapshot actions when admin permissions arrive or change.
- Preserve disabled example snapshots in the show-all preview.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-23
- Show active desktop image installations and console links before capture.
- Expose installation builds alongside startable images in the API.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-22
- Include dhcp-client and its dependencies in staged RHEL 9 images.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-21
- Add the Host pane heading with a pipe and Red Hat icon.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-20
- Label container/account identifiers as ID in the web UI, prompts and text menus.
- Retain existing API field names and stored identifiers for compatibility.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-19
- Restore vendor colors for Red Hat, Ubuntu, Windows and Apple pane icons.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-18
- Display environment titles with full-width aesthetic glyphs on dashboard and login.
- Preserve plain stored values, browser titles, accessible names and API data.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-17
- Install one executable /usr/bin/mykos launcher (also /bin/mykos on RHEL).
- Prefer the RPM menu at /usr/share/mykos and remove the legacy local-bin launcher.
- Modernize timezone/NTP syntax and remove obsolete Anaconda kickstart sections.
- Adapt staging copies for older RHEL 8 image-building tools.
- Preserve operator-edited kickstarts across RPM upgrades.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-16
- Open host/Linux root terminals in popup dialogs with session cleanup on close.
- Add admin-only scheduled host/Linux shell and Windows guest PowerShell jobs.

* Sun Oct 25 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-15
- Rename the backup pane, navigation and notes labels to rsync Backups.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-14
- Display full OS names and snapshot capture date/time; add explicit snapshot deletion to UI/API.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-12
- Include the latest operator-authored KEY_ISSUES.md unchanged.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-11
- Persistent lifecycle actions, live container snapshots and per-container restore UI/API.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-10
- Keep only the current startup checkpoint; discard old and consumed captures.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-9
- Remove completed template builders and unused upstream Dockurr image tags.
- Preserve prepared-template staging without builder containers.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-8
- Use the macOS spelling consistently in dashboard labels.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-7
- Check usable KVM support during RPM installation and upgrades.
- Stop and commit captured VM templates under readable local image tags.
- Keep stopped prepared templates available for staging; update dashboard labels/IPs.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-6
- Capture and restore running Windows/macOS guests with RAM and device state.
- Add one-shot checkpoint loader and migratable Windows CPU configuration.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-5
- Support macOS version subdirectories during safe live template capture.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-4
- Live-capture desktop templates without shutdown; resume safely on failure.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-3
- Include KEY_ISSUES.md in installed documentation and the web Docs index.

* Sat Oct 24 2026 MYKOS Maintainers <mgreen@mykol.com> - 1.5.20261024-2
- Target RHEL 8 with Python 3.9; preserve existing pipework networking.
