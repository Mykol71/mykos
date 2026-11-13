"""Documentation must follow the implemented catalog without launching builders."""
import ast,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def assignment(path,name):
 tree=ast.parse((ROOT/path).read_text())
 return next(x.value for x in tree.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in x.targets))

def catalogs():
 native=set(ast.literal_eval(assignment('services/helpers/mykos_runtime.py','NATIVE_OS_NAMES')))
 vm=set(eval(compile(ast.Expression(assignment('services/helpers/mykos_vm.py','VERSIONS')),'catalog','eval'),{'__builtins__':{}}))
 return native,vm

def native_rows(path):
 text=(ROOT/path).read_text();table=text.split('| Native guest/image type |',1)[1].split('\n\n',1)[0]
 return set(re.findall(r'`([a-z0-9.]+)`',table))

def test_both_native_reference_tables_cover_every_implemented_choice():
 native,_=catalogs()
 assert native_rows('README.md')==native
 assert native_rows('docs/IMAGE_BUILDS.md')==native

def test_index_catalog_covers_native_and_vm_choices_and_accurate_totals():
 native,vm=catalogs();text=(ROOT/'docs/README.md').read_text();section=text.split('## Image catalog at a glance',1)[1].split('\n## ',1)[0]
 rows='\n'.join(x for x in section.splitlines() if x.startswith('|'))
 assert set(re.findall(r'`([a-z0-9.]+)`',rows))==native|vm
 for path in ['README.md','docs/README.md']:
  assert f'**{len(native)+len(vm)} image selections: {len(native)} native Linux containers and {len(vm)} VM-backed guests.**' in (ROOT/path).read_text()

def test_vm_build_reference_covers_all_choices_including_manual_android():
 _,vm=catalogs();text=(ROOT/'docs/IMAGE_BUILDS.md').read_text();part=text.split('#### All supported VM types',1)[1].split('### Android-x86 legacy installer',1)[0]
 assert set(re.findall(r'/pos/vm (?:build|create) ([a-z0-9.]+)',part))==vm
 assert 'two Finish steps' in part

def test_native_bulk_scope_matches_menu_and_remains_explicit():
 text=(ROOT/'pos/pos').read_text();body=text.split('build-all-images() {',1)[1].split('\n}',1)[0]
 choices=re.search(r'local images=\(([^)]+)\)',body)[1].split()
 assert len(choices)==16
 assert 'rh7' in choices and 'rh6' not in choices and 'centos6' not in choices
 guide=(ROOT/'docs/IMAGE_BUILDS.md').read_text()
 assert '**16 EL images**' in guide and 'existing `rh7`' in guide

def test_menu_route_and_native_cli_are_documented_separately_from_instance_start():
 guide=(ROOT/'docs/IMAGE_BUILDS.md').read_text()
 assert '2. Containers' in guide and 'c. Build image' in guide
 assert 'sudo bash /usr/local/share/mykos/pos/pos --build-image centos6' in guide
 assert 'does not build a missing image' in guide
 assert 'older native builds are not reconstructed' in guide

def test_api_history_interval_matches_runtime():
 node=assignment('services/web/host_history.py','INTERVAL');interval=ast.literal_eval(node)
 assert f'`sample_interval_seconds: {interval}`' in (ROOT/'docs/API.md').read_text()

def test_reference_coverage_and_legacy_sync_guidance():
 text=(ROOT/'docs/REFERENCES.md').read_text()
 for name in ['Scientific Linux','Springdale','Debian','Arch Linux','SUSE BCI','Amazon Linux','Alpine Linux','Android-x86']:assert name in text
 scripts=(ROOT/'docs/SCRIPTS.md').read_text();assert 'RH6 is rejected' not in scripts
 assert 'Explicit majors6–10' in scripts


def test_heading_entities_use_canonical_ids_and_preserve_old_viewer_bookmarks():
 html=(ROOT/'docs/rendered/UI_GUIDE.html').read_text()
 assert 'id="jobs--recovery"' in html
 assert 'id="jobs-amp-recovery"' in html
 assert '#jobs--recovery' in html
