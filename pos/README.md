---
pos
---
RHEL 7 container image builds
-----------------------------
`ks/rh7.ks` uses an explicit package list with `--nocore` because the site's three
RHEL 7 mirrors do not publish package-group metadata. The explicit package
names were checked against those mirrors on 23 October 2026. Dependencies and a
complete image build still need validation in a matching RHEL 7 build environment.
The existing Optional and Extras URL aliases are retained. The RHEL 7 OpenSCAP
PCI-DSS profile is retained using its full profile ID; syntax/package checks do
not certify compliance or validate remediation inside a container.

The post-install script uses `/etc/yum/vars`, tolerates an absent subscription
manager plugin, and leaves account provisioning explicit. It stops on failures
and records output in `/anaconda-post.log`. Downloaded `updateos` content must be
nonempty and pass `bash -n` before it replaces the installed script. The subsequent
Podman build checks its download too. Check syntax on the builder with:

```sh
ksvalidator -v RHEL7 ks/rh7.ks
```

RHEL 10 container image builds
-----------------------------
`ks/rh10.ks` targets RHEL 10 Kickstart syntax and a container root filesystem.
Use RHEL 10 Anaconda/Lorax tooling for this build. The `create-image` function uses
`livemedia-creator --no-virt`, which runs the builder's installer rather than a
RHEL 10 installer booted inside a VM. An existing RHEL 8 builder does not become
a RHEL 10 builder by changing only the repository URLs.
The image workflow now checks the RHEL major version and validates the rh10
kickstart before starting this build. If image creation fails, it stops before
moving the output or building/starting a Podman template.

Validate on the build machine before starting the image build:

```sh
cat /etc/redhat-release
rpm -q lorax anaconda-core pykickstart
ksvalidator -v RHEL10 ks/rh10.ks
```

If `RHEL10` is not a supported validator version, use an appropriate RHEL 10
build environment. The root account remains locked; provision access explicitly.
The old RHEL 8 OpenSCAP installer profile is not carried into this image. Apply
any required RHEL 10 profile after building; this file does not certify compliance.
The post-install script stops on errors and logs to `/root/mykos-image-post.log`
inside the target image. The image creator log is
`/tmp/build-rh10-container.error.log`. Successful syntax validation does not check
the site's repository metadata, available packages, or the resulting image boot.

~ A container orchestration utility to improve the process of providing new off-site instances to customers.
Leveraging podman's use of overlay and cgroups to manage rootless containers (chroot envs) that are whole instance containers.
This creates a very fast stage time from vanilla to running instance specific to a customer.
Whole instance rootless containers are also very secure. Existing hardening methods can be freely executed on a per-container basis. each container has root access. each container is locked down to a single, non-admin user on the host system.
VPN connections use crypto that use kernel drivers. Leaving the kernel installed for each container, keeps that encryption segregated to the chroot envs usersspace.
Built in VPN, rootless container production, .....???


RTI
---
~ RTI system running exclusively in the cloud on Redhat.

Whole system RH container images are built with the POS software and a site-to-site vpn connection created to the stores router.
All network traffic is routed from the container through the customers router on site.
Printing and samba shares work the same.
VPN connections from other remote store locations can work the same way as well so long as the main store router can handle the traffic for all stores.
Support processes and tools should not be different than a standard on-premise.
Staging times in the secs. (With or without data from localhost.)
Each POS system is independant from the other. (If one becomes corrupt, the others dont suffer.)

10/02/2026  3:02 PM 
┏━━━━━━━━
┃ @ Mike's Store
┃ )~ pos      v3.2
┃━
┃  prod.mykol.com
┃ Space    : 474G
┃
┃ ID : 00000000
┃ Type : rti
┃ Stat : running
┃
┃  192.168.222.76 
┃ <=> 192.168.10.0 
┃
┃-Containers
┃ 1. Status
┃ 2. Start/Stop
┃ 3. Connect
┃
┃-VPNs
┃ 4. Status
┃ 5. Add
┃ 6. Remove
┃
┃-System
┃ s. ID
┃ m. Images
┃ i. Install
┃ p. Purge
┃ r. Readme
┃ x. Exit
┗━
Select: 

-> Startup time (staged with data and vpn): 1min 13secs.


Features
--------
  o complete rhel system rootless container images.
  o manages ikev2/ESP site-to-site vpns from the running container to a cisco/meraki endpoint.
  o can be ran on a physical server, cloud server, or both.
  o handles multiple instances on one host.
  o Registers instances with Redhat Insights for malware scanning, compliance reporting, CVE information, etc.
  o Administration access to host is public key SSH. Must use an account in the wheel group to install.
  o does not use epel or any other "extra" repos.
  - 

  ? Primary/secondary: make 2 of these for cloud backup servers. the one labelled primary is the rti app server. 2ndary is cloud backup server.


TODO
----
 -> VPNs
    * Prompt to ask if want to upload to meraki. (startup is faster if no)
    * Auto upload config to meraki org.
    * Keep VPN data even if container stopped. So it will auto start if started with same shopcode.
    - Narrow network scheme comming from so meraki autovpn works.
    + multi-vpns from one pos instance.
    ? route through static ip instead of dhcp one?
    - auto upload to meraki switched to append? (depends on how site-to-site vpns are handled now)
    - prompt for api key instead of store.
    -

 -> Images
    - rh9.
    - nopos trimming.
    - move initial image creation to outer menu. (root needed)
      - shopcode, startup/shutdown, vpns can be used by any user.
      - test running rootless containers.
      -

 -> Menu
    * Clear shopcode and postype after stopping container.
    * Verify existing VPNs come up after a restart. (stop and start with same shopcode.)
    - Add conn name to vpn status line.
    -

 -> Host-side
    - public key auth for host account.
    * remote repo setup.
    - performance test.
    - status page.
    -

 -> Container-side
    - openscap padss4 scripts.
    - add firewall setup.
    - rule to block container network after startup.
    + insights registration and scanning.
    - default passwords.
    - configure static ip address.
    * rti start up post data restore.
    ~ services startup post vanilla staging.
    - issue: looses ip address in a random amount of time. can happen to an individual container and not others.
    -

 -> Documentation/Reporting
    - billing.
    - customer lists.
    - health check.
    - flow chart(s).
    - admin guide.
    -



---
mgreen@teleflora.com

## Purge all container state

The POS menu's **Purge** action now performs a full reset rather than just
cleaning unused base-image tags. It previews the affected host and requires
`PURGE hostname` to be typed exactly. It deletes all rootful Podman resources,
VM disks, memory captures, snapshots/recovery disks, per-ID VPN files, build
artifacts, scheduled command jobs, inventory and notes. This cannot be undone.
MYKOS users and API keys, rsync backups/accounts, synced repositories, installed
application/configuration/docs and installer recovery packages are retained.

Run `sudo /usr/local/sbin/mykos-prune --dry-run` to preview without stopping or
deleting anything. Run `sudo /usr/local/sbin/mykos-prune purge` to confirm and
execute; inspect `sudo /usr/local/sbin/mykos-prune status` if interrupted.
