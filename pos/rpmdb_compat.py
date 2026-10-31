"""Give legacy container rootfs builds a database their own RPM can read and write.

A SQLite-based installer can install EL7/8 files while writing an incompatible
RPM database. Re-register the exact solved packages with the target RPM using
--justdb. Package files and scriptlets are not reinstalled.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

QUERY='%{NAME}\t%{EPOCH}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n'


def inventory(output):
    values=[]
    for line in output.splitlines():
        parts=line.split('\t')
        if len(parts)!=5:raise ValueError('Invalid native RPM inventory')
        name,epoch,version,release,arch=parts
        if name=='gpg-pubkey':continue
        values.append((name,int(epoch) if epoch not in ('','(none)') else 0,version,release,arch))
    return sorted(values)


def run(arguments):
    return subprocess.check_output(arguments,stderr=subprocess.STDOUT,universal_newlines=True)


def native_inventory(root,database=None):
    command=['chroot',str(root),'rpm']
    if database is not None:command+=['--dbpath',str(database)]
    result=subprocess.run(command+['-qa','--qf',QUERY],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    if result.returncode:raise subprocess.CalledProcessError(result.returncode,command,result.stdout+result.stderr)
    return inventory(result.stdout)


def checked_root(root,major):
    root=Path(root)
    if root.is_symlink() or root.resolve()==Path('/') or not root.is_dir():raise ValueError('Unsafe image installation root')
    release=(root/'etc/redhat-release').read_text()
    import re
    if not re.search(r'\brelease\s+'+str(major)+r'(?:[. ]|$)',release):raise ValueError('Image release mismatch')
    database=root/'var/lib/rpm'
    if database.is_symlink() or root.resolve() not in database.resolve().parents:raise ValueError('RPM database escapes image root')
    return root,database


def download_exact(packages,repositories,major,directory):
    import dnf
    import dnf.repo
    with dnf.Base() as base:
        base.conf.cachedir=str(directory/'cache');base.conf.persistdir=str(directory/'persist');base.conf.logdir=str(directory)
        base.conf.plugins=False;base.conf.substitutions['releasever']=str(major)
        base.conf.module_platform_id='platform:el'+str(major)
        for index,url in enumerate(repositories):
            repo=dnf.repo.Repo('mykos-rpmdb-'+str(index),base.conf);repo.baseurl=[url];repo.module_hotfixes=True;base.repos.add(repo)
        base.fill_sack(load_system_repo=False)
        selected=[]
        for name,epoch,version,release,arch in packages:
            matches=base.sack.query().available().filter(name=name,epoch=epoch,version=version,release=release,arch=arch).run()
            if not matches:raise ValueError('Exact installed RPM is no longer available: '+name+'-'+version+'-'+release+'.'+arch)
            selected.append(matches[0])
        base.download_packages(selected)
        paths=directory/'packages';paths.mkdir(mode=0o700)
        for index,package in enumerate(selected):shutil.copyfile(package.localPkg(),str(paths/(str(index)+'.rpm')))
        return paths


def normalize(root,major,repositories):
    root,database=checked_root(root,major)
    if not (database/'rpmdb.sqlite').is_file():return {'converted':False,'reason':'Installer database is already native'}
    import rpm
    transaction=rpm.TransactionSet(str(root))
    expected=sorted((h['name'],int(h['epoch'] or 0),h['version'],h['release'],h['arch']) for h in transaction.dbMatch() if h['name']!='gpg-pubkey')
    del transaction
    if not expected:raise ValueError('Installer RPM database is empty')
    try:matching=native_inventory(root)==expected
    except subprocess.CalledProcessError:matching=False
    previous={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in database.iterdir() if p.name.startswith('rpmdb.sqlite')}
    staged=Path(tempfile.mkdtemp(prefix='.mykos-native-rpmdb-',dir=str(database.parent)))
    original=staged.with_name(staged.name+'-installer');changed=False
    try:
        if matching:
            # EL8 can query a newer installer's SQLite database, but its
            # compatibility backend is read-only. Rebuild a private copy with
            # target RPM, then open it for writing before publishing it.
            for source in database.iterdir():
                if source.is_symlink() or not source.is_file():raise ValueError('Unsafe installer RPM database entry')
                shutil.copy2(str(source),str(staged/source.name))
            db='/' + str(staged.relative_to(root))
            run(['chroot',str(root),'rpm','--dbpath',db,'--rebuilddb'])
            run(['chroot',str(root),'rpm','--dbpath',db,'--initdb'])
            if native_inventory(root,db)!=expected:raise ValueError('Rebuilt native RPM inventory did not match installed packages')
        else:
            with tempfile.TemporaryDirectory(prefix='mykos-rpmdb-download-',dir='/var/tmp') as temporary:
                packages=download_exact(expected,repositories,major,Path(temporary))
                guest=Path(tempfile.mkdtemp(prefix='.mykos-rpmdb-packages-',dir=str(root/'var/tmp')))
                try:
                    for p in packages.iterdir():shutil.copyfile(str(p),str(guest/p.name))
                    db='/' + str(staged.relative_to(root))
                    run(['chroot',str(root),'rpm','--dbpath',db,'--initdb'])
                    # Retain the distribution's public trust keys in the new database.
                    run(['chroot',str(root),'/bin/bash','-ceu','for key in /etc/pki/rpm-gpg/*; do [ -f "$key" ] || continue; rpm --dbpath "$1" --import "$key"; done','mykos-rpmdb',db])
                    paths=['/'+str(p.relative_to(root)) for p in sorted(guest.iterdir())]
                    # These are precisely the installer's already-solved package set.
                    # --justdb skips payload files; scripts and triggers are disabled.
                    run(['chroot',str(root),'rpm','--dbpath',db,'--install','--justdb','--noscripts','--notriggers','--nodeps','--excludedocs','--define','_install_langs en']+paths)
                    if native_inventory(root,db)!=expected:raise ValueError('Native RPM inventory did not match the installed package set')
                finally:shutil.rmtree(str(guest))
        staged.chmod(database.stat().st_mode & 0o777)
        for p in staged.iterdir():
            if p.is_file():p.chmod(0o644)
        current={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in database.iterdir() if p.name.startswith('rpmdb.sqlite')}
        if current!=previous:raise ValueError('Installer RPM database changed during conversion')
        os.rename(str(database),str(original));changed=True;os.rename(str(staged),str(database))
        if native_inventory(root)!=expected:raise ValueError('Native RPM validation failed after database replacement')
        changed=False;shutil.rmtree(str(original))
        return {'converted':True,'packages':len(expected),'backend':'native target RPM'}
    except BaseException:
        if changed:
            if database.exists():os.rename(str(database),str(staged))
            os.rename(str(original),str(database))
        raise
    finally:
        if staged.exists():shutil.rmtree(str(staged))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',choices=('/mnt/sysroot','/mnt/sysimage'))
    parser.add_argument('--major',type=int,choices=(7,8),required=True)
    parser.add_argument('--repo',action='append',required=True)
    arguments=parser.parse_args()
    if os.geteuid()!=0:parser.error('Root is required')
    print(json.dumps(normalize(arguments.root,arguments.major,arguments.repo),sort_keys=True))
