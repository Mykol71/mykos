import pytest
from fastapi import HTTPException
import mykos_admin_api as api

@pytest.mark.parametrize('explicit',[None,'http://192.0.2.80/'])
def test_forwarded_dashboard_uses_trusted_download_origin(monkeypatch,explicit):
    listing={'repos':[], 'download_base_url':explicit}
    monkeypatch.setattr(api,'authorize',lambda *a:None)
    monkeypatch.setattr(api,'listing',lambda *a:listing)
    monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN','https://mykos.example/mykos')
    value=api.repos(object())
    assert value['download_base_url']==(explicit or 'https://mykos.example/repos/')
    assert listing['download_base_url']==explicit

@pytest.mark.parametrize('origin',['javascript:x','https://user:secret@example/','https://example/?secret=x'])
def test_invalid_download_origin_fails_closed(monkeypatch,origin):
    monkeypatch.setattr(api,'authorize',lambda *a:None)
    monkeypatch.setattr(api,'listing',lambda *a:{'repos':[]})
    monkeypatch.setenv('MYKOS_PUBLIC_ORIGIN',origin)
    with pytest.raises(HTTPException) as error:api.repos(object())
    assert error.value.status_code==503
