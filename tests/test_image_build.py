import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zlib
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_image_build as b

@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'STATE',tmp_path/'builds')
    monkeypatch.setattr(b,'origin',lambda:'https://mykos.example.com')
    monkeypatch.setattr(b.vm,'settings',lambda:('192.0.2.10','127.0.0.1'))
    monkeypatch.setattr(b.vm,'storage',lambda key:tmp_path/key)
    return tmp_path


def test_credentials_are_generated_once_without_prompts(state):
    first=b.credentials('win10');assert len(first['password'])>=24
    assert b.credentials('win10')==first
    assert (b.folder('win10')/'credentials.json').stat().st_mode & 0o777==0o600
    with pytest.raises(ValueError):b.credentials('win10','administrator','short')


def test_completion_token_is_scoped_and_expires(state,monkeypatch):
    import hashlib,time
    path=b.folder('mac15')/'callback.json'
    b.write(path,json.dumps({'digest':hashlib.sha256(b'private-token').hexdigest(),'expires':time.time()+600,'count':0}))
    with pytest.raises(ValueError):b.callback('mac15','wrong-token')
    assert not b.callback('mac15','private-token')['remove']
    assert not b.callback('mac15','private-token')['remove']
    b.arm_second_boot('mac15')
    assert b.callback('mac15','private-token')['remove']
    with pytest.raises(ValueError):b.callback('mac15','private-token')
    with pytest.raises(ValueError):b.callback('mac14','private-token')


def test_windows_answer_sets_both_passwords_and_keeps_driver_hook():
    source=b'''<unattend xmlns="urn:schemas-microsoft-com:unattend"><settings><UserAccounts><LocalAccounts><LocalAccount><Name>Docker</Name><Password><Value>old</Value><PlainText>false</PlainText></Password></LocalAccount></LocalAccounts><AdministratorPassword><Value>old</Value><PlainText>false</PlainText></AdministratorPassword></UserAccounts><AutoLogon><Username>Docker</Username><Enabled>false</Enabled><LogonCount>99</LogonCount><Password><Value>old</Value><PlainText>false</PlainText></Password></AutoLogon><Path>driver-hook.cmd</Path></settings></unattend>'''
    xml=b.windows_answer(source,{'username':'mykos','password':'PrivateTest9!long'})
    tree=ET.fromstring(xml);ns={'u':'urn:schemas-microsoft-com:unattend'}
    assert tree.find('.//u:LocalAccount/u:Name',ns).text=='mykos'
    assert tree.find('.//u:AutoLogon/u:LogonCount',ns).text=='3'
    import base64
    assert base64.b64decode(tree.find('.//u:AdministratorPassword/u:Value',ns).text).decode('utf-16le')=='PrivateTest9!longAdministratorPassword'
    assert b'driver-hook.cmd' in xml and b'PrivateTest9!' not in xml


def test_ubuntu_userdata_is_unattended_and_keeps_guest_fixes(monkeypatch):
    if sys.platform == "darwin":
        original=b.subprocess.run
        def openssl(args, **kwargs):
            if args[:2]==["openssl","passwd"]:
                assert kwargs["input"]=="PrivateTest9!long\n" and "PrivateTest9!long" not in args
                return subprocess.CompletedProcess(args,0,"$6$test-salt$test-hash\n","")
            return original(args,**kwargs)
        monkeypatch.setattr(b.subprocess,"run",openssl)
    text=b.ubuntu_userdata({'username':'mykos','password':'PrivateTest9!long'},b'print("setup")')
    value=json.loads(text.split('\n',1)[1])['autoinstall']
    assert value['identity']['password'].startswith('$6$')
    assert 'PrivateTest9!' not in text
    assert value['shutdown']=='poweroff'
    assert value['packages']==[]
    late=value['late-commands']
    update=next(i for i,line in enumerate(late) if 'apt-get -o Acquire::Retries=5 update' in line)
    install=next(i for i,line in enumerate(late) if 'install -y qemu-guest-agent libreswan' in line)
    complete=next(i for i,line in enumerate(late) if 'disk-installed' in line)
    assert update<install<complete
    assert value['apt']['preserve_sources_list'] is False
    assert all('universe' in line for line in value['apt']['sources_list'].splitlines())
    assert '$RELEASE-security' in value['apt']['sources_list']
    assert 'trusted=yes' not in str(value['apt'])
    assert 'ubuntu-desktop-minimal' not in value['packages']
    assert 'apt-get install -y ubuntu-desktop-minimal' in str(value['user-data'])
    assert 'installer-failed' in str(value['error-commands'])
    assert any('mykos-ubuntu-setup.py' in str(x) for x in value['late-commands'])
    assert 'image-install-complete' in str(value['user-data'])


def test_mac_package_is_private_valid_xar_with_provisioning_assets(state):
    values={'username':'mykos','password':'PrivateTest9!long'}
    target=state/'mac';target.mkdir()
    b.macos_assets('mac15',values,'token-for-tests',target)
    raw=(target/'admin.pkg').read_bytes()
    magic,size,version,compressed,uncompressed,algorithm=struct.unpack('>4sHHQQI',raw[:28])
    assert (magic,size,version,algorithm)==(b'xar!',28,1,1)
    toc=zlib.decompress(raw[28:28+compressed]);assert len(toc)==uncompressed
    assert b'PrivateTest9!' not in raw
    for name in ('firstboot.sh','launch.sh'):
        subprocess.run(['bash','-n',str(target/name)],check=True)
    assert 'startosinstall' in (target/'launch.sh').read_text()
    assert '--installpackage "$BOOTSTRAP_PACKAGE"' in (target/'launch.sh').read_text()
    assert (target/'bootstrap.pkg').stat().st_mode & 0o777==0o600
    assert b'PrivateTest9!' not in (target/'bootstrap.pkg').read_bytes()
    subprocess.run(['bash','-n',str(b.RECIPES/'macos/bootstrap-postinstall')],check=True)
    # macOS's own archive tool verifies TOC checksums and extraction layout.
    if Path('/usr/bin/xar').exists():
        dest=state/'extract';dest.mkdir()
        subprocess.run(['/usr/bin/xar','-xf',str(target/'admin.pkg')],cwd=dest,check=True)
        assert (dest/'component.pkg/Scripts').is_file()
        bootstrap=state/'bootstrap-extract';bootstrap.mkdir()
        subprocess.run(['/usr/bin/xar','-xf',str(target/'bootstrap.pkg')],cwd=bootstrap,check=True)
        import gzip
        scripts=gzip.decompress((bootstrap/'component.pkg/Scripts').read_bytes())
        assert b'./firstboot.sh' in scripts and b'./admin.pkg' in scripts
        assert b'/bin/bash "$MYDIR/setup-postinstall" "$@"' in scripts
        assert b'/bin/chmod 0700 "$SUPPORT/firstboot.sh"' in scripts



def test_one_command_has_no_input_prompts(monkeypatch,capsys):
    import importlib.machinery, importlib.util
    path=Path(__file__).resolve().parents[1]/'pos/vm'
    loader=importlib.machinery.SourceFileLoader('image_build_cli_test',str(path))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
    def prompt(*args):raise AssertionError('Unattended build prompted for input')
    monkeypatch.setattr('builtins.input',prompt)
    monkeypatch.setattr(cli.getpass,'getpass',prompt)
    calls=[]
    monkeypatch.setattr(b,'build',lambda name,**kw:calls.append((name,kw)) or {'ready':True})
    monkeypatch.setattr(sys,'argv',['vm','build','win10'])
    cli.main()
    assert calls==[('win10',{'replace':False})]
    assert 'credentials.json' in capsys.readouterr().out


def test_interrupted_build_resumes_existing_storage_without_recreation(state,monkeypatch):
    record=state/'record.json';record.write_text('{}')
    value={'os':'mac15','role':'template','unattended':True,'ready':False,'installation_complete':True}
    monkeypatch.setattr(b.vm,'record_path',lambda name:record)
    monkeypatch.setattr(b.vm,'read',lambda name:value)
    calls=[]
    monkeypatch.setattr(b,'renew_completion_window',lambda name:calls.append(('renew',name)))
    monkeypatch.setattr(b.vm,'resume',lambda name:calls.append(('resume',name)))
    monkeypatch.setattr(b.vm,'create',lambda *a,**kw:pytest.fail('Resume recreated an existing image'))
    monkeypatch.setattr(b,'boot_installed',lambda *a:pytest.fail('Resume repeated a completed device-detachment step'))
    monkeypatch.setattr(b,'wait_installed',lambda *a:None)
    monkeypatch.setattr(b.vm,'prepare',lambda *a,**kw:{'ready':True})
    monkeypatch.setattr(b.vm,'run',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('No upstream tag')))
    assert b.build('mac15')['ready']
    assert calls==[('renew','mac15'),('resume','mac15')]
    with pytest.raises(ValueError,match='existing build credentials'):b.build('mac15',username='different')


def test_callback_uses_supplied_ca_and_writes_private_curl_configuration(state,monkeypatch):
    ca=state/'ca.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=mykos.example.com','-days','1','-keyout',str(state/'test-key.pem'),'-out',str(ca)],check=True,capture_output=True)
    ca.chmod(0o600);monkeypatch.setattr(b,'BUILD_CA',ca)
    script=b.callback_script('mac15','test-completion-token')
    assert '--insecure' not in script
    prefix=script.split('/usr/bin/curl --fail',1)[0]
    result=subprocess.run(['bash','-c',prefix+'test -s "$ca" && grep -F "cacert" "$config"\n'],check=True,capture_output=True,text=True)
    assert 'cacert = "' in result.stdout
    ca.chmod(0o666)
    with pytest.raises(ValueError,match='CA must be'):b.callback_script('mac15','test-completion-token')


def test_guest_agent_rejects_unsigned_rpm_before_extracting_or_staging(state,monkeypatch):
    cache=b.STATE/'.tools';cache.mkdir(parents=True)
    package=cache/'virtio-win-test.rpm';package.write_bytes(b'not signed');package.chmod(0o600)
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0,'digests OK',''))
    monkeypatch.setattr(b,'agent_from_rpm',lambda *a:pytest.fail('Unsigned RPM payload was extracted'))
    with pytest.raises(ValueError,match='valid signature'):b.windows_agent_package('win10')
    assert not (state/'win10/oem/qemu-ga-x86_64.msi').exists()


def test_resumed_mac_callback_keeps_token_scope_and_boot_counter(state,monkeypatch):
    import hashlib
    path=b.folder('mac15')/'callback.json'
    digest=hashlib.sha256(b'original-scoped-token').hexdigest()
    b.write(path,json.dumps({'digest':digest,'expires':1,'count':1,'expected_boot':2}))
    monkeypatch.setattr(b.time,'time',lambda:10000)
    b.renew_completion_window('mac15')
    value=json.loads(path.read_text())
    assert value=={'digest':digest,'expires':24400,'count':1,'expected_boot':2}
    with pytest.raises(ValueError):b.callback('mac15','wrong-token')
    assert b.callback('mac15','original-scoped-token')['remove']
    b.renew_completion_window('mac15')
    with pytest.raises(ValueError):b.callback('mac15','original-scoped-token')


def test_failed_build_delete_refuses_ready_and_shared_storage(state,monkeypatch):
    import contextlib
    import mykos_containers as containers
    monkeypatch.setattr(b.vm,'locked',contextlib.nullcontext)
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**k:type('Result',(),{'stdout':'inactive'})())
    monkeypatch.setattr(b,'status',lambda name:{'state':'failed'})
    value={'role':'template','ready':True,'name':'ubuntu22.04','os':'ubuntu22.04'}
    monkeypatch.setattr(b.vm,'read',lambda key:value)
    with pytest.raises(ValueError,match='unfinished'):b.discard_failed('ubuntu22.04','ubuntu22.04')
    value['ready']=False
    folder=state/'ubuntu22.04';folder.mkdir();(folder/'disk').write_text('preserve')
    monkeypatch.setattr(b.vm,'inspect',lambda key:{'Id':'a'*64,'Config':{'Labels':{'io.mykos.vm.role':'template','io.mykos.vm.os':'ubuntu22.04'}}})
    monkeypatch.setattr(containers,'all_containers',lambda:[{'Id':'b'*64,'Name':'shared','Mounts':[{'Source':str(folder)}]}])
    monkeypatch.setattr(b.vm,'stop_container',lambda *a:(_ for _ in ()).throw(AssertionError('must not stop shared storage')))
    with pytest.raises(ValueError,match='also mounted'):b.discard_failed('ubuntu22.04','ubuntu22.04')
    assert (folder/'disk').read_text()=='preserve'


def test_ubuntu_firstboot_activates_desktop_without_waiting_for_reboot(state,monkeypatch):
    import yaml,crypt
    monkeypatch.setattr(crypt,'crypt',lambda *args:'$6$test-salt$test-hash')
    data=yaml.safe_load(b.ubuntu_userdata(b.credentials('ubuntu22.04'),b'pass'))
    commands=data['autoinstall']['user-data']['runcmd']
    serialized=json.dumps(commands)
    assert 'systemctl set-default graphical.target; systemctl start --no-block graphical.target' in serialized
    assert serialized.index('start --no-block graphical.target')<serialized.index('touch /var/lib/mykos/image-install-complete')


def test_mac_prebuilt_account_extracts_with_native_firstboot_utilities(state):
    if not Path('/usr/bin/xar').exists(): pytest.skip('Native macOS archive utilities required')
    import gzip,plistlib,hashlib
    target=state/'assets';target.mkdir()
    values={'username':'mykos','password':'PrivateTest9!long'}
    b.macos_assets('mac15',values,'test-token',target)
    out=state/'account';out.mkdir(mode=0o700)
    subprocess.run(['/usr/bin/xar','-xf',str(target/'admin.pkg')],cwd=out,check=True)
    scripts=gzip.decompress((out/'component.pkg/Scripts').read_bytes())
    subprocess.run(['/usr/bin/cpio','-id','--no-preserve-owner'],input=scripts,cwd=out,capture_output=True,check=True)
    user=plistlib.loads((out/'user.plist').read_bytes())
    assert user['name']==['mykos']
    assert (out/'user.plist').stat().st_mode & 0o777==0o600
    shadow=plistlib.loads(user['ShadowHashData'][0])['SALTED-SHA512-PBKDF2']
    assert hashlib.pbkdf2_hmac('sha512',values['password'].encode(),shadow['salt'],shadow['iterations'],dklen=128)==shadow['entropy']
    assert (out/'account-password').read_text()==values['password']+'\n'
    assert (out/'account-password').stat().st_mode & 0o777==0o600
    assert (out/'account-create.exp').is_file()
    assert (out/'postinstall').is_file() and (out/'config').is_file()
    subprocess.run(['bash','-n',str(b.RECIPES/'macos/firstboot.sh')],check=True)


def test_callback_origin_can_match_certificate_without_changing_browser_origin(tmp_path):
    env=tmp_path/'web.env'
    env.write_text('MYKOS_PUBLIC_ORIGIN=https://192.168.222.123\nMYKOS_IMAGE_BUILD_ORIGIN=https://dev.mykol.com\n')
    assert b.origin(env)=='https://dev.mykol.com'
    env.write_text('MYKOS_PUBLIC_ORIGIN=https://192.168.222.123\n')
    assert b.origin(env)=='https://192.168.222.123'
    env.write_text('MYKOS_PUBLIC_ORIGIN=https://192.168.222.123\nMYKOS_IMAGE_BUILD_ORIGIN=http://wrong.example\n')
    with pytest.raises(ValueError):b.origin(env)


def test_build_retry_reuses_committed_capture_without_reinstall(state,monkeypatch):
    record=state/'saved.json';record.write_text('{}')
    value={'role':'template','unattended':True,'ready':True,'committed':True}
    monkeypatch.setattr(b.vm,'record_path',lambda key:record)
    monkeypatch.setattr(b.vm,'read',lambda key:value)
    calls=[]
    monkeypatch.setattr(b.vm,'ensure_prepared_base',lambda key:calls.append(key) or {'ready':True})
    monkeypatch.setattr(b,'credentials',lambda *a:pytest.fail('credentials must not change'))
    monkeypatch.setattr(b,'wait_installed',lambda *a:pytest.fail('installation must not repeat'))
    monkeypatch.setattr(b.vm,'prepare',lambda *a,**k:pytest.fail('capture must not repeat'))
    assert b.build('win10')['ready']
    assert calls==['win10']


def test_pending_commit_build_skips_installer_and_capture_boots(state,monkeypatch):
    record=state/'record.json';record.write_text('{}')
    value={'os':'mac15','role':'template','unattended':True,'ready':False,'pending_commit':{'directory':'.capture-test'}}
    monkeypatch.setattr(b.vm,'record_path',lambda name:record)
    monkeypatch.setattr(b.vm,'read',lambda name:value)
    calls=[]
    monkeypatch.setattr(b.vm,'prepare',lambda name,**kw:calls.append(name) or {'ready':True})
    monkeypatch.setattr(b.vm,'resume',lambda *a:pytest.fail('Restarted a builder with a pending capture'))
    monkeypatch.setattr(b.vm,'create',lambda *a,**kw:pytest.fail('Recreated a pending build'))
    monkeypatch.setattr(b,'wait_installed',lambda *a:pytest.fail('Repeated installed boot checks'))
    monkeypatch.setattr(b.vm,'run',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('No upstream tag')))
    assert b.build('mac15')['ready'] and calls==['mac15']


def test_pending_commit_failure_reports_safe_retry(state,monkeypatch):
    record=state/'record.json';record.write_text('{}')
    value={'os':'mac15','role':'template','unattended':True,'ready':False,'pending_commit':{'directory':'.capture-test'}}
    monkeypatch.setattr(b.vm,'record_path',lambda name:record)
    monkeypatch.setattr(b.vm,'read',lambda name:value)
    monkeypatch.setattr(b.vm,'prepare',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('No space left')))
    reports=[]
    monkeypatch.setattr(b,'report',lambda *args:reports.append(args))
    with pytest.raises(RuntimeError,match='No space left'):b.build('mac15')
    assert reports[-1][1]=='failed'
    assert 'completed disk/RAM capture retained' in reports[-1][2]
    assert 'without reinstalling' in reports[-1][2]


def test_ubuntu_wrapper_uses_its_python_despite_host_helper_shebang(tmp_path):
    import os
    storage=tmp_path/'storage';state=storage/'.mykos-installstate';state.mkdir(parents=True)
    run=tmp_path/'run';run.mkdir()
    helper=run/'mykos-ubuntu-iso'
    helper.write_text('#!/no/such/host/python3.11\nfrom pathlib import Path\nPath('+repr(str(state/'iso-prepared'))+').touch()\n')
    helper.chmod(0o755)
    entry=run/'entry.sh';entry.write_text('#!/bin/sh\ntouch '+str(state/'disk-installed')+'\n');entry.chmod(0o755)
    source=(Path(b.__file__).parent/'mykos-unattended-entry').read_text().replace('/storage',str(storage)).replace('/run/',str(run)+'/')
    process=subprocess.run(['/bin/bash','-c',source],env={**os.environ,'MYKOS_UNATTENDED':'ubuntu'},capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    assert (state/'iso-prepared').exists() and (state/'boot-installed').exists()


def test_ubuntu_media_cache_tracks_seed_and_discards_vendor_boot_copy(tmp_path,monkeypatch):
    import hashlib,io
    storage=tmp_path/'storage';state=storage/'.mykos-installstate';state.mkdir(parents=True)
    official=b'checked official media';(storage/'installer.iso').write_bytes(official)
    (state/'user-data').write_text('old seed');(state/'meta-data').write_text('instance metadata')
    (storage/'custom.iso').write_bytes(b'old custom');(storage/'boot.iso').write_bytes(b'old boot')
    monkeypatch.setenv('MYKOS_UBUNTU_ISO_URL','https://releases.ubuntu.com/20.04/ubuntu-20.04.6-live-server-amd64.iso')
    monkeypatch.setattr(b.urllib.request,'urlopen',lambda *a,**k:io.BytesIO((hashlib.sha256(official).hexdigest()+'  ubuntu-20.04.6-live-server-amd64.iso\n').encode()))
    builds=[]
    def xorriso(args,**kwargs):
        if '-extract' in args:Path(args[-1]).write_text('set timeout=30\nlinux /casper/vmlinuz ---\n')
        else:
            builds.append(args);Path(args[args.index('-outdev')+1]).write_bytes((state/'user-data').read_bytes())
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(subprocess,'run',xorriso)
    code=(Path(b.__file__).parent/'mykos-ubuntu-iso').read_text().replace("root=Path('/storage')","root=Path("+repr(str(storage))+")")
    exec(compile(code,'mykos-ubuntu-iso','exec'),{})
    assert len(builds)==1 and not (storage/'boot.iso').exists()
    assert (storage/'custom.iso').read_text()=='old seed'
    (storage/'boot.iso').write_text('current boot')
    with pytest.raises(SystemExit) as unchanged:exec(compile(code,'mykos-ubuntu-iso','exec'),{})
    assert unchanged.value.code==0 and len(builds)==1 and (storage/'boot.iso').exists()
    (state/'user-data').write_text('corrected seed')
    exec(compile(code,'mykos-ubuntu-iso','exec'),{})
    assert len(builds)==2 and not (storage/'boot.iso').exists()
    assert (storage/'custom.iso').read_text()=='corrected seed'
