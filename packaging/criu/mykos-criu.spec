%global debug_package %{nil}
%global __brp_python_bytecompile %{nil}
%global commit 9539417f3e3cfa4eb84c319cd71f4d52f1f08645
Name: mykos-criu
Version: 4.2.1
Release: 3%{?dist}
Summary: Versioned native checkpoint engine for MYKOS
License: GPLv2
URL: https://criu.org/
Source0: criu-%{version}.tar.gz
Patch0: 4.2.1-subtree-control.patch
Patch1: overlay-fsnotify.patch
ExclusiveArch: x86_64
BuildRequires: gcc make python3 pkgconf-pkg-config protobuf-devel protobuf-compiler
BuildRequires: protobuf-c-devel protobuf-c-compiler libnet-devel libnl3-devel libcap-devel
BuildRequires: libaio-devel libselinux-devel nftables-devel libbpf-devel gnutls-devel libuuid-devel patch

%description
CRIU built from the pinned upstream 4.2.1 commit with the controller readback
whitespace correction verified on MYKOS QA. Installs under /opt/mykos-criu
without replacing the distribution's CRIU executable.

%prep
%setup -q -n criu-%{commit}
%patch0 -p1
%patch1 -p1

%build
make %{?_smp_mflags} criu/criu
./criu/criu --version | head -1 | grep -Fx 'Version: 4.2.1'

%install
mkdir -p %{buildroot}/opt/mykos-criu/%{version}/sbin
install -m0755 criu/criu %{buildroot}/opt/mykos-criu/%{version}/sbin/criu
printf '%s\n' 'Upstream commit: %{commit}' 'Patch: trim cgroup.subtree_control readback whitespace before prefixing controller names' > %{buildroot}/opt/mykos-criu/%{version}/SOURCE.txt
chmod 0644 %{buildroot}/opt/mykos-criu/%{version}/SOURCE.txt

%files
%license COPYING
%dir %attr(0755,root,root) /opt/mykos-criu
%dir %attr(0755,root,root) /opt/mykos-criu/%{version}
%dir %attr(0755,root,root) /opt/mykos-criu/%{version}/sbin
%attr(0755,root,root) /opt/mykos-criu/%{version}/sbin/criu
%attr(0644,root,root) /opt/mykos-criu/%{version}/SOURCE.txt

%changelog
* Fri Oct 30 2026 MYKOS Maintainers <mgreen@mykol.com> - 4.2.1-3
- Retain portable paths for overlay watches even when handle opening succeeds at capture.

* Fri Oct 30 2026 MYKOS Maintainers <mgreen@mykol.com> - 4.2.1-2
- Backport the distribution overlay/inotify directory-walk fix and regression tests.

* Fri Oct 30 2026 MYKOS Maintainers <mgreen@mykol.com> - 4.2.1-1
- Package the QA-verified checkpoint engine separately for each EL host major.
- Preserve the vendor executable and validate root-owned versioned paths.
