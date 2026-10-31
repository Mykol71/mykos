# MYKOS documentation

These maintained guides include current operational features. Historical design baselines are identified in the guides that retain them.

**Supported MYKOS hosts: RHEL 9 and RHEL 10 (x86_64).** RHEL 8 host support has been dropped. Guest/image support is separate: RHEL 8 guest recipes remain available.

Use these guides to install, operate, integrate, and recover MYKOS. Start with the
guide for your task, then follow its prerequisites and verification steps.

> **Highlighted feature: Snapshot Preview**
> Inspect a saved point in time in a separate, temporary console without rolling
> back the running container. Desktop previews resume captured RAM and disks;
> native Linux previews open the saved filesystem in an isolated shell.
> [How Snapshot Preview works](DESKTOPS.md#snapshot-preview).

See the [screen examples gallery](SCREENSHOTS.md) for the dashboard, snapshot actions and a separate preview console.

## Project design and business value

Read the [two-paragraph business pitch](DEPLOYMENT_EFFICIENCY.md#the-business-case-in-two-paragraphs).

| Guide | Use it for |
| --- | --- |
| [Screen examples](SCREENSHOTS.md) | Dashboard, VPN map and Snapshot Preview screenshots with example data |
| [Example use cases](USE_CASES.md) | Practical workflows, starting with older applications on newer hardware |
| [Security by design](SECURITY_BY_DESIGN.md) | Trust boundaries, identity, privileged operations, data protection, and deployment limits |
| [Key innovations](KEY_INNOVATIONS.md) | Technical leadership overview of the design contributions |
| [Performance by design](ENGINEERING_LESSONS.md) | Engineering milestones, difficult problems, and the whole-instance design rationale |
| [Deployment efficiency and business value](DEPLOYMENT_EFFICIENCY.md) | Business scenarios, customer self-service, cost-saving mechanisms, and an illustrative savings model |

The key innovations, performance by design, and deployment efficiency guides include downloadable PDFs retaining their reviewed release146 baseline. Current task guides describe later operational additions; planned compliance scanning and alerts remain clearly marked as future work.

## Installation and recovery

| Guide | Use it for |
| --- | --- |
| [IP installation and optional domain HTTPS](BOOTSTRAP.md) | IP access, optional HTTPS, and bootstrap retries |
| [Installation updates and recovery](INSTALL_SERVICES.md) | Source installation, updates, verification, backup, and restore |
| [RHEL RPM packages](RPM.md) | Signed EL9 and EL10 host packages and repositories |
| [Identity management](IDENTITY.md) | Browser accounts, permissions, passwords, and API keys |

## Images containers and networking

| Guide | Use it for |
| --- | --- |
| [Unattended image builds](IMAGE_BUILDS.md) | Build and capture reusable OS images and manage base console sessions |
| [Windows macOS and Ubuntu desktops](DESKTOPS.md) | VM requirements, private storage, consoles, and snapshots |
| [Managed networking](NETWORKING.md) | Container interfaces, VPN modes, persistence, and topology |
| [Guest ballooning](BALLOONING.md) | Memory behavior and native snapshot compatibility |

## Integration and reference

| Guide | Use it for |
| --- | --- |
| [Credits](CREDITS.md) | Vendor and project logos, acknowledgments, Dell, Teleflora, Codex, and ChatGPT |
| [References](REFERENCES.md) | Upstream projects, dependencies, compatibility references, development tools, and OS vendor sites |
| [API usage](API.md) | Authentication, endpoints, examples, and errors |
| [OpenAPI specification](openapi.json) | Machine-readable API schema |
| [Developer’s guide](DEVELOPERS_GUIDE.md) | Human handoff, code walkthroughs, safe setup, tests and changing the system |
| [Architecture and deployed paths](ARCHITECTURE.md) | Components, trust boundaries, ownership, and systemd units |
| [Operator script catalog](SCRIPTS.md) | Commands, menus, helper protocols, and legacy limitations |
| [Validation record](VALIDATION.md) | Recorded checks and remaining integration work |
| [Host monitoring review](HOST_SIDE_REVIEW.md) | Observations, findings, and implemented optimizations |
| [Third-party icon credits](THIRD_PARTY_ICONS.txt) | Bundled icon attribution |

## Administrator guide and operator notes

The [Markdown administrator guide](MYKOS_Administrator_Guide.md) is a reference
edition of the guide dated 21 October 2026. Its historical commands and caveats
are retained. Use the maintained task guides above for current source procedures.
The [original Word guide](MYKOS_Administrator_Guide.docx) is preserved.

[Operator notes](KEY_ISSUES.md) retain the author's original text verbatim.
They record context and proposals; they do not establish verified deployment behavior.

## Documentation build

See [the maintained build workflow](DOCUMENTATION_BUILD.md) for dependencies,
rendering, link checks and the production operator-edit guard. Generated HTML
keeps the permanent light theme.

## Diagrams

- [Service boundaries](diagrams/architecture.svg) · [Mermaid source](diagrams/architecture.mmd)
- [Images containers and snapshots](diagrams/image-lifecycle.svg) · [Mermaid source](diagrams/image-lifecycle.mmd)

SVG images display the diagrams without requiring a Mermaid renderer. The editable
Mermaid files describe the same nodes and connections. Relative links, Markdown
tables, and language-tagged command blocks keep the guides portable across GitHub
and Azure Repos.

- [Direct native VPN](diagrams/vpn-direct.svg) · [Mermaid](diagrams/vpn-direct.mmd)
- [Shared native gateway](diagrams/vpn-native-gateway.svg) · [Mermaid](diagrams/vpn-native-gateway.mmd)
- [Host shared gateway](diagrams/vpn-host-shared.svg) · [Mermaid](diagrams/vpn-host-shared.mmd)
- [Host-only VPN](diagrams/vpn-host-only.svg) · [Mermaid](diagrams/vpn-host-only.mmd)
