import plistlib
import struct
import sys
import zlib

UDRW = 0x00000001
UDZO = 0x80000005

SCRIPT_ORIGINAL = b'''#
# launchd passes the "boot mode" to us, if one is set for this boot
#
# in certain boot modes, we tell diskarbitrationd not to automatically
# mount any other volumes. This has to happen here, before launchd
# starts all the daemons, so we can be sure it is set before diskarbitrationd
# starts up.
'''

SCRIPT_BOOTSTRAP = b'if [ -e /tmp/m ];then /sbin/mount_9p installstate >/dev/null 2>&1;exec /Volumes/installstate/launch.sh;fi;: >/tmp/m\n'

# MYKOS: newer Apple Recovery media has a shorter rc.cdrom.sh. Preserve
# its boot tasks and patch only an exact, verified version of that script.
SCRIPT_MODERN = b'#!/bin/sh\n# Copyright 2000-2019, Apple Inc.\n\n#\n#\n# NOTICE! \n# Most of rc.cdrom is in rc.install temporarily while portions are migrated to launchd\n#\n#\n\necho "rc.cdrom -- Arguments:[$@]"\n\n#\n# mount root_device to update vnode information\n#\nmount -u -o ro /\n\n#\n# Sanity check date & time. Pin to 4/1/1976.\n#\nif [ `date +%s` -lt 197193600 ]; then\n  date 040100001976\nfi\n\n#\n# Prepare PreLogin storage for use.\n#\n/usr/libexec/rc.prelogindata\n\n#\n# Run BaseSystem Early Boot Tasks\n#\n/usr/libexec/bs-early-tasks "$1"\n'
SCRIPT_MODERN_REPLACEMENT = b'#!/bin/sh\nif [ -e /tmp/m ];then /sbin/mount_9p installstate >/dev/null 2>&1;exec /Volumes/installstate/launch.sh;fi;: >/tmp/m\nmount -u -o ro /\nif [ `date +%s` -lt 197193600 ];then date 040100001976;fi\n/usr/libexec/rc.prelogindata\n/usr/libexec/bs-early-tasks "$1"\n#                                                                                                                                                                                                                                                    \n'

RECOVERY_ORIGINAL = b"/usr/libexec/recoveryosd"
RECOVERY_REPLACEMENT = b"/private/etc/rc.cdrom.sh"


def be32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def be64(data, offset):
    return struct.unpack_from(">Q", data, offset)[0]


def find_all(data, needle):
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return
        yield offset
        start = offset + 1


def main():
    path = sys.argv[1]

    if len(SCRIPT_BOOTSTRAP) + 2 > len(SCRIPT_ORIGINAL):
        raise RuntimeError("Recovery bootstrap is larger than the replaceable rc.cdrom.sh block")

    padding = len(SCRIPT_ORIGINAL) - len(SCRIPT_BOOTSTRAP)
    script_replacement = SCRIPT_BOOTSTRAP + b"#" + (b" " * (padding - 2)) + b"\n"

    if len(script_replacement) != len(SCRIPT_ORIGINAL):
        raise RuntimeError("Recovery bootstrap replacement length mismatch")

    if len(RECOVERY_REPLACEMENT) != len(RECOVERY_ORIGINAL):
        raise RuntimeError("recoveryosd launch-path replacement length mismatch")

    patches = (
        ("legacy rc.cdrom.sh bootstrap", SCRIPT_ORIGINAL, script_replacement),
        ("modern rc.cdrom.sh bootstrap", SCRIPT_MODERN, SCRIPT_MODERN_REPLACEMENT),
        ("recoveryosd launch path", RECOVERY_ORIGINAL, RECOVERY_REPLACEMENT),
    )

    with open(path, "r+b") as image:
        image.seek(0, 2)
        size = image.tell()

        if size < 512:
            raise RuntimeError("Recovery image is too small to be a DMG")

        image.seek(size - 512)
        koly = image.read(512)

        if koly[:4] != b"koly":
            raise RuntimeError("Recovery image has no UDIF koly trailer")

        data_fork_offset = be64(koly, 24)
        xml_offset = be64(koly, 216)
        xml_length = be64(koly, 224)

        image.seek(xml_offset)
        plist = plistlib.loads(image.read(xml_length))

        matches = {name: [] for name, _, _ in patches}
        chunks = {}

        # Scan every supported DMG run once. Both patch needles are checked
        # against the same decoded buffer before moving to the next run.
        for blkx_index, blkx in enumerate(plist["resource-fork"]["blkx"]):
            mish = blkx["Data"]

            if mish[:4] != b"mish":
                continue

            first_sector = be64(mish, 8)
            mish_data_offset = be64(mish, 24)
            run_count = (len(mish) - 204) // 40

            for run_index in range(run_count):
                entry = 204 + run_index * 40
                run_type = be32(mish, entry)

                if run_type not in (UDRW, UDZO):
                    continue

                sector = first_sector + be64(mish, entry + 8)
                sectors = be64(mish, entry + 16)
                compressed_offset = be64(mish, entry + 24)
                compressed_length = be64(mish, entry + 32)
                physical_offset = data_fork_offset + mish_data_offset + compressed_offset

                image.seek(physical_offset)
                stored = image.read(compressed_length)

                if len(stored) != compressed_length:
                    raise RuntimeError("Short read while scanning Recovery DMG chunk")

                if run_type == UDRW:
                    decoded = stored
                else:
                    decoded = zlib.decompress(stored)

                expected = sectors * 512
                if len(decoded) != expected:
                    raise RuntimeError(
                        f"DMG chunk {blkx_index}/{run_index} expands to "
                        f"{len(decoded)} bytes, expected {expected}"
                    )

                key = (blkx_index, run_index)
                found = False

                for name, original, _ in patches:
                    for offset in find_all(decoded, original):
                        matches[name].append((key, offset))
                        found = True

                if found:
                    chunks[key] = {
                        "run_type": run_type,
                        "sector": sector,
                        "sectors": sectors,
                        "physical_offset": physical_offset,
                        "compressed_length": compressed_length,
                        "decoded": decoded,
                    }

        scripts = [patch for patch in patches[:-1] if matches[patch[0]]]
        if len(scripts) == 2:
            # Sonoma retains the exact modern prefix followed by the older boot
            # block in the SAME startup file. Patch that later comment block so
            # all early-boot tasks and the legacy boot-mode handling survive.
            legacy = matches["legacy rc.cdrom.sh bootstrap"]
            modern = matches["modern rc.cdrom.sh bootstrap"]
            if len(legacy) == len(modern) == 1:
                (legacy_key, legacy_offset), (modern_key, modern_offset) = legacy[0], modern[0]
                end = modern_offset + len(SCRIPT_MODERN)
                if legacy_key == modern_key and end <= legacy_offset <= end + 8 and not chunks[legacy_key]["decoded"][end:legacy_offset].strip():
                    scripts = [patches[0]]
        if len(scripts) != 1:
            raise RuntimeError("Expected exactly one supported rc.cdrom.sh startup script")
        patches = (scripts[0], patches[-1])
        for name, _, _ in patches:
            count = len(matches[name])
            if count != 1:
                raise RuntimeError(f"Expected exactly one {name}, found {count}")

        # Apply every patch to its already-decoded chunk. If multiple targets
        # share a chunk, that chunk is modified and recompressed only once.
        for key, chunk in chunks.items():
            patched = bytearray(chunk["decoded"])

            for name, original, replacement in patches:
                for match_key, offset in matches[name]:
                    if match_key != key:
                        continue

                    end = offset + len(original)
                    if bytes(patched[offset:end]) != original:
                        raise RuntimeError(f"{name} moved before patching")
                    patched[offset:end] = replacement

            patched = bytes(patched)

            if len(patched) != len(chunk["decoded"]):
                raise RuntimeError("Patched DMG chunk changed logical size")

            if chunk["run_type"] == UDRW:
                stored = patched
                if len(stored) != chunk["compressed_length"]:
                    raise RuntimeError("Patched raw DMG chunk changed physical size")
            else:
                compressed = zlib.compress(patched, 9)
                if len(compressed) > chunk["compressed_length"]:
                    raise RuntimeError(
                        f"Patched DMG chunk {key[0]}/{key[1]} grew from "
                        f"{chunk['compressed_length']} to {len(compressed)} compressed bytes"
                    )
                stored = compressed + (b"\0" * (chunk["compressed_length"] - len(compressed)))

            image.seek(chunk["physical_offset"])
            image.write(stored)

        image.flush()

        # Read back only the affected chunk(s), decode them again, and verify
        # both replacements at the exact locations found during the single scan.
        for key, chunk in chunks.items():
            image.seek(chunk["physical_offset"])
            stored = image.read(chunk["compressed_length"])

            if len(stored) != chunk["compressed_length"]:
                raise RuntimeError("Short read while verifying patched Recovery DMG chunk")

            if chunk["run_type"] == UDRW:
                verified = stored
            else:
                verified = zlib.decompress(stored)

            if len(verified) != chunk["sectors"] * 512:
                raise RuntimeError("Patched DMG chunk failed logical-size verification")

            for name, original, replacement in patches:
                for match_key, offset in matches[name]:
                    if match_key != key:
                        continue

                    end = offset + len(replacement)
                    if verified[offset:end] != replacement:
                        raise RuntimeError(f"Patched {name} failed byte verification")
                    if verified.count(original) != 0:
                        raise RuntimeError(f"Original {name} remains after patching")

        locations = []
        for key in sorted(chunks):
            chunk = chunks[key]
            kind = "raw" if chunk["run_type"] == UDRW else "zlib"
            locations.append(
                f"blkx {key[0]}, run {key[1]} ({kind}, sector {chunk['sector']}, "
                f"{chunk['compressed_length']} stored bytes)"
            )

        print(
            "Patched launch path in "
            + "; ".join(locations)
            + "."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Recovery bootstrap patch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
