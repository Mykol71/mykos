* mykos customs/components: 

 + Security/Compliance: Allows for very secure rootless containers to be created that run in a users chrooted jail.
   The management of the environment, image creation, key manipulation, access roles, etc. are handled by an admin at host install time.
   However, once configured and running in a production env, the VMs are ran under an unelevated user account.
   Isolation helps keep issues from happening to the global list of VMs.
   while still allowing root/admin access to the guest.

 + Deployment/Staging: Using podman as an orchestrator of chrooted, running, jails. 
   Allows for keeping a "warm" copy (already booted) as a starting point. 
   Making startup times extremely fast.

 + Image Capture/Snapshot Tool: Created a custom "snapshot" to use instead of docker commit because the commit doesnt capture device and memory states. only disks.
   So, a captured template or commited one would boot from scratch instead of starting from the warm state.
   This was used for all VM types.
 . This is also used when creating the images so they can start from a warm state.

 + Statistical Data: All podman/dockers status info, start/stop, exec commands etc. are sent through PID1 linearly. That's a performance issue.
   A collection process, and API were created, along with refresh timing controls, to address that issue.

 + Fileshares/Printing: Keeping fileshares, printers, etc. on a linux server type from having to be done differently regardless if the server is at the same physical location or not.
   VPN solution was created to allow the guest images or the host instance to connect via ipsec ikev2 in a site-to-site (meraki/cisco) fashion.
   That allows for a machine to be used as a cloud server, serving hundreds of instances to different locations.
   Or, one instance that houses a server and several desktop instances that connect to it.
   Also allows for connections of complete remote offices. Not just a single node.
   So long as 1 side has udp ports 4500 and 500 forwarded to mykos, then a 2nd mykos instance can be used as a VPN endpoint in addition to the meraki/cisco routers.

 + Reporting / Alerting / Billing: Instead of making home-grown custom reports, alerts, etc. an API was added to offer all data points collected routinely by the custom collectors.
   This allows integrating any RMM, Reporting, Support etc. type system you wish.
   The collectors handle the data refresh (every few secs) so the integrating systems don't have to.
   The API used key based auth without cookies.
   It also has an admin and readonly roles for deliniating between using the POST API action commands as well.

 + Running the desktop instances on the same server also eleminates the need for a solution to quickly stage desktops that connect to the server. I.E. a pxe solution.
   Also, it allows for hardening of the host to protect not just the server, but all the desktops as well against whatever security role or requirements.
   Allowing the user able to connect to the system with just a browser. Easing some of the compliance and support issues.
   The desktop instances are also licenseless. Leaving it still up to the owner of the system to license the instances appropriately.
 . Supports http desktop access or rdp and vnc remote desktops for windows and macos.

 + RedHat patches/Repos for Image creation: The system will optionally house the redhat rpm repos used for redhat installations.
   This allows for much faster staging times, as well as allowing for running of different OS major versions on the same host server.
 
 + Packaging/Installations/Patching: mykos is packaged in a signed rpm format and has an installable repo to go along side redhat's for patch management of this application as well.
   This way, the patching process should align with whatever existing process there is for the host for patches.

 + Memory ballooning: Windows does support it and it was implemented on the guest images.
   macOS does not support it and needs a memory ballooning driver.
   RedHat supports out of box.

 + Networking: Using a custom networking script (pipework). It requires dhclient, but opperates otherwise independantly of NetworkManager.
   the dhclient package is to be deprecated, however still available for rh9. So it was added as a dependency.
   This is used to share the hosts nic to the guest as an additional nic on the guest that makes a dhcp request for the same internal network the host is on.

 + WebUI: Created mainly to help with development and performance testing. (Then intension was to be administered primarily via the API).  But can be end user facing if desired.

 + RTI Backup: Legacy rsync backup solution that will be used as a migration tool to import physical systems custom data quickly.

 + Performance graph: To help with diagnosing performance issues along the way.

 + Host and guest networking: in the hopes of making the networking configuration consistant across instance types, wsl install was added to the win11 image creation and a prepared container built that contains libres   wan, iproute2, dhclient, and net-tools for the custom network pipework script. This way, we can similar nic names and behavior across OS types.
   Requirements we also added to ubuntu for the same reasons.
   Nested virtualization was enabled on the win11 guest.

 -

mgreen@mykol.com
