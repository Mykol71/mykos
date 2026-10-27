#!/bin/bash
set -u

PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

LABEL="com.dockur.macos.firstboot"
BASE="/Library/Application Support/macos-unattended"
ADMIN_PACKAGE="$BASE/admin.pkg"
DAEMON="/Library/LaunchDaemons/$LABEL.plist"
USER_DIR="/private/var/db/dslocal/nodes/Default/users"
ADMIN_PLIST="/private/var/db/dslocal/nodes/Default/groups/admin.plist"
LOG="/var/log/macos-unattended-firstboot.log"

log() {
  printf '%s\n' "[firstboot] $1" >> "$LOG" 2>/dev/null || :
}

fail() {
  log "ERROR: $1"
  exit 1
}

log "first-boot account setup started"

[ -s "$ADMIN_PACKAGE" ] || fail "prebuilt account package is missing"
for command in /usr/bin/xar /usr/bin/gzip /usr/bin/cpio; do
  [ -x "$command" ] || fail "required system archive utility is missing"
done

count=0
while (( count < 300 )); do
  if [ -d "$USER_DIR" ] && [ -f "$ADMIN_PLIST" ]; then
    break
  fi
  count=$((count + 1))
  sleep 1
done

[ -d "$USER_DIR" ] || fail "local user database did not become available"
[ -f "$ADMIN_PLIST" ] || fail "admin group database did not become available"

log "local user database is available; installing prebuilt account package"

# Import private installation assets, then use native account management.
# Directory Services owns its protected database; never write its records directly.
# No SIP, sandbox, authentication or system-wide security setting is changed.
ACCOUNT="$BASE/account"
/bin/mkdir -p "$ACCOUNT" || fail "failed to create private account staging"
/bin/chmod 0700 "$ACCOUNT" || fail "failed to secure account staging"
if ! (
  set -eo pipefail
  cd "$ACCOUNT"
  /usr/bin/xar -xf "$ADMIN_PACKAGE"
  /usr/bin/gzip -dc component.pkg/Scripts | /usr/bin/cpio -id
  [ -s ./postinstall ] && [ -s ./user.plist ] && [ -s ./config ]
  /bin/bash ./postinstall "$ADMIN_PACKAGE" / /
) >> "$LOG" 2>&1; then
  fail "prebuilt administrator account setup failed"
fi

log "prebuilt account package installed successfully"

if ! /bin/rm -f "$DAEMON"; then
  fail "failed to remove first-boot LaunchDaemon"
fi

if ! /bin/rm -rf "$BASE"; then
  fail "failed to remove first-boot staging directory"
fi

log "first-boot account setup completed"
exit 0
