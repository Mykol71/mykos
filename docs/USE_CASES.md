# MYKOS example use cases

[Documentation index](README.md) · [Architecture](ARCHITECTURE.md) · [Developer’s guide](DEVELOPERS_GUIDE.md)

**Practical examples of what you could build with MYKOS, starting with older applications on newer hardware.**

These are illustrative workflows, not reports of completed customer migrations.
MYKOS prepares and manages operating-system environments; you install, configure,
and validate the application inside them. It does not automatically convert an
old application, translate its code, or make every historical OS compatible.

## 1. Keep an older application running on newer hardware

### The situation

A business still depends on an older inventory, accounting, scheduling, or
order-processing application. The application works, but the physical server is
wearing out. Replacing that server is difficult because the application expects
particular libraries, directory layouts, accounts, or OS behavior.

For example, imagine a distributor with an older Linux order-entry program. Its
application files live under `/usr2` and `/usr/java`, customer data is under `/d`,
and staff depend on established accounts and numeric file ownership. The goal is
to move useful service to a newer Linux host without first rewriting the program.

### What you could build

An application environment with the compatible OS userland, application runtime,
configuration, accounts, and data that the program needs. MYKOS gives the running
environment its own identity and network address and provides lifecycle controls
and an administrative console.

Here, “application container” means a managed environment containing the
application and its dependencies. It need not be a tiny, single-process container.
For suitable Linux workloads, MYKOS uses native Linux containers. Windows and
other supported VM-backed workloads run a guest OS through QEMU/KVM inside a
Podman wrapper.

| Application requirement | Approach to evaluate |
| --- | --- |
| Older Linux libraries and userspace, compatible with the host kernel | A supported native Linux image with the application’s required userspace |
| A separate guest kernel or a supported Windows environment | A supported VM-backed environment with its own installed OS |
| A hardware dongle, proprietary board, obsolete device driver, or CPU-specific behavior | Prove the device and platform requirements separately before choosing this approach |

Native containers share the host kernel. They cannot supply an arbitrary old
kernel simply because an old filesystem is available. A VM provides a separate
kernel, but its OS, drivers, CPU requirements, and devices still need to work on
the selected host. An existing physical system is not an automatic import: plan
application installation or restoration using the supported MYKOS workflow.

### An example workflow

1. **Record what the application actually needs.** Identify its OS, libraries,
   Java or other runtime, database, services, accounts, permissions, printers,
   external systems, and licensing requirements. Save a known-good business
   transaction or report for comparison.
2. **Prepare a compatible image.** Use the appropriate supported OS workflow,
   install the required application prerequisites, and verify that they start.
   Capture useful base-image changes before ending the base console session.
3. **Prepare a separate working copy of the customer backup.** For supported
   native Linux restores, MYKOS can compose the environment with selected backup
   trees and reconcile accounts and numeric ownership. Those mounted trees are
   writable: use a working copy when the original backup must remain unchanged.
4. **Create an ID-specific instance.** Give the rehearsal its own identity and
   network plan. Install or restore the application and configure its services.
   Keep test traffic away from live ordering, email, payment, or batch endpoints
   until those connections are deliberately ready.
5. **Test the business workflow.** Check staff login, application startup, data
   access, an actual representative transaction, reports, printing, integrations,
   and service behavior after an explicit stop/start. Compare the result with
   the known-good system.
6. **Rehearse the move.** Measure the data synchronization and outage window,
   decide how to handle writes during cutover, and test the rollback procedure.
   Switch users and dependencies only after the application owner accepts the
   result.

### What success looks like

Staff can carry out the required business tasks on the new host, accounts and
ownership behave correctly, and the application recovers through the documented
restart and restore procedures. The old physical server can be retired after the
agreed acceptance and rollback period.

The benefit is separating the application’s environment from a failing physical
machine. This does not itself upgrade the application or remove the maintenance
needs of an old guest OS. Keep its network access appropriate to its role and
maintain protected backups.

**Read next:** [Images](IMAGE_BUILDS.md), [desktop and VM requirements](DESKTOPS.md),
[backup composition](INSTALL_SERVICES.md#complete-customer-backup-mounts), and
[networking](NETWORKING.md).

## 2. Rehearse an application upgrade before changing production

**The situation:** You need to update an application, database, runtime, or
configuration, but want to see the effect on a realistic environment first.

**What you could build:** A separate rehearsal instance using a compatible image
and a working copy of representative application data.

**How it could work:** Establish a baseline, apply the proposed change in the
rehearsal, and run the same business checks before and after. Keep its scheduled
jobs and external connections from making unintended production changes. Record
configuration changes and any data conversion steps. Use intentional snapshots
where supported, together with an application-appropriate backup of the data.

**Acceptance check:** The upgrade works with representative data, restart is
repeatable, and the team understands whether a data conversion can be reversed.
An OS snapshot alone does not establish that an external database or directly
mounted backup tree has been rolled back consistently.

**Read next:** [Snapshots and lifecycle](DESKTOPS.md#persistent-lifecycle-and-container-snapshots)
and [validation](VALIDATION.md).

## 3. Prepare a recovery environment before an outage

**The situation:** Rebuilding the operating system and application prerequisites
from scratch would consume too much of a recovery window.

**What you could build:** A maintained compatible image, an available protected
customer backup, and a tested runbook for bringing the application back into use.

**How it could work:** Prepare the OS and runtime ahead of time. Periodically
rehearse restoration against a separate working copy, verify application data,
and test access to its required services. Record the storage, networking,
credentials, and operator steps needed on the intended recovery host.

**Acceptance check:** Users can perform a real application task within a measured
recovery time, and the age of the recovered data is acceptable. Record backup
transfer and application recovery time as well as OS startup time.

MYKOS guest memory/device captures are tied to compatible disks, CPU settings,
and host assumptions; the documented VM startup captures are intended for the
same host. Do not treat a warm capture as a portable recovery image or assume it
will resume on a different server. Prove a cold-start or rebuild/restore path for
replacement hardware. Automatic failover and uninterrupted service are not
established by preparing an image.

**Read next:** [Installation and recovery](INSTALL_SERVICES.md),
[VM storage and capture requirements](DESKTOPS.md), and [networking](NETWORKING.md).

## 4. Reproduce a customer problem for support

**The situation:** A fault depends on a customer’s application version,
configuration, account permissions, or data and cannot be reproduced in a generic
test install.

**What you could build:** A separate support instance based on a suitable copy of
that customer’s environment, with an explicit network and data-handling plan.

**How it could work:** Reproduce one reported failing action, collect the relevant
application evidence, apply a proposed fix, and repeat the action. Check an
unrelated normal workflow too. Use copies or appropriately reduced data where
possible, and prevent duplicate background jobs from contacting live services.

**Acceptance check:** Another technician can reproduce both the failure and the
fix from the recorded steps. A fix working in the support instance is evidence
for a controlled production change, not an automatic change to the customer’s
running environment.

**Read next:** [Developer troubleshooting](DEVELOPERS_GUIDE.md#debug-by-locating-the-boundary-that-failed)
and [accounts and access](IDENTITY.md).

## 5. Give trainees or testers repeatable application environments

**The situation:** Several people need to learn an application or test a release
without sharing one mutable practice system.

**What you could build:** A prepared application image and separate ID-specific
instances containing synthetic or otherwise suitable practice data.

**How it could work:** Prepare and validate a starting environment, capture the
base changes, and create an instance for each exercise. Give each instance the
right hostname, network identity, accounts, and allowed connections. Document
which reset or recreation procedure returns an exercise to its starting point;
keep intentional snapshots separate from automatic startup state.

**Acceptance check:** Two participants can complete the exercise independently,
and resetting one practice environment does not affect another or a real
customer. Account for the host memory, CPU, and storage required by concurrent
instances. The released access model does not by itself establish a fully scoped
multi-customer self-service portal.

**Read next:** [Image preparation](IMAGE_BUILDS.md), [identity management](IDENTITY.md),
and [instance lifecycle](DESKTOPS.md).

## 6. Operate related server and desktop workloads together

**The situation:** A small office or branch needs a server-side application plus
supported desktop environments used to administer or access it.

**What you could build:** A native Linux application environment and, where
appropriate, supported VM-backed desktops managed through the same MYKOS control
surface. Use an explicit VPN/gateway mode when remote connectivity is needed.

**How it could work:** Establish the application’s server requirements, desktop
requirements, network paths, and concurrent-user capacity. Prepare the required
images, create instances, configure access, and verify the entire user workflow.
Test printers, scanners, and other physical dependencies separately.

**Acceptance check:** Users can reach the application and required services, the
intended access boundaries hold, and loss of a desktop or network path has an
understood recovery procedure. Central hosting does not automatically replace
physical devices, their drivers, or their validation.

**Read next:** [Desktop environments](DESKTOPS.md), [VPN architecture](NETWORKING.md#vpn-architecture-and-operations),
and [deployment efficiency](DEPLOYMENT_EFFICIENCY.md).

## Turn an example into a deployment plan

For any of these cases, write down:

- The application and business task you want to preserve or improve.
- The OS/runtime fit, devices, external services, and required data.
- The prepared image, instance identity, network plan, and data ownership.
- A representative acceptance test and the person who can approve its result.
- The restart, backup, recovery, cutover, and rollback procedures that apply.

A running container is a useful starting point. A working, tested application
with a repeatable operating procedure is the outcome.
