import importlib.util
import importlib.machinery
import json
from pathlib import Path
import subprocess
import pytest

loader = importlib.machinery.SourceFileLoader('bootstrap', str(Path(__file__).resolve().parents[1] / 'bin/mykos-bootstrap'))
spec = importlib.util.spec_from_loader(loader.name, loader)
b = importlib.util.module_from_spec(spec)
loader.exec_module(b)


def app(tmp_path):
    calls = []
    def run(args, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='installed')
    return b.Bootstrap('prod.example.com', tmp_path, run), calls


@pytest.mark.parametrize('name,expected', [('prod.mykol.com', 'mykol.com'), ('dev.EXAMPLE.COM.', 'example.com')])
def test_domain(name, expected):
    assert b.domain_name(name) == expected


@pytest.mark.parametrize('name', ['localhost', '192.168.222.5', 'bad;name.com', '../bad.com', 'bad.local'])
def test_invalid_domain(name):
    with pytest.raises(ValueError): b.domain_name(name)


def test_order_and_repeat(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    events = []
    for method in ('prerequisites', 'firewall', 'https', 'renewal', 'proxy', 'repository'):
        monkeypatch.setattr(a, method, lambda *args, name=method: events.append(name))
    monkeypatch.setattr(a, 'release', lambda: '9')
    monkeypatch.setattr(a, 'tls_ready', lambda: True)
    a.install()
    assert events == ['prerequisites', 'firewall', 'https', 'renewal', 'proxy', 'repository']
    setup = next(args for args in calls if args[0] == '/usr/sbin/mykos-setup')
    assert setup == ['/usr/sbin/mykos-setup', '--public-origin', 'https://example.com', '--set-public-origin', '--enable']
    calls.clear(); events.clear()
    a.install()
    assert events == []
    assert not any(c[0] in ('dnf', 'certbot', '/usr/sbin/mykos-setup') for c in calls)


def test_https_failure_blocks_rpm(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(a, 'release', lambda: '9')
    monkeypatch.setattr(a, 'prerequisites', lambda el: None)
    monkeypatch.setattr(a, 'firewall', lambda: None)
    def fail(email): raise ValueError('ACME failed')
    monkeypatch.setattr(a, 'https', fail)
    with pytest.raises(ValueError, match='ACME failed'): a.install()
    assert not any(c[0] in ('dnf', '/usr/sbin/mykos-setup') for c in calls)


def test_certbot_noninteractive_and_second_pass(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    probes = iter([False, True, True])
    monkeypatch.setattr(a, 'tls_ready', lambda: next(probes))
    a.https(None)
    command = next(c for c in calls if c[0] == 'certbot')
    for flag in ('certonly', '--non-interactive', '--agree-tos', '--keep-until-expiring', '--webroot', '--register-unsafely-without-email'):
        assert flag in command
    assert command[-4:-2] != ['--email', 'invented@example.com']
    assert a.state['certificate_created']
    assert 'SSLCertificateFile /etc/letsencrypt/live/example.com/fullchain.pem' in a.path('/etc/httpd/conf.d/00-mykos-https.conf').read_text()
    calls.clear(); a.https(None)
    assert calls == []


def test_configtest_failure_rolls_back(tmp_path):
    a, calls = app(tmp_path)
    def run(args, check=True):
        if args == ['httpd', '-t']: raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout='')
    a.run = run
    with pytest.raises(subprocess.CalledProcessError): a.apache_write('/etc/httpd/conf.d/00-mykos-https.conf', 'invalid')
    assert not a.path('/etc/httpd/conf.d/00-mykos-https.conf').exists()
    assert a.state['files'] == {}


def test_preserve_existing_config(tmp_path):
    a, calls = app(tmp_path)
    p = a.path('/etc/httpd/conf.d/00-mykos-https.conf')
    p.parent.mkdir(parents=True); p.write_text('operator config')
    with pytest.raises(ValueError, match='Preserving'): a.managed('/etc/httpd/conf.d/00-mykos-https.conf', 'new')
    assert p.read_text() == 'operator config'


def test_cleanup_owned_certificate_and_files(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(b.shutil, 'which', lambda name: None)
    a.managed('/etc/httpd/conf.d/00-mykos-https.conf', 'site')
    a.managed('/etc/systemd/system/mykos-certificate-renew.timer', 'timer')
    a.state['certificate_created'] = True; a.save()
    a.cert_path().parent.mkdir(parents=True); a.cert_path().write_text('public cert')
    a.cleanup()
    assert ['certbot', 'delete', '--non-interactive', '--cert-name', 'example.com'] in calls
    assert not a.state_path.exists()
    calls.clear(); a.cleanup()
    assert calls == []


def test_cleanup_preserves_shared_cert_and_edited_file(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(b.shutil, 'which', lambda name: None)
    name = '/etc/httpd/conf.d/00-mykos-https.conf'
    a.managed(name, 'original')
    a.path(name).write_text('SSLCertificateFile /etc/letsencrypt/live/example.com/fullchain.pem\n# operator edit')
    a.state['certificate_created'] = True; a.save()
    a.cleanup()
    assert a.path(name).exists()
    assert not any(c[0] == 'certbot' for c in calls)
    assert a.state_path.exists()


def test_existing_certificate_is_not_deleted(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(b.shutil, 'which', lambda name: None)
    a.save(); a.cleanup()
    assert not any(c[0] == 'certbot' for c in calls)


def test_rpm_upgrade_does_not_uninstall_bootstrap():
    spec = (Path(__file__).resolve().parents[1] / 'packaging/rpm/mykos.spec').read_text()
    block = spec.split('%preun\n', 1)[1].split('%postun', 1)[0]
    assert block.index('if [ "$1" -eq 0 ]; then') < block.index('mykos-bootstrap --uninstall') < block.index('\nfi')


def test_fresh_install_order(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    a.path('/etc/os-release').parent.mkdir(parents=True)
    a.path('/etc/os-release').write_text('ID="rhel"\nVERSION_ID="8.10"\n')
    monkeypatch.setattr(b.shutil, 'which', lambda name: '/usr/bin/certbot' if name == 'certbot' else None)
    monkeypatch.setattr(a, 'key', lambda name, url: '/etc/pki/rpm-gpg/RPM-GPG-KEY-MYKOS')
    probes = iter([False, True, True])
    monkeypatch.setattr(a, 'tls_ready', lambda: next(probes))
    def run(args, check=True):
        calls.append(args)
        if args == ['rpm', '-q', 'mykos']:
            return subprocess.CompletedProcess(args, 1, stdout='not installed')
        return subprocess.CompletedProcess(args, 0, stdout='1.5-73')
    a.run = run
    a.install('admin@example.com')
    cert = next(i for i,c in enumerate(calls) if c[0] == 'certbot')
    rpm = next(i for i,c in enumerate(calls) if c[:2] == ['dnf', '-y'] and c[-1] == 'mykos')
    setup = next(i for i,c in enumerate(calls) if c[0] == '/usr/sbin/mykos-setup')
    assert cert < rpm < setup
    assert '--email' in calls[cert]
    assert 'gpgcheck=1' in a.path('/etc/yum.repos.d/mykos-bootstrap.repo').read_text()
    assert 'ws://127.0.0.1:8000/' in a.path('/etc/httpd/mykos-proxy.inc').read_text()
    assert a.state['complete']


def test_shared_certificate_retains_renewal(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(b.shutil, 'which', lambda name: None)
    a.managed('/etc/systemd/system/mykos-certificate-renew.timer', 'timer')
    foreign = a.path('/etc/nginx/site.conf')
    foreign.parent.mkdir(parents=True)
    foreign.write_text('/etc/letsencrypt/live/example.com/fullchain.pem')
    a.state['certificate_created'] = True; a.save()
    a.cleanup()
    assert a.path('/etc/systemd/system/mykos-certificate-renew.timer').exists()
    assert not any(c[0] == 'certbot' or 'disable' in c for c in calls)


def test_key_mismatch_rejected(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'wrong key'
    monkeypatch.setattr(b.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match='pinned trust'):
        a.key('mykos', 'https://example.com/key')
    assert not a.path('/etc/pki/rpm-gpg/RPM-GPG-KEY-MYKOS').exists()


def test_cleanup_config_failure_restores_files(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(b.shutil, 'which', lambda name: None)
    name = '/etc/httpd/mykos-proxy.inc'
    a.managed(name, 'original')
    def fail(args, check=True):
        if args == ['httpd', '-t']: raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout='')
    a.run = fail
    with pytest.raises(subprocess.CalledProcessError): a.cleanup()
    assert a.path(name).read_text() == 'original'
    assert name in a.state['files']


def test_installed_rpm_does_not_reinstall_or_add_repo(tmp_path, monkeypatch):
    a, calls = app(tmp_path)
    monkeypatch.setattr(a, 'release', lambda: '9')
    for name in ('prerequisites', 'firewall', 'https', 'renewal', 'proxy'):
        monkeypatch.setattr(a, name, lambda *args: None)
    monkeypatch.setattr(a, 'repository', lambda el: pytest.fail('RPM setup must not add a second repository'))
    monkeypatch.setattr(a, 'tls_ready', lambda: True)
    a.install(installed_rpm=True)
    assert not any(c[0] == 'dnf' for c in calls)
    assert any(c[0] == '/usr/sbin/mykos-setup' for c in calls)


def test_rpm_queues_setup_outside_transaction():
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'packaging/rpm/mykos.spec').read_text()
    posttrans = spec.split('%posttrans\n', 1)[1].split('%preun', 1)[0]
    assert 'systemctl enable --now mykos-firstboot.service' in posttrans
    assert 'MYKOS is running:' in posttrans
    assert '--configure-https' not in posttrans
    assert '! -f /var/lib/mykos-installer/installed.json' in posttrans
    assert 'dnf ' not in posttrans
    unit = (root / 'services/systemd/mykos-firstboot.service').read_text()
    assert 'ConditionPathExists=!/var/lib/mykos-installer/installed.json' in unit
    assert 'mykos-bootstrap --installed-rpm' in unit
    preun = spec.split('%preun\n', 1)[1].split('%postun', 1)[0]
    assert preun.index('disable --now mykos-firstboot.service') < preun.index('mykos-bootstrap --uninstall')


@pytest.mark.parametrize('el',['8','9','10'])
def test_repository_uses_renamed_signed_repository(tmp_path,monkeypatch,el):
    a,calls=app(tmp_path)
    keys=[]
    monkeypatch.setattr(a,'key',lambda name,url:keys.append((name,url)) or '/etc/pki/rpm-gpg/RPM-GPG-KEY-MYKOS')
    a.repository(el)
    origin='https://mykol.com/mykos-1.5-for-x86_64-el'+el
    assert keys==[('mykos',origin+'/RPM-GPG-KEY-MYKOS')]
    text=a.path('/etc/yum.repos.d/mykos-bootstrap.repo').read_text()
    assert 'baseurl='+origin+'\n' in text
    assert 'gpgcheck=1\n' in text and 'enabled=1\n' in text


def test_ip_setup_never_calls_certbot_or_domain_setup_and_reports_reachable_url(tmp_path,monkeypatch,capsys):
    calls=[]
    def run(args,check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args,0,stdout='Disabled' if args==['getenforce'] else '')
    a=b.Bootstrap('192.168.22.10',tmp_path,run,ip_only=True)
    a.path('/etc/os-release').parent.mkdir(parents=True);a.path('/etc/os-release').write_text('ID=rhel\nVERSION_ID="9.8"\n')
    monkeypatch.setattr(b.shutil,'which',lambda name:None)
    a.install_ip(installed_rpm=True)
    assert not any(c[0] in ('certbot','dnf') for c in calls)
    site=a.path('/etc/httpd/conf.d/00-mykos-ip.conf').read_text()
    assert 'ServerName 192.168.22.10' in site and 'X-Forwarded-Proto "http"' in site
    assert not a.path('/etc/httpd/conf.d/00-mykos-https.conf').exists()
    assert calls.index(['systemctl','is-active','--quiet','mykos-web.service'])<calls.index(['curl','--fail','--silent','--show-error','--max-time','5','http://192.168.22.10/mykos/'])
    assert 'MYKOS is running: http://192.168.22.10/mykos/' in capsys.readouterr().out


def test_failed_app_start_never_prints_ready_url(tmp_path,monkeypatch,capsys):
    a=b.Bootstrap('192.168.22.10',tmp_path,lambda args,check=True:subprocess.CompletedProcess(args,1,stdout=''),ip_only=True)
    monkeypatch.setattr(b.time,'sleep',lambda seconds:None)
    with pytest.raises(ValueError,match='did not become reachable'):a.ready('http://192.168.22.10')
    assert 'MYKOS is running:' not in capsys.readouterr().out


@pytest.mark.parametrize('version',['8.10','9.8','10.1'])
def test_supported_release_versions(tmp_path,version):
    a,calls=app(tmp_path);p=a.path('/etc/os-release');p.parent.mkdir(parents=True);p.write_text('ID=rhel\nVERSION_ID="'+version+'"\n')
    assert a.release()==version.split('.')[0]


def test_standard_os_release_symlink_is_read_without_allowing_managed_symlink_writes(tmp_path):
    a,calls=app(tmp_path)
    target=tmp_path/'usr/lib/os-release';target.parent.mkdir(parents=True);target.write_text('ID=rhel\nVERSION_ID="9.8"\n')
    path=tmp_path/'etc/os-release';path.parent.mkdir();path.symlink_to('../usr/lib/os-release')
    assert a.release()=='9'
    with pytest.raises(ValueError,match='symlink'):a.path('/etc/os-release')


def test_fresh_apache_configuration_does_not_reload_stopped_service(tmp_path):
    calls=[]
    def run(args,check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args,3 if args==['systemctl','is-active','--quiet','httpd'] else 0,stdout='')
    a=b.Bootstrap('192.168.22.10',tmp_path,run,ip_only=True)
    a.apache_write('/etc/httpd/conf.d/00-mykos-ip.conf','ServerName 192.168.22.10\n')
    assert ['httpd','-t'] in calls
    assert ['systemctl','reload','httpd'] not in calls
