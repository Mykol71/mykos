from pathlib import Path
import sys,importlib.machinery,importlib.util,json,os,subprocess,io,tarfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_runtime as runtime
import mykos_host_monitoring as monitor
NEW={v+str(n):(title,n) for v,title,ns in [('rocky','Rocky Linux',[8,9,10]),('alma','AlmaLinux',[8,9,10]),('oracle','Oracle Linux',[8,9,10]),('centos','CentOS Stream',[9,10]),('fedora','Fedora',[43,44])] for n in ns}

@pytest.mark.parametrize('os_name',NEW)
def test_image_identity_and_staging_without_guest_probe(os_name,monkeypatch):
    title,major=NEW[os_name];version=str(major) if os_name.startswith(('fedora','centos')) else str(major)+'.10'
    monkeypatch.setattr(monitor,'image_release',lambda *a:pytest.fail('No guest or image mount probe for labeled images'))
    data=monitor.image_os({'Image':'a'*64,'ImageName':'a'*64},{'RepoTags':['localhost/'+os_name+':latest'],'Labels':{'io.mykos.os.version':version}})
    assert data['os_type']==title and data['os_version']==version
    assert os_name in runtime.NATIVE_OS_NAMES and os_name in runtime.STAGE_OS_NAMES
    monkeypatch.setattr(runtime,'run',lambda *a,**kw:json.dumps([{'Names':[os_name],'Image':'localhost/'+os_name+':latest','State':'running'}]))
    import mykos_vm
    monkeypatch.setattr(mykos_vm,'prepared_templates',lambda:[])
    assert runtime.stage_images()==[{'os':os_name,'image':'localhost/'+os_name+':latest'}]

@pytest.mark.parametrize('os_name',NEW)
def test_native_kickstarts_keep_vendor_repos_and_guest_customizations(os_name):
    source=(ROOT/'pos/ks'/ (os_name+'.ks')).read_text()
    assert 'mykol.com' not in source and 'subscription-manager' not in source
    assert 'eth1' not in source and 'dhclient' not in source
    assert '-tuned' in source and 'rootpw --lock' in source and ': > /etc/machine-id' in source
    assert 'rm -f /etc/yum.repos.d' not in source and 'libreswan' in source
    assert source.count('%post ')==1
    subprocess.run(['bash','-n'],input=source.split('%post ',1)[1].split('\n',1)[1].split('%end',1)[0],text=True,check=True)

@pytest.mark.parametrize('os_name',NEW)
def test_native_dockerfile_labels_version_vendor_and_avoids_private_update_script(tmp_path,os_name):
    title,major=NEW[os_name];version=str(major) if os_name.startswith(('fedora','centos')) else str(major)+'.10'
    with tarfile.open(tmp_path/(os_name+'-docker.tar.xz'),'w:xz') as t:
        b=('VERSION_ID="'+version+'"\n').encode();m=tarfile.TarInfo('etc/os-release');m.size=len(b);t.addfile(m,io.BytesIO(b))
    source=(ROOT/'pos/pos').read_text();function=source[source.index('create_dockerfile() {'):source.index('stop_container() {')]
    subprocess.run(['bash','-c',function+'\ncreate_dockerfile'],cwd=tmp_path,env={**os.environ,'OS':os_name,'ROOTDIR':str(ROOT/'pos'),'MYKOS_PYTHON':sys.executable,'REPOSERVER':'private.example'},check=True)
    dockerfile=(tmp_path/'Dockerfile').read_text();assert 'io.mykos.os.version="'+version+'"' in dockerfile
    assert 'org.label-schema.vendor="'+title+'"' in dockerfile
    assert 'private.example' not in dockerfile and 'updateos' not in dockerfile
