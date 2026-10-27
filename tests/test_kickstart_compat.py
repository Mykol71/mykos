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


def test_weak_dependency_spelling_changes_only_package_header():
    source='%packages --nocore --exclude-weakdeps\n@core\n%end\n%post\necho --exclude-weakdeps\n%end\n'
    old=for_builder(source,False,'--excludeWeakdeps')
    assert '%packages --nocore --excludeWeakdeps\n' in old
    assert '%post\necho --exclude-weakdeps\n%end' in old
    assert for_builder(old,True,'--exclude-weakdeps')==source
    assert for_builder(source,True)==source


def test_unsupported_weak_dependencies_are_not_silently_dropped():
    import pytest
    with pytest.raises(ValueError,match='cannot exclude weak dependencies'):
        for_builder('%packages --exclude-weakdeps\n%end\n',False,None)
