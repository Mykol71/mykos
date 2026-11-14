# Testing README — start here

[Documentation index](README.md) · [Packaging README](PACKAGING_README.md) · [Developer guide](DEVELOPERS_GUIDE.md)

Use this sequence to set up the test environment, run the relevant checks, verify
installation, and remove test resources. Run workstation tests as a normal user
from a **full reviewed source checkout**, not an installed payload or the smaller
installer source archive. Keep existing user environments and workloads intact.

## 1. Choose the scope and record the baseline

Record the exact source commit/tree, changed files, host OS and intended checks.
Before changing or packaging source, compare both production documentation
locations and the live POS source as described in the
[operator-edit guard](DOCUMENTATION_BUILD.md#protect-operator-edits-before-every-change-batch).
Those documents are preservation guards, not hourly task sources.

| Change | Start with | Before calling it validated |
| --- | --- | --- |
| Documentation | Step 5 and viewer tests | Rebuild check, local links/allowlists, staged inclusion and live guide URLs |
| Python/backend | Relevant `tests/test_*.py` | Full regressions for a release, dev endpoint/feature checks |
| Dashboard JavaScript | Relevant `tests/*.cjs` | All JS checks for a release, browser visual/keyboard checks |
| Installer/RPM | Steps 6–7 | Signed package/dependency/setup checks on matching hosts |
| Podman/CRIU/KVM/networking | Mocked regressions first | Scoped live checks on an allocated compatible Linux host |

For live tests, record containers and their IDs/start times, images/tags,
snapshots, mounts, active builds, peer state and relevant host settings before
testing. Give every test-created resource an explicit owner/run identifier and
register its exact name/path for cleanup immediately. Preserve pre-existing
resources. Arrange `finally`/exit/signal cleanup before creating live resources;
if interrupted unexpectedly, reconcile the recorded inventory before rerunning.

## 2. Create an isolated workstation environment

Use Python 3.12 for workstation regressions and Node.js 20+ for UI/docs checks.
RHEL runtime validation is separate: EL9 uses Python 3.11; EL10 uses its packaged
`python3`. A Python build providing `hashlib.scrypt` is required for identity tests.

From the full checkout, run the following in a **Bash session** and keep the same
session for steps 3–6. The trap removes only the environment and scratch created
here when that session exits, including failures and interrupt/termination.
Activate `set -e` so a failed command stops that session; open a dedicated shell
for these instructions if you need your parent shell to remain open.

```bash
set -eu
umask 077
test_work=$(mktemp -d /tmp/mykos-test.XXXXXX)
test_work=$(cd "$test_work" && pwd -P)
trap 'rm -rf -- "$test_work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/services/helpers:$PWD/services/web${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="$test_work"
export PIP_CACHE_DIR="$test_work/pip-cache"
export npm_config_cache="$test_work/npm-cache"
python3.12 -m venv "$test_work/venv"
. "$test_work/venv/bin/activate"
python -m pip install -r services/requirements.txt pytest httpx
python -c 'import hashlib; assert hasattr(hashlib, "scrypt")'
python --version
node --version
python -m pip freeze > "$test_work/dependencies.txt"
```

Expected: dependencies install, the scrypt assertion succeeds, and the versions
are recorded. Runtime direct dependencies are pinned; `pytest`/`httpx` and all
transitive test dependencies are not a fully locked test environment. Preserve a
compact dependency record with a failure report before exiting. Do not replace
pinned runtime versions just to hide a failing assertion.

## 3. Run Python regressions

Start with files relevant to the change. For example:

```bash
python -m pytest -q -p no:cacheprovider tests/test_supporting_docs.py
python -m pytest -q -p no:cacheprovider tests/test_installer.py
```

For a planned release, run the complete discovered Python suite:

```bash
python -m pytest -q -p no:cacheprovider --basetemp="$test_work/pytest" tests
```

Expected: no unexplained failures. Record the actual pass/skip counts and reasons;
do not copy a historical count into a new result. Tests use temporary fixtures
and mocked system boundaries; passing them does not prove live host behavior.
The suite contains Linux-specific behavior, so reproduce a platform-only failure
on the compatible dev host before changing code. Never remove a failing test to
make the release appear green.

## 4. Run JavaScript checks

From the same checkout and session:

```bash
for test_file in tests/*.cjs; do
  node "$test_file" || exit 1
done
```

Expected: every script exits successfully. These are plain Node scripts; use
`node FILE.cjs`, not an assumed npm or `node --test` project runner. For changed
controls, also inspect real browser behavior on dev: focus/keyboard order,
permissions, popup/close behavior and refresh/error recovery. Test fixtures do
not establish visual correctness or a working remote terminal/guest console.
Close temporary tabs, sessions and PTYs, and restore browser preferences after
verification.

## 5. Build and verify documentation

Use an expendable copy so `npm ci` never deletes a pre-existing tooling install.
The example copies the docs tool's exact files into this run's scratch, installs
there, and temporarily links its dependencies into this reviewed checkout. It
refuses to replace an existing `tools/docs/node_modules`; use that existing
installation after checking its lockfile instead, or use a separate scratch
checkout. The cleanup trap removes only the link and scratch created here.

```bash
(
  set -eu
  test ! -e tools/docs/node_modules
  test ! -L tools/docs/node_modules
  docs_work=$(mktemp -d /tmp/mykos-docs.XXXXXX)
  trap 'rm -f tools/docs/node_modules; rm -rf -- "$docs_work"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cp tools/docs/package.json tools/docs/package-lock.json "$docs_work/"
  npm --prefix "$docs_work" ci --ignore-scripts
  ln -s "$docs_work/node_modules" tools/docs/node_modules
  node tools/docs/build.mjs
  python tools/docs/check.py
  node tools/docs/build.mjs --check
  python -m pytest -q -p no:cacheprovider tests/test_supporting_docs.py
)
```

Expected: all pages/links, exact viewer allowlists, diagrams, protected originals
and permanent light theme pass; `--check` finds no generated-file drift. Keep
changed generated HTML/diagrams as intentional source output. It is normal to
commit that output; it is not disposable test scratch. Review the diff. See the
[documentation build guide](DOCUMENTATION_BUILD.md) for source ownership and
public allowlists. Do not edit rendered HTML as the primary source.

## 6. Check staged installation

Stay in the isolated environment from step 2. Stage into its empty scratch root;
never substitute `/` for this `--root`:

```bash
python bin/mykos-install install --root "$test_work/stage" \
  --python /usr/bin/python3.11
python bin/mykos-install verify --root "$test_work/stage"
test -s "$test_work/stage/usr/local/share/mykos/LICENSE.txt"
test -s "$test_work/stage/usr/local/share/mykos/THIRD_PARTY_NOTICES.txt"
test -s "$test_work/stage/usr/local/share/mykos/docs/rendered/PACKAGING_README.html"
test -s "$test_work/stage/usr/local/share/mykos/docs/rendered/TESTING_README.html"
```

Expected: installer verification reports no unreviewed drift and required guides
and license files exist. Alternate-root staging does not activate host services,
install host dependencies, or prove login/guest lifecycle behavior. Use the
[Packaging README](PACKAGING_README.md) to build, inspect and verify the archive
and signed RPMs separately. Check the extracted archive too; passing tests in a
full checkout does not prove that packaging included every required file.

## 7. Validate live behavior on dev, then production

Use an explicitly allocated compatible Linux environment and the intended signed
candidate. Follow [RPM upgrade and verification](RPM.md#upgrade-and-verify) with a
recorded scoped rollback. Recheck docs/POS preservation guards immediately before
each deployment. Confirm dev's installer verification, app readiness,
authenticated behavior and the changed feature before production promotion.

Keep HTTPS verification enabled and use the host's actual trusted certificate
name. A DNS rename does not update its certificate; do not treat a `curl -k`
result as a successful trust check. Check the main Docs page and new guide links
through the actual proxy path, not only loopback. Compare served bytes against
accepted rendered output.

Only exercise live lifecycle operations required by the change, with disposable
IDs/storage recorded before use. Scripts named `*_probe.py` may create real
resources and are not equivalent to mocked pytest files: inspect them first.
Do not build all OS images as a routine app-release test. Preserve active user
builds, containers, snapshots, peers, network configuration and background
repository copies. `MYKOS_BASE` does not relocate every privileged helper's
hard-coded system paths and is not a lifecycle sandbox.

After dev passes, deploy the same candidate to production, check affected
behavior, and compare preserved resource identities/start times and settings
with the baseline. Defer a disruptive web reload if an active base editor would
be disconnected. If production fails, restore the scoped application change,
record the failure and return to dev; stop publication.

Record test limits precisely. Mocked tests and UBI package/app/login checks do
not verify physical NIC routing, bare-metal KVM, CRIU memory restore or a fresh
installation of every guest. EL10 fresh dependency/native-CRIU availability and
live EL10 runtime verification remain separate release qualifications.

## 8. Clean up and record results

Before reporting any test complete, on success, failure or interruption:

1. Remove only this run's test containers and temporary accounts/sessions/PTYs.
   Remove only its test snapshots, disks, volumes and mounts after checking
   references and unmounting temporary mounts.
2. Check image/container/snapshot references before removing test tags/images.
   Use non-forced removal. Never run a global prune or remove shared/in-use
   images to make the inventory look clean.
3. Remove this run's staging roots, build trees, scratch, caches and temporary
   dependency installations. Preserve pre-existing caches/environments. Exit the
   dedicated shell from step 2 to trigger its cleanup; verify its recorded
   `$test_work` path is gone from a parent shell. A forced process kill may
   require explicit recovery cleanup from the resource record.
4. Compare final inventory with the pre-test baseline. Explain legitimate user
   changes; do not delete them to force equality. Retain necessary scoped
   installer rollback records and accepted release artifacts/evidence only.
5. Record source identity, commands, environment, actual results, skips/limits,
   dev/prod endpoint results and cleanup verification. Keep private notes and
   credentials out of public source, release artifacts and unrelated summaries.

A test is complete when its expected assertions and cleanup checks pass. A
release is complete only after signed publication and verification at all
required destinations; see the [release closeout steps](PACKAGING_README.md#7-publish-verify-and-close-the-batch).

### Restore and inventory regression checks

`tests/test_review256.py` covers shared writable mount rejection and independent repository inventory reads. Business provisioning is tested separately in its extension package.

GitHub traffic/report changes: run `python3 -m pytest -q tests/test_rpm_download_stats.py tests/test_github_traffic.py`. Check owner-only HTML and JSON access before loading data, stale/malformed snapshots, provider unique semantics, UTC daily labels, Central refresh timestamps, escaped labels, total ordering and the minute page refresh. Use synthetic aggregates and temporary SQLite files; do not create accounts or read production authentication data for these tests.
