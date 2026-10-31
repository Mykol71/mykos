"""Use modern maintained kickstarts without breaking old RHEL 8 parsers."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pos'))
from prepare_ks import modernize,for_builder,container_build
import prepare_ks
import pytest


def test_update_preserves_custom_guest_changes_and_post_sections():
    original='timezone --isUtc --nontp UTC\n%anaconda\n# old policy\n%end\n%packages\nkernel\n%end\n%post\n# custom setting\n%end\n'
    result=modernize(original)
    assert result=='timezone --utc UTC\ntimesource --ntp-disable\n%packages\nkernel\n%end\n%post\n# custom setting\n%end\n'
    assert modernize(result)==result
    assert for_builder(result,True)==result
    old=for_builder(result,False)
    assert 'timesource' not in old and 'timezone --utc UTC --nontp' in old
    assert '%post\n# custom setting\n%end' in old


def test_sources_have_no_reported_deprecations():
    root=Path(__file__).resolve().parents[1]/'pos/ks'
    for name in ('rh8','rh9','rh10'):
        source=(root/(name+'.ks')).read_text()
        assert '--isUtc' not in source and '--nontp' not in source
        assert '\n%anaconda' not in source
        assert 'timesource --ntp-disable' in source


def test_existing_ntp_default_is_preserved():
    assert for_builder('timezone America/Chicago\n',False)=='timezone America/Chicago\n'


def test_weak_dependency_spelling_changes_only_package_header():
    source='%packages --nocore --exclude-weakdeps\n@core\n%end\n%post\necho --exclude-weakdeps\n%end\n'
    old=for_builder(source,False,'--excludeWeakdeps')
    assert '%packages --nocore --excludeWeakdeps\n' in old
    assert '%post\necho --exclude-weakdeps\n%end' in old
    assert for_builder(old,True,'--exclude-weakdeps')==source
    assert for_builder(source,True)==source


def test_unsupported_weak_dependencies_are_not_silently_dropped():
    import pytest
    with pytest.raises(ValueError,match='cannot exclude weak dependencies'):
        for_builder('%packages --exclude-weakdeps\n%end\n',False,None)


def test_language_filter_survives_old_and_new_builder_spellings():
    source='%packages --nocore --inst-langs=en\nbash\n%end\n%post\necho --inst-langs=en\n%end\n'
    old=for_builder(source,False,instlangs='--instLangs')
    assert '%packages --nocore --instLangs=en\n' in old
    assert '%post\necho --inst-langs=en\n%end' in old
    assert for_builder(old,True)==source
    with pytest.raises(ValueError,match='cannot limit installed languages'):
        for_builder(source,True,instlangs=None)


def test_generated_container_build_excludes_hardware_and_host_login_marker():
    source='%packages\n@system-tools\nbash\n-kernel\n%end\n%post\nsystemd-tmpfiles --create --boot\necho operator-setting\n%end\n'
    result=container_build(source)
    assert '@system-tools\nbash\n-kernel\n-linux-firmware\n-*-firmware\n-microcode_ctl\n%end' in result
    assert 'systemd-tmpfiles --create --boot --exclude-prefix=/run/nologin --exclude-prefix=/var/run/nologin\n' in result
    assert 'echo operator-setting\n%end' in result
    assert container_build(result)==result


def test_operator_package_list_with_implicit_kernel_keeps_its_firmware_dependencies():
    source='%packages --nocore\nsystemd\nsystemd-sysv\ninitscripts\nnet-snmp\n%end\n'
    assert container_build(source)==source


def test_nonboot_tmpfiles_and_other_sections_stay_intact():
    source='%post\nsystemd-tmpfiles --create\necho systemd-tmpfiles --create --boot\n%end\n'
    assert container_build(source)==source


@pytest.mark.parametrize('new_builder,name,adapted',[(True,'rh7.ks',True),(False,'rh7.ks',False),(True,'custom.ks',False)])
def test_rh7_authentication_is_deferred_only_in_generated_compat_copy(tmp_path,monkeypatch,new_builder,name,adapted):
    # Legacy operator kickstarts still require the cross-release auth shim.
    original=(Path(__file__).resolve().parents[1]/'pos/ks/rh7.ks').read_bytes()
    original=original.replace(b'rootpw --lock\n',b'rootpw --lock\nauth  --useshadow --passalgo=sha512\n',1)
    original=original.replace(b'\nbash\n',b'\nbash\nauthconfig\n',1)
    source=tmp_path/name;source.write_bytes(original)
    destination=tmp_path/'build.ks'
    monkeypatch.setattr(prepare_ks,'builder_capabilities',lambda:dict(timesource=False,weakdeps=None,authselect=new_builder))
    prepare_ks.prepare(source,destination)
    generated=destination.read_text()
    assert source.read_bytes()==original
    assert ('\nauth  --useshadow --passalgo=sha512\n' not in generated)==adapted
    assert ('/usr/sbin/authconfig --update --nostart --useshadow --passalgo=sha512' in generated)==adapted
    assert 'rootpw --lock\n' in generated
    # A second pass must not append another auth script.
    if adapted:assert prepare_ks.rh7_auth_compat(generated)==generated


def test_rh7_custom_authentication_is_rejected_without_writing_output(tmp_path,monkeypatch):
    source=tmp_path/'rh7.ks'
    source.write_text('auth --enableldap --ldapserver=example.test\n%packages\nauthconfig\n%end\n')
    destination=tmp_path/'build.ks';destination.write_text('existing build copy\n')
    monkeypatch.setattr(prepare_ks,'builder_capabilities',lambda:dict(timesource=False,weakdeps=None,authselect=True))
    with pytest.raises(ValueError,match='supports only'):
        prepare_ks.prepare(source,destination)
    assert destination.read_text()=='existing build copy\n'


def test_rh7_auth_post_preserves_options_and_propagates_failure(tmp_path):
    import subprocess
    source='auth --useshadow --passalgo=sha512\n%packages\nauthconfig\n%end\n%post\necho operator-setting\n%end\n'
    generated=prepare_ks.rh7_auth_compat(source)
    assert '%post\necho operator-setting\n%end' in generated
    assert '%post --erroronfail --log=/mykos-authconfig-post.log' in generated
    script=generated.rsplit('%post ',1)[1].split('\n',1)[1].split('%end',1)[0]
    executable=tmp_path/'authconfig'
    executable.write_text('#!/bin/sh\n[ "$*" = "--update --nostart --useshadow --passalgo=sha512" ] || exit 99\nexit 23\n')
    executable.chmod(0o755)
    result=subprocess.run(['bash','-c',script.replace('/usr/sbin/authconfig',str(executable))])
    assert result.returncode==23


@pytest.mark.parametrize('root_name',['sysroot','sysimage'])
@pytest.mark.parametrize('download',['ok','fail','empty','invalid'])
def test_rh7_update_script_is_staged_on_host_without_chroot_dns(tmp_path,root_name,download):
    import os,re,subprocess
    original=(Path(__file__).resolve().parents[1]/'pos/ks/rh7.ks').read_text()
    generated=prepare_ks.rh7_download_compat(original)
    assert prepare_ks.rh7_download_compat(generated)==generated
    sections=re.findall(r'^%post([^\n]*)\n(.*?)^%end\b',generated,flags=re.M|re.S)
    assert '--nochroot' in sections[0][0] and '--erroronfail' in sections[0][0]
    assert 'curl ' not in sections[1][1]
    assert '[ -s /usr/bin/updateos ]\nbash -n /usr/bin/updateos' in sections[1][1]
    # All original customization commands before the download are preserved.
    before=original.split('%post ',1)[1].split('\n',1)[1].split('updateos_tmp=',1)[0]
    assert before in sections[1][1]
    root=tmp_path/root_name;(root/'etc').mkdir(parents=True);(root/'usr/bin').mkdir(parents=True)
    (root/'etc/redhat-release').write_text('Red Hat Enterprise Linux Server release 7.9 (Maipo)\n')
    destination=root/'usr/bin/updateos';destination.write_text('previous script\n')
    tools=tmp_path/'bin';tools.mkdir();curl=tools/'curl'
    curl.write_text('''#!/bin/bash
while [ "$#" -gt 0 ]; do
    case "$1" in --output) output=$2; shift 2;; *) shift;; esac
done
case "$DOWNLOAD" in
  ok) printf '#!/bin/bash\\nexit 0\\n' > "$output" ;;
  fail) exit 6 ;;
  empty) : > "$output" ;;
  invalid) printf 'if then\\n' > "$output" ;;
esac
''');curl.chmod(0o755)
    script=sections[0][1].replace('/mnt/sysroot',str(tmp_path/'sysroot')).replace('/mnt/sysimage',str(tmp_path/'sysimage'))
    result=subprocess.run(['bash','-c',script],text=True,capture_output=True,
        env={**os.environ,'PATH':str(tools)+':'+os.environ['PATH'],'DOWNLOAD':download})
    assert (result.returncode==0)==(download=='ok'),result.stderr
    assert not list((root/'usr/bin').glob('.updateos.*'))
    if download=='ok':
        assert destination.read_text()=='#!/bin/bash\nexit 0\n'
        assert destination.stat().st_mode & 0o777==0o755
    else:assert destination.read_text()=='previous script\n'


def test_rh7_custom_download_is_not_silently_overwritten():
    original=(Path(__file__).resolve().parents[1]/'pos/ks/rh7.ks').read_text()
    with pytest.raises(ValueError,match='Custom RH7 updateos download'):
        prepare_ks.rh7_download_compat(original.replace('--retry 2','--retry 8'))
