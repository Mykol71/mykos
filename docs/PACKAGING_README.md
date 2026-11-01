# Packaging README — start here

[Documentation index](README.md) · [Testing README](TESTING_README.md) · [Installing signed RPMs](RPM.md)

Follow these steps from reviewed MYKOS source to a verified source archive and
host-specific RPMs. Run workstation steps as a normal user. Build RPMs on an
allocated RHEL build host. A local archive or unsigned RPM is a candidate, not a
published release. Packaging does not require building every guest OS.

## 1. Select and preserve the source

Start from the latest recorded working source, including deployed hotfixes, or a
reviewed Git commit. Copy a frozen release to a new working directory before
editing it. Do not reset a checkout containing unrelated or unpublished work.
Record the baseline commit, intended changes and release number in your release
record. Inspect `packaging/rpm/mykos.spec` for its current Version and Release;
assign the next unused Release only when preparing a signed release. Preserve
published release trees and never replace an existing release's files.

Before editing or packaging, compare relative paths and hashes in both production
locations, `/home/mgreen/mykos/docs` and `/usr/local/share/mykos/docs`, against the
chosen baseline. Record an absent location explicitly. Privately fetch and
reconcile operator edits before continuing. Preserve `KEY_ISSUES.md`, the original
administrator guides and live `/usr/local/share/mykos/pos/pos` fixes. Repeat this
comparison immediately before deployment. See the
[operator-edit guard](DOCUMENTATION_BUILD.md#protect-operator-edits-before-every-change-batch).

Exclude runtime databases, personal/pane notes, accounts, credentials, signing
keys, VM disks, captures, caches and scratch from release source. Do not create a
release by archiving an entire host directory or a mixed checkout.

## 2. Validate the candidate

Follow the [Testing README](TESTING_README.md) first. For a planned release, run
the full Python and JavaScript regressions from the full checkout, regenerate
and check documentation, and retain compact results with interpreter/dependency
versions. Resolve failures before signing or publication.

Confirm new files are in the installer plan: `bin/mykos-install` selects source
payloads; Git membership alone does not ensure inclusion. New public guides must
appear in both `tools/docs/guides.json` and `services/web/supporting_docs.py`.
Rebuild their HTML rather than editing generated files directly.

## 3. Build and inspect a source archive

Prerequisites: Python 3.9+ and the reviewed source tree. These commands target a
Linux build host with `sha256sum`, `tar` and `mktemp`. Run the entire block from
the source root in Bash. Its temporary archive is removed on failure or exit;
set `release_output` to a new, private destination for the accepted candidate.

```bash
(
  set -eu
  umask 077
  release_output="$PWD/release-output"
  mkdir -p "$release_output"
  package_work=$(mktemp -d /tmp/mykos-package.XXXXXX)
  trap 'rm -rf -- "$package_work"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  version=$(awk '/^Version:/ {print $2; exit}' packaging/rpm/mykos.spec)
  archive="mykos-${version}.tar.gz"
  python3 bin/mykos-package --output "$package_work/$archive"
  (cd "$package_work" && sha256sum -c "$archive.sha256")
  mkdir "$package_work/unpacked"
  tar -tzf "$package_work/$archive"
  tar -xzf "$package_work/$archive" -C "$package_work/unpacked"
  (cd "$package_work/unpacked/mykos-$version" && sha256sum -c SHA256SUMS)
  test -s "$package_work/unpacked/mykos-$version/LICENSE.txt"
  test -s "$package_work/unpacked/mykos-$version/THIRD_PARTY_NOTICES.txt"
  test -s "$package_work/unpacked/mykos-$version/docs/rendered/PACKAGING_README.html"
  test -s "$package_work/unpacked/mykos-$version/docs/rendered/TESTING_README.html"
  test ! -e "$release_output/$archive"
  test ! -e "$release_output/$archive.sha256"
  cp "$package_work/$archive" "$package_work/$archive.sha256" "$release_output/"
)
```

Expected: the packager prints its file count and SHA-256; both checksum checks
pass. Review the member list for required files and accidental private material.
The archive basename must stay `mykos-VERSION.tar.gz`: it sets the top directory,
which must match the RPM spec's `%setup`. The packager refuses existing output
files. Preserve accepted archives and their checksum as release artifacts;
remove rejected candidates by their exact paths.

The archive contains managed installer payloads and selected support files. It
is **not the full regression checkout**. Use the full reviewed checkout for the
[Python and JavaScript test suites](TESTING_README.md#3-run-python-regressions).

## 4. Build EL9 and EL10 RPM candidates

Use a disposable/allocated RHEL builder with Python 3.9+, `rpm-build`, and the
spec's build requirements. Install missing builder tools through your normal
approved host setup. Building does not install the resulting application. Keep
the RPM top directory separate from an operator's existing `~/rpmbuild`.

Run this complete Bash block from the same source root. It retains only accepted
binary/source RPMs and removes the private build tree on success or failure:

```bash
(
  set -eu
  umask 077
  release_output="$PWD/release-output"
  version=$(awk '/^Version:/ {print $2; exit}' packaging/rpm/mykos.spec)
  rpm_work=$(mktemp -d /tmp/mykos-rpmbuild.XXXXXX)
  trap 'rm -rf -- "$rpm_work"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  mkdir -p "$rpm_work"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
  cp "$release_output/mykos-$version.tar.gz" "$rpm_work/SOURCES/"
  cp packaging/rpm/mykos.spec "$rpm_work/SPECS/"
  test ! -e "$release_output/rpms"
  for target in 9 10; do
    rpmbuild -ba --define "_topdir $rpm_work" \
      --define "mykos_target_el $target" --define "dist .el$target" \
      "$rpm_work/SPECS/mykos.spec"
  done
  find "$rpm_work/RPMS" "$rpm_work/SRPMS" -type f -name '*.rpm' \
    -exec rpm -qp --queryformat '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n' '{}' \;
  mkdir "$release_output/rpms"
  cp "$rpm_work"/RPMS/noarch/*.rpm "$rpm_work"/SRPMS/*.rpm "$release_output/rpms/"
)
```

Expected: two `.noarch.rpm` application packages and two `.src.rpm` packages,
with matching versions/releases and distinct `.el9`/`.el10` targets. Inspect with
`rpm -qpl FILE.rpm`, `rpm -qp --requires FILE.rpm`, and
`rpm -qp --scripts FILE.rpm`; substitute an actual output path for `FILE.rpm`.
Check license/notice delivery and host-specific Python/dependency requirements.
`noarch` does not make one host-major package suitable for the other major.
Cross-target spec builds do not establish native EL10 runtime compatibility.

## 5. Sign and independently verify

Use the established protected MYKOS signing environment. Signing key location,
passphrase and wrapper configuration remain host-local; never copy them into
source or invent replacement keys. Once that environment is configured, run
`rpmsign --addsign FILE.rpm` for each accepted binary and source RPM, using the
approved wrapper/macros. Sign before generating final published checksums.

Verify the public key fingerprint with `gpg --show-keys --with-fingerprint
RPM-GPG-KEY-MYKOS`; compare it with the current
[trusted fingerprint](RPM.md#install-from-the-matching-repository). Use a fresh,
isolated RPM database for verification so a workstation's unrelated keyring does
not determine the result:

```bash
(
  set -eu
  signature_work=$(mktemp -d /tmp/mykos-signature.XXXXXX)
  trap 'rm -rf -- "$signature_work"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  rpm --dbpath "$signature_work" --initdb
  rpmkeys --dbpath "$signature_work" --import ./RPM-GPG-KEY-MYKOS
  rpmkeys --dbpath "$signature_work" --checksig ./release-output/rpms/*.rpm
  (cd release-output/rpms && sha256sum ./*.rpm > SHA256SUMS && sha256sum -c SHA256SUMS)
)
```

Place the reviewed public key at `./RPM-GPG-KEY-MYKOS` before this block. Expected:
every package reports valid digests and signatures, with no missing/untrusted key.
A checksum alone does not authenticate a release. A mismatch stops publication;
do not bypass it by disabling GPG checks.

## 6. Validate installation on dev, then production

Follow [staged and live testing](TESTING_README.md#6-check-staged-installation).
A staging-root install checks file plans without activating services. A separate
signed-package install on a compatible dev host checks dependency resolution,
setup, app/login behavior and the changed features. Use the
[RPM installation/update steps](RPM.md#upgrade-and-verify), with a recorded
rollback and current operator-edit guards. Protect active builds, containers,
disks, notes, peers, host settings and repository copies.

After dev passes, promote the same verified candidate to production and test the
affected behavior there. Defer disruptive reloads while base editors are active.
If production fails, restore the scoped application change and return to dev;
do not publish a failing candidate. Never use a source backup as a substitute for
customer-disk backups.

Known limits: application RPM publication for EL10 does not prove a fresh EL10
dependency installation; availability of the native MYKOS CRIU dependency and
live EL10 validation must be checked separately. A mocked suite or UBI app/login
test does not establish bare-metal KVM, CRIU or guest installation success.

## 7. Publish, verify and close the batch

1. Preserve the accepted source, final signed RPMs, public key, checksum manifest,
   source commit/release tag and compact validation receipts.
2. Publish to the matching custom EL9/EL10 MYKOS repositories using the existing
   release process. Refresh only those repositories' metadata. Preserve upstream
   mirror trees and active copies; do not mix application RPMs into them.
3. Publish the reviewed source/release on GitHub. Check for concurrent changes
   first. Never force-push or replace an existing published release artifact.
4. Fetch published artifacts and metadata; compare their bytes/checksums with the
   accepted signed files. Check signatures, versions and public documentation
   links. Keep `gpgcheck=1`; package signing alone does not sign repo metadata.
5. Verify dev/prod installed versions, installer manifests, affected endpoints
   and preservation of the pre-change workload/settings baseline.
6. Remove this batch's test-only containers, unreferenced images with non-forced
   removal, snapshots, mounts, build trees, staging and caches. Compare inventory
   with the pre-test baseline. Retain only accepted release evidence and necessary
   scoped installer rollback records. See [cleanup](TESTING_README.md#8-clean-up-and-record-results).
7. For the authorized hourly workflow, record completion evidence in its private
   ledger and send the completion email only after every required destination and
   cleanup check succeeds. Clear completed note items only under the established
   revision/text/timestamp compare-and-swap policy and after the email receipt.
   Leave unfinished, mixed or concurrently edited notes intact.

If a step fails, record the exact unfinished step and resume there. Do not label a
build signed, installed, published or tested until its corresponding check passes.
