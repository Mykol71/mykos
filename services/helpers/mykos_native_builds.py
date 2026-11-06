"""Read-only dashboard summaries for active native OCI image builds."""
import mykos_prune
from mykos_runtime import native_build_os

OCI_OS_NAMES=frozenset(('debian13','archlinux','sles15sp7','amazon2023','alpine'))

def installing_images():
    # Lifecycle discovery already verifies the builder executable and its OS.
    # Never expose process arguments, host paths, credentials or a fake console.
    selected={native_build_os(value.get('args') or []) for value in mykos_prune.builders().values()}
    return [{'os':os_name,'name':os_name,'unattended':True,
             'build_state':'building',
             'build_message':'Building the native image from its vendor base; no installation console is required.'}
            for os_name in sorted(selected & OCI_OS_NAMES)]
