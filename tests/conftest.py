"""Keep release tests independent of live Meraki credentials and APIs."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/web'))
@pytest.fixture(autouse=True)
def no_live_meraki_credentials(monkeypatch,request):
    if request.node.module.__name__ != 'test_meraki_sites':
        import meraki_sites
        monkeypatch.setattr(meraki_sites,'credentials',lambda *a,**k:{})
