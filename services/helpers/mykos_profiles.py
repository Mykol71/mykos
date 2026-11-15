"""Explicit application adapters. Generic virtualization selects none by default."""
from pathlib import Path
import mykos_runtime as runtime


def adapter(settings=None):
    value=(runtime.config() if settings is None else settings).get('application_adapter','none')
    if value!='none':raise ValueError('A business adapter requires its separately installed extension')
    return value


def has_backup(shop, settings=None):
    from mykos_backup_restore import available
    return available(shop,runtime.config() if settings is None else settings)


def mounts(shop, settings=None):
    from mykos_backup_restore import mount_arguments
    return mount_arguments(shop,runtime.config() if settings is None else settings)


def services():
    return ()


def backup_type(path):
    return 'rsync'


def snapshot_vm_identity(current, source, replacement=None):
    """Optional edition adapter for identity-bound VM assignment metadata."""
    return {}


def el_vm():
    """Load the shared public EL VM builder; no application adapter required."""
    import importlib.util,sys
    name='_mykos_el_vm'
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name('mykos-el-vm.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        try:spec.loader.exec_module(module)
        except BaseException:sys.modules.pop(name,None);raise
    return sys.modules[name]
