from pathlib import Path
import sys,importlib.machinery,importlib.util,json,os,subprocess,io,tarfile
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/helpers'))
import mykos_runtime as runtime
import mykos_host_monitoring as monitor
NEW={v+str(n):(title,n) for v,title,ns in [('rocky','Rocky Linux',[8,9,10]),('alma','AlmaLinux',[8,9,10]),('oracle','Oracle Linux',[8,9,10]),('centos','CentOS Stream',[8,9,10]),('fedora','Fedora',[43,44])] for n in ns}

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
    assert 'mykol.com' not in source
    if 'subscription-manager' in source:assert 'if [ -f /etc/yum/pluginconf.d/subscription-manager.conf ]; then' in source
    assert 'eth1' not in source and 'dhclient' not in source
    assert '-tuned' in source and 'rootpw --lock' in source
    if not os_name.startswith('fedora'):
        major=NEW[os_name][1];parent=(ROOT/'pos/ks'/('rh'+str(major)+'.ks')).read_text()
        original_packages=parent.split('%packages',1)[1].split('%end',1)[0].splitlines()[1:]
        derived_packages=source.split('%packages',1)[1].split('%end',1)[0].splitlines()[1:]
        def seeds(lines):return {line for line in lines if line and not line.startswith(('#','-'))}
        parent_runtime=seeds(original_packages)-{'redhat-release','curl','openssl','nmap-ncat'}
        assert parent_runtime <= seeds(derived_packages)
        assert 'redhat-release' not in seeds(derived_packages)
        # Clones keep vendor release packages and omit our updateos-only tools.
        assert not {'curl','openssl','nmap-ncat'} & seeds(derived_packages)
    assert 'rm -f /etc/yum.repos.d' not in source and 'libreswan' in source
    assert len(__import__('re').findall(r'^%post(?:\s|$)',source,flags=__import__('re').M))==1
    subprocess.run(['bash','-n'],input=__import__('re').split(r'^%post[^\n]*\n',source,flags=__import__('re').M,maxsplit=1)[1].split('%end',1)[0],text=True,check=True)

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


@pytest.mark.parametrize('payload,status,success',[
    ('#!/bin/bash\necho ready\n',0,True),
    ('#!/bin/bash\necho partial\n',22,False),
    ('',0,False),
    ('if then\n',0,False),
])
def test_redhat_minimal_image_stages_validated_update_script_on_host(tmp_path,payload,status,success):
    with tarfile.open(tmp_path/'rh10-docker.tar.xz','w:xz') as t:
        data=b'VERSION_ID="10.1"\n';m=tarfile.TarInfo('etc/os-release');m.size=len(data);t.addfile(m,io.BytesIO(data))
    # No curl binary exists in this rootfs. Downloads run only on the host.
    (tmp_path/'updateos').write_text('previous valid script')
    source=(ROOT/'pos/pos').read_text();function=source[source.index('create_dockerfile() {'):source.index('stop_container() {')]
    script=function+"\ncurl() { printf '%s' \"$TEST_PAYLOAD\" > \"${@: -1}\"; return \"$TEST_STATUS\"; }; create_dockerfile\n"
    result=subprocess.run(['bash','-c',script],cwd=tmp_path,env={**os.environ,'OS':'rh10','ROOTDIR':str(ROOT/'pos'),'MYKOS_PYTHON':sys.executable,'REPOSERVER':'test.invalid','TEST_PAYLOAD':payload,'TEST_STATUS':str(status)},text=True,capture_output=True)
    assert (result.returncode==0)==success,result.stdout+result.stderr
    assert not list(tmp_path.glob('.mykos-updateos.*'))
    if success:
        dockerfile=(tmp_path/'Dockerfile').read_text()
        assert 'COPY updateos /usr/bin/updateos' in dockerfile and 'RUN curl' not in dockerfile
        assert (tmp_path/'updateos').read_text()==payload
        assert (tmp_path/'updateos').stat().st_mode & 0o777==0o755
    else:
        assert not (tmp_path/'Dockerfile').exists()
        assert (tmp_path/'updateos').read_text()=='previous valid script'
