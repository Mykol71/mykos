from pathlib import Path
import os,struct,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_backup_identity as identity


def data(users='',groups='',root_hash='base-root'):
    return {'passwd':'root:x:0:0:root:/root:/bin/bash\n'+users,
            'group':'root:x:0:\n'+groups,'shadow':'root:'+root_hash+':1:0:99999:7:::\n','gshadow':'root:!::\n'}


def test_customer_ids_preserved_and_conflicting_os_accounts_relocated():
    base=data('daemon:x:50:50:daemon:/var/lib/daemon:/sbin/nologin\n','daemon:x:50:\n')
    backup=data('alice:x:50:50:Alice:/home/alice:/bin/bash\n','customer:x:50:alice\n',root_hash='customer-root')
    backup['shadow']+='alice:customer-secret:1:0:99999:7:::\n'
    original=dict(backup);plan=identity.merge(base,backup)
    assert plan['uid_map'][50]!=50 and plan['gid_map'][50]!=50
    assert 'alice:x:50:50:' in plan['files']['passwd']
    assert f"daemon:x:{plan['uid_map'][50]}:{plan['gid_map'][50]}:" in plan['files']['passwd']
    assert 'base-root' in plan['files']['shadow'] and 'customer-root' not in plan['files']['shadow']
    assert 'customer-secret' in plan['files']['shadow'] and 'customer-secret' not in repr(plan['summary'])
    assert backup==original


def test_image_application_files_remap_to_customer_account_ids():
    base=data('app:x:500:500:app:/srv/app:/bin/bash\n','app:x:500:\n')
    backup=data('app:x:1001:1002:app:/home/app:/bin/bash\n','app:x:1002:app\n')
    plan=identity.merge(base,backup)
    assert plan['uid_map']=={500:1001} and plan['gid_map']=={500:1002}
    assert 'app:x:1001:1002:app:/srv/app:/bin/bash' in plan['files']['passwd']


def test_explicit_receiver_ids_and_old_backup_account_removed_without_source_change():
    customer=data('tfsupport:x:1000:1000:support:/home/tfsupport:/bin/bash\ntfrsync-00003501:x:1004:1004:backup:/home/tfrsync-00003501:/bin/bash\n','rti:x:1000:tfsupport\ntfrsync-00003501:x:1004:\n')
    before=dict(customer)
    adjusted=identity.adjusted_customer(customer,{'users':{'tfsupport':1011},'groups':{'rti':1012}},exclude=['tfrsync-00003501'])
    assert 'tfsupport:x:1011:1012:' in adjusted['passwd']
    assert 'rti:x:1012:tfsupport' in adjusted['group']
    assert 'tfrsync-00003501' not in repr(adjusted) and customer==before


def test_acl_named_user_and_group_ids_are_reconciled():
    acl=struct.pack('<IHHIHHIHHI',2,1,7,0xffffffff,2,6,500,8,4,600)
    updated=identity.remap_acl(acl,{500:1500},{600:1600})
    assert struct.unpack_from('<I',updated,16)[0]==1500
    assert struct.unpack_from('<I',updated,24)[0]==1600
    with pytest.raises(ValueError):identity.remap_acl(b'bad',{}, {})


@pytest.mark.parametrize('text',['evil:x:0:0:admin:/root:/bin/bash\n','bad:x:-1:0:bad:/tmp:/bin/bash\n','root:x:1:0:root:/root:/bin/bash\n'])
def test_invalid_or_extra_root_identity_rejected(text):
    with pytest.raises(ValueError):identity.merge(data(),{'passwd':text,'group':'root:x:0:\n'})


def test_ids_can_swap_without_changing_the_input_records():
    base=data('app1:x:1000:1000:a:/home/a:/bin/bash\napp2:x:1001:1001:b:/home/b:/bin/bash\n','a:x:1000:\nb:x:1001:\n')
    backup=data('app1:x:1001:1000:a:/home/a:/bin/bash\napp2:x:1000:1001:b:/home/b:/bin/bash\n','a:x:1000:\nb:x:1001:\n')
    plan=identity.merge(base,backup)
    assert plan['uid_map']=={1000:1001,1001:1000}


def test_identity_reader_rejects_symlink_escape(tmp_path):
    root=tmp_path/'backup';root.mkdir();outside=tmp_path/'host';outside.mkdir();(outside/'passwd').write_text('host private')
    (root/'etc').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):identity.read_files(root)
