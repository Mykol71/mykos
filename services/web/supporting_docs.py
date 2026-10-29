"""Allowlisted, pre-rendered operator documentation with a permanent light theme."""
from pathlib import Path
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse,RedirectResponse

GUIDES={
    'USE_CASES.md':'Example use cases',
    'DEVELOPERS_GUIDE.md':'Developer handoff and source guide',
    'KEY_INNOVATIONS.md':'Key innovations',
    'ENGINEERING_LESSONS.md':'Performance by design: engineering milestones',
    'DEPLOYMENT_EFFICIENCY.md':'Deployment efficiency and business value',

    'README.md':'MYKOS documentation',
    'DOCUMENTATION_BUILD.md':'Documentation build and verification',
    'BOOTSTRAP.md':'IP installation and optional domain HTTPS',
    'RPM.md':'RHEL RPM packages',
    'INSTALL_SERVICES.md':'Installation updates and recovery',
    'ARCHITECTURE.md':'Architecture and components',
    'NETWORKING.md':'Native networking and VPNs',
    'DESKTOPS.md':'Windows macOS and Ubuntu desktops',
    'IMAGE_BUILDS.md':'Unattended image builds',
    'BALLOONING.md':'Guest memory ballooning',
    'HOST_SIDE_REVIEW.md':'Host monitoring review',
    'KEY_ISSUES.md':'Operator notes',
    'IDENTITY.md':'Identity management',
    'API.md':'API usage',
    'SCRIPTS.md':'Operator script catalog',
    'VALIDATION.md':'Validation record',
    'MYKOS_Administrator_Guide.md':'Administrator guide reference edition',
}
DOWNLOADS={'MYKOS_Key_Innovations.pdf':'application/pdf','MYKOS_Engineering_Lessons.pdf':'application/pdf','MYKOS_Deployment_Efficiency.pdf':'application/pdf','MYKOS_Administrator_Guide.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
           'openapi.json':'application/json','THIRD_PARTY_ICONS.txt':'text/plain'}
DIAGRAMS={'vpn-host-shared.svg', 'vpn-direct.mmd', 'image-lifecycle.mmd', 'vpn-host-only.svg', 'vpn-direct.svg', 'vpn-native-gateway.mmd', 'architecture.mmd', 'architecture.svg', 'image-lifecycle.svg', 'vpn-native-gateway.svg', 'vpn-host-shared.mmd', 'vpn-host-only.mmd'}
HEADERS={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",'X-Content-Type-Options':'nosniff'}


def router(root):
    root=Path(root);routes=APIRouter()
    def file(relative):
        path=root/relative
        if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise HTTPException(404,'Document not installed')
        return path
    def page(name):
        return FileResponse(file('rendered/'+name[:-3]+'.html'),media_type='text/html',headers=HEADERS)
    @routes.get('/docs',include_in_schema=False)
    def redirect():return RedirectResponse('docs/')
    @routes.get('/docs/',include_in_schema=False,name='supporting_docs_index')
    def index():return page('README.md')
    @routes.get('/docs/diagrams/{name}',include_in_schema=False)
    def diagram(name:str):
        if name not in DIAGRAMS:raise HTTPException(404,'Diagram not found')
        return FileResponse(file('diagrams/'+name),media_type='image/svg+xml' if name.endswith('.svg') else 'text/plain',headers=HEADERS)
    @routes.get('/docs/{name}',include_in_schema=False)
    def document(name:str):
        if name in DOWNLOADS:
            return FileResponse(file(name),media_type=DOWNLOADS[name],filename=name if name.endswith('.docx') else None,headers=HEADERS)
        if name.endswith('.html'):name=name[:-5]+'.md'
        if name not in GUIDES:raise HTTPException(404,'Document not found')
        return page(name)
    return routes
