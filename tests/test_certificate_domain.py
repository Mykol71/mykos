from pathlib import Path
import sys,ssl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/helpers'))
import mykos_host_monitoring as host


def setup(tmp_path,monkeypatch,names):
    config=tmp_path/'conf';config.mkdir()
    live=tmp_path/'live';certificate=live/'site/fullchain.pem';certificate.parent.mkdir(parents=True);certificate.write_text('public certificate')
    env=tmp_path/'web.env';env.write_text('MYKOS_PUBLIC_ORIGIN=https://app.example.com\n')
    monkeypatch.setattr(host,'APACHE_CONFIG_DIRS',(config,));monkeypatch.setattr(host,'CERTBOT_LIVE',live);monkeypatch.setattr(host,'WEB_ENV',env)
    decoded=[]
    def decode(path):
        assert Path(path)==certificate
        decoded.append(path)
        return {'subjectAltName':[('DNS',name) for name in names]}
    monkeypatch.setattr(ssl._ssl,'_test_decode_cert',decode)
    (config/'ssl.conf').write_text('<VirtualHost *:443>\nServerName app.example.com\nSSLCertificateFile "'+str(certificate)+'"\n</VirtualHost>')
    return config,env,decoded


def test_heading_uses_actual_certified_public_domain(tmp_path,monkeypatch):
    _,_,decoded=setup(tmp_path,monkeypatch,['example.com','app.example.com'])
    assert host.apache_certificate_domain()=='app.example.com'
    assert len(decoded)==1


def test_wildcard_uses_matching_apache_name(tmp_path,monkeypatch):
    _,env,_=setup(tmp_path,monkeypatch,['*.example.com']);env.write_text('MYKOS_PUBLIC_ORIGIN=https://unrelated.test\n')
    assert host.apache_certificate_domain()=='app.example.com'


def test_non_certbot_files_and_keys_are_never_read(tmp_path,monkeypatch):
    config,_,decoded=setup(tmp_path,monkeypatch,['app.example.com'])
    (config/'ssl.conf').write_text('SSLCertificateFile /etc/pki/tls/certs/local.crt\nSSLCertificateFile '+str(tmp_path/'live/site/privkey.pem')+'\n')
    assert host.apache_certificate_domain() is None
    assert not decoded


def test_unmatched_origin_is_not_presented_as_certified(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch,['other.example.com'])
    assert host.apache_certificate_domain()=='other.example.com'
