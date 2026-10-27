"""Use modern maintained kickstarts without breaking old RHEL 8 parsers."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pos'))
from prepare_ks import modernize,for_builder


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
