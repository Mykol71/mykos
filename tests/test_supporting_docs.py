from pathlib import Path
import sys
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/web'))
from supporting_docs import router,GUIDES


@pytest.fixture
def client():
    app=FastAPI(docs_url=None);app.include_router(router(ROOT/'docs'))
    return TestClient(app)


def test_document_index_and_guides_render_light_pages(client):
    index=client.get('/docs')
    assert index.status_code==200 and '<table>' in index.text
    for name in GUIDES:
        for url in ['/docs/'+name,'/docs/'+name[:-3]+'.html']:
            response=client.get(url)
            assert response.status_code==200
            assert 'color-scheme:only light' in response.text
            assert 'name="color-scheme" content="only light"' in response.text
            assert "default-src 'none'" in response.headers['content-security-policy']
    assert '--configure-https' in client.get('/docs/BOOTSTRAP.md').text


def test_diagrams_available_and_legacy_business_download_excluded(client):
    response=client.get('/docs/diagrams/architecture.svg')
    assert response.status_code==200 and response.headers['content-type'].startswith('image/svg+xml')
    assert client.get('/docs/diagrams/architecture.mmd').text.startswith('flowchart TD')
    assert client.get('/docs/MYKOS_Administrator_Guide.docx').status_code==404


@pytest.mark.parametrize('url',['/docs/app.py','/docs/diagrams/private.svg','/docs/auth.db','/docs/rendered/README.html'])
def test_only_named_docs_and_assets_are_public(client,url):
    assert client.get(url).status_code==404


def test_document_symlink_cannot_read_outside_root(tmp_path):
    root=tmp_path/'docs';(root/'rendered').mkdir(parents=True)
    outside=tmp_path/'private';outside.write_text('not a document')
    (root/'rendered/README.html').symlink_to(outside)
    app=FastAPI(docs_url=None);app.include_router(router(root))
    assert TestClient(app).get('/docs').status_code==404


def test_document_index_redirect_does_not_expose_backend_host_or_scheme(client):
    response=client.get('/docs',follow_redirects=False,headers={'host':'internal-backend.example:8000'})
    assert response.status_code==307 and response.headers['location']=='docs/'


def test_maintained_guides_are_public_and_legacy_downloads_excluded(client):
    for guide in ['KEY_INNOVATIONS','ENGINEERING_LESSONS','DEPLOYMENT_EFFICIENCY']:
        assert client.get('/docs/'+guide+'.html').status_code==200
        assert guide+'.html' in client.get('/docs/').text
    for name in ['MYKOS_Key_Innovations.pdf','MYKOS_Engineering_Lessons.pdf','MYKOS_Deployment_Efficiency.pdf']:
        assert client.get('/docs/'+name).status_code==404
    for name in ['LICENSING.md','LICENSING.html']:
        response=client.get('/docs/'+name)
        assert response.status_code==200 and 'MYKOL-DOT-COM' in response.text


def test_only_named_example_screenshots_are_public(client):
    for name in ('example-dashboard.jpg','example-snapshot-actions.jpg','example-snapshot-preview.jpg'):
        response=client.get('/docs/screenshots/'+name)
        assert response.status_code==200 and response.headers['content-type'].startswith('image/jpeg')
        assert response.content==(ROOT/'docs/screenshots'/name).read_bytes()
    assert client.get('/docs/screenshots/private.jpg').status_code==404
    assert client.get('/docs/screenshots/auth.db').status_code==404


def test_credits_logos_are_local_allowlisted_assets(client):
    from supporting_docs import BRAND_LOGOS
    page=client.get('/docs/CREDITS.html')
    assert page.status_code==200
    for name in ('Dell','Teleflora','Codex','ChatGPT'):
        assert name in page.text
    for name in BRAND_LOGOS:
        if not name.endswith('-pane.png'):
            assert 'brands/'+name in page.text
        response=client.get('/docs/brands/'+name)
        assert response.status_code==200
        assert response.headers['content-type'].startswith('image/')
        assert "default-src 'none'" in response.headers['content-security-policy']
    assert client.get('/docs/brands/private.svg').status_code==404
    assert client.get('/docs/BRAND_SOURCES.txt').status_code==200


def test_full_license_and_third_party_notices_are_exact_public_downloads(client):
    for name in ('LICENSE.txt','THIRD_PARTY_NOTICES.txt'):
        response=client.get('/docs/'+name)
        assert response.status_code==200
        assert response.content==(ROOT/name).read_bytes()==(ROOT/'docs'/name).read_bytes()
        assert response.headers['content-type'].startswith('text/plain')
    assert 'LICENSING.html' in client.get('/docs/').text
    assert client.get('/docs/LICENSING-REVIEW.txt').status_code==404


def test_dashboard_vendor_artwork_is_local_and_allowlisted(client):
    page=(ROOT/'services/web/index.html').read_text()
    for name in ('centos-pane.png','fedora-pane.png','oracle-linux-pane.png'):
        if name!='centos-pane.png':
            assert '/mykos/docs/brands/'+name in page
        response=client.get('/docs/brands/'+name)
        assert response.status_code==200
        assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
    import re
    marks=re.findall(r'<svg[^>]*data-brand-icon="windows"[^>]*>.*?</svg>',page)
    assert len(marks)==2
    for mark in marks:
        for color in ('#f25022','#7fba00','#00a4ef','#ffb900'):
            assert 'fill="'+color+'"' in mark
