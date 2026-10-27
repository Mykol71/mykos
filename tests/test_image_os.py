"""Image identity uses immutable host metadata and never guest os-release."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_host_monitoring as host


def test_native_image_reference_prefers_os_tag_over_snapshot_pin(monkeypatch):
    monkeypatch.setattr(host, 'image_release', lambda image_id: None)
    info={'ImageName':'a'*64, 'Image':'a'*64}
    image={'RepoTags':['localhost/mykos-snapshots/01234567:123','localhost/rh8:build-20261025']}
    assert host.image_name(info,image)=='localhost/rh8:build-20261025'
    assert host.image_os(info,image)=={'os_type':'Red Hat Enterprise Linux','os_version':'8','os_codename':'Ootpa','os_source':'image','image_id':'a'*64}


def test_image_catalog_batches_and_keeps_immutable_identity(monkeypatch):
    calls=[]
    def run(args,timeout=15):
        calls.append(args)
        return '[{"Id":"sha256:aaa","RepoTags":["localhost/rh9:latest"],"Labels":{"version":"9.6"}}]'
    monkeypatch.setattr(host,'run',run)
    infos=[{'Image':'aaa'},{'Image':'aaa'}]
    catalog=host.image_catalog(infos)
    assert len(calls)==1 and calls[0][-1]=='aaa'
    assert host.image_os(infos[0],host.image_info(infos[0],catalog))['os_version']=='9.6'


def test_vm_image_identity_and_unknown_are_not_guest_probes():
    info={'Image':'abc','Config':{'Labels':{'io.mykos.vm.os':'win11'}}}
    assert host.image_os(info,{})['os_version']=='11'
    assert host.image_os({'ImageName':'private/customer:latest'}, {})['os_version']=='Unknown'


def test_full_redhat_label_never_mounts_image(monkeypatch):
    monkeypatch.setattr(host,'image_release',lambda *a:(_ for _ in ()).throw(AssertionError('No mount for labeled images')))
    assert host.image_os({'ImageName':'localhost/rh9:latest'}, {'Labels':{'io.mykos.os.version':'9.8'}})['os_version']=='9.8'


def test_existing_image_version_is_cached_and_unmounted(tmp_path,monkeypatch):
    cache=tmp_path/'cache';root=tmp_path/'image';(root/'usr/lib').mkdir(parents=True)
    (root/'usr/lib/os-release').write_text('NAME="Red Hat Enterprise Linux"\nVERSION_ID="8.10"\n')
    monkeypatch.setattr(host,'IMAGE_OS_CACHE',cache)
    calls=[]
    def run(args,**kw):
        calls.append(args);return str(root) if args[2]=='mount' else ''
    monkeypatch.setattr(host,'run',run)
    assert host.image_release('a'*64)=='8.10'
    assert host.image_release('a'*64)=='8.10'
    assert len(calls)==2 and calls[-1][2]=='unmount'
    assert host.image_release('b'*64)=='8.10'
    assert len(calls)==4


def test_image_release_rejects_escape_and_caches_failure(tmp_path,monkeypatch):
    root=tmp_path/'image';(root/'etc').mkdir(parents=True)
    outside=tmp_path/'outside';outside.write_text('VERSION_ID="9.8"')
    (root/'etc/os-release').symlink_to(outside)
    monkeypatch.setattr(host,'IMAGE_OS_CACHE',tmp_path/'cache')
    calls=[]
    monkeypatch.setattr(host,'run',lambda args,**kw:calls.append(args) or (str(root) if args[2]=='mount' else ''))
    assert host.image_release('a'*64) is None
    assert host.image_release('a'*64) is None
    assert len(calls)==2


def test_build_labels_full_version_from_archive_in_build_directory(tmp_path):
    import tarfile,io,subprocess,os
    root=Path(__file__).resolve().parents[1]
    with tarfile.open(tmp_path/'rh9-docker.tar.xz','w:xz') as archive:
        content=b'NAME="Red Hat Enterprise Linux"\nVERSION_ID="9.8"\n'
        member=tarfile.TarInfo('usr/lib/os-release');member.size=len(content);archive.addfile(member,io.BytesIO(content))
    source=(root/'pos/pos').read_text()
    function=source[source.index('create_dockerfile() {'):source.index('stop_container() {')]
    subprocess.run(['bash','-c',function+'''\ncurl() { printf '#!/bin/bash\\necho test\\n' > "${@: -1}"; }
create_dockerfile'''],cwd=tmp_path,
                   env={**os.environ,'MYKOS_PYTHON':sys.executable,'ROOTDIR':str(root/'pos'),'OS':'rh9','REPOSERVER':'example.org'},check=True)
    assert 'LABEL io.mykos.os.version="9.8"' in (tmp_path/'Dockerfile').read_text()


def test_sql_image_identity_uses_product_version_without_guest_probe(monkeypatch):
    monkeypatch.setattr(host, 'image_release', lambda *args: (_ for _ in ()).throw(AssertionError('No SQL guest probe')))
    info={'ImageName':'a'*64,'Image':'a'*64}
    image={'RepoTags':['localhost/mykos-snapshots/01234567:123','localhost/mssql2022:latest'],
           'Labels':{'io.mykos.os':'mssql2022','io.mykos.os.version':'9.6'}}
    assert host.image_name(info,image)=='localhost/mssql2022:latest'
    assert host.image_os(info,image)['os_type']=='Microsoft SQL Server'
    assert host.image_os(info,image)['os_version']=='2022'
