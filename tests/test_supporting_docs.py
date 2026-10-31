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


def test_diagrams_and_preserved_word_guide_are_available(client):
    response=client.get('/docs/diagrams/architecture.svg')
    assert response.status_code==200 and response.headers['content-type'].startswith('image/svg+xml')
    assert client.get('/docs/diagrams/architecture.mmd').text.startswith('flowchart TD')
    response=client.get('/docs/MYKOS_Administrator_Guide.docx')
    assert response.status_code==200 and 'attachment' in response.headers['content-disposition']
    assert response.content==(ROOT/'docs/MYKOS_Administrator_Guide.docx').read_bytes()


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


def test_project_guides_and_pdf_downloads_are_public_and_licensing_stays_removed(client):
    for guide,pdf in [('KEY_INNOVATIONS','MYKOS_Key_Innovations.pdf'),
                      ('ENGINEERING_LESSONS','MYKOS_Engineering_Lessons.pdf'),
                      ('DEPLOYMENT_EFFICIENCY','MYKOS_Deployment_Efficiency.pdf')]:
        response=client.get('/docs/'+guide+'.html')
        assert response.status_code==200 and pdf in response.text
        response=client.get('/docs/'+pdf)
        assert response.status_code==200
        assert response.headers['content-type'].startswith('application/pdf')
        assert response.content==(ROOT/'docs'/pdf).read_bytes()
        assert guide+'.html' in client.get('/docs/').text
    for name in ['LICENSING.md','LICENSING.html']:
        assert client.get('/docs/'+name).status_code==404


def test_only_named_example_screenshots_are_public(client):
    for name in ('example-dashboard.jpg','example-snapshot-actions.jpg','example-snapshot-preview.jpg'):
        response=client.get('/docs/screenshots/'+name)
        assert response.status_code==200 and response.headers['content-type'].startswith('image/jpeg')
        assert response.content==(ROOT/'docs/screenshots'/name).read_bytes()
    assert client.get('/docs/screenshots/private.jpg').status_code==404
    assert client.get('/docs/screenshots/auth.db').status_code==404
