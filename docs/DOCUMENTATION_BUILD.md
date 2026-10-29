# Build and verify the documentation

[Documentation index](README.md)

The public guide list in `tools/docs/guides.json` must match the viewer allowlist.
Only those Markdown files are rendered; extra draft guides are excluded.
The maintained sources are those guides, `tools/docs/diagrams.json` and
`tools/docs/theme.css`. The graph model generates both Mermaid text and SVG
using the same nodes/edges. SVG uses Graphviz for portable static layout; it is
not a screenshot of a Mermaid renderer. Markdown renders with Marked GFM. Keep
relative links for GitHub and Azure Repos; no browser Mermaid plugin is required.

## Local build

Use Node.js 20+ and Python 3.9+. From the source root, install the exact direct
build dependencies listed in `tools/docs/package.json` into that directory:

```bash
npm --prefix tools/docs ci --ignore-scripts
node tools/docs/build.mjs
python3 tools/docs/check.py
node tools/docs/build.mjs --check
```

These dependencies are documentation tooling, not installed server requirements.
The exact direct versions and lock graph are included; update them deliberately
and verify generated output when changing dependencies. The HTML and SVG are bundled so deployed hosts need neither Node nor
npm. `--check` compares every generated file byte-for-byte without writing.
`check.py` validates Markdown targets, rendered local links/anchors, diagram XML,
viewer allowlists and protected-original hashes. Run viewer tests separately:

```bash
python3 -m pytest -q tests/test_supporting_docs.py
```

Never edit `docs/rendered` or generated `.svg`/`.mmd` files as primary sources.
After a source edit, rebuild and include all changed generated files. Add a new
guide or diagram to the explicit `services/web/supporting_docs.py` allowlist;
do not broaden it to arbitrary filesystem paths. The fixed light stylesheet and
documentation CSP are independent of the dashboard/system theme.

## Protect operator edits before every change batch

Before editing, packaging, or promotion, compare hashes and relative paths in
**both** `/home/mgreen/mykos/docs` and `/usr/local/share/mykos/docs` with the
reviewed local baseline. Fetch and privately preserve new files/edits from either
copy, reconcile them, then build again. Recheck immediately before promotion;
abort if the baseline changed. A previously clean comparison does not authorize
overwriting a later operator edit. `KEY_ISSUES.md` and the original Word guide
remain verbatim. The Markdown administrator edition is historical reference;
correct maintained task guides and label historical material clearly.

Use the existing approved packaging/promotion process after review. Keep source
and deployed documentation synchronized, including rendered pages, allowlists
and manifest hashes. Do not rerun the one-time formatting migration. Documentation
verification must not start/stop guests, change VPNs/routes/peers, replace disks,
restore snapshots, alter identities or modify repositories.
