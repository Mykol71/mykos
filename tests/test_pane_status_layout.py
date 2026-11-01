"""Preserve live status nodes while placing them beside all pane labels."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Dashboard(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.stack = []
        self.ids = {}
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        node = {'tag': tag, 'attrs': attrs, 'parents': list(self.stack)}
        if attrs.get('id'):
            assert attrs['id'] not in self.ids
            self.ids[attrs['id']] = node
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr', 'path'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                self.stack = self.stack[:index]
                break


def test_all_live_pane_summaries_belong_to_their_heading():
    html = (ROOT / 'services/web/index.html').read_text()
    document = Dashboard(html)
    prefixes = ['', 'rocky-', 'alma-', 'oracle-', 'centos-', 'fedora-', 'ubuntu-', 'mssql-', 'windows-', 'windows-server-', 'macos-']
    mapping = {prefix + 'mykos-containers-status': prefix + 'mykos-containers-pane' for prefix in prefixes}
    mapping.update({'mykos-' + name + '-status': 'mykos-' + name + '-title' for name in ['snapshots', 'backups', 'repos']})
    for status, heading in mapping.items():
        node = document.ids[status]
        assert node['tag'] == 'span'
        assert node['attrs']['role'] == 'status'
        assert node['parents'][-1]['attrs']['id'] == heading
        assert 'mykos-pane-status' in node['attrs']['class'].split()
        assert not any('panel' in p['attrs'].get('class', '').split() for p in node['parents'])
    assert len(mapping) == 14
    assert '.mykos-pane-status:not(:empty)::before { content: " · "; }' in html


def test_count_and_refresh_targets_remain_inside_the_live_status_nodes():
    document = Dashboard((ROOT / 'services/web/index.html').read_text())
    for prefix in ['', 'rocky-', 'alma-', 'oracle-', 'centos-', 'fedora-', 'ubuntu-', 'mssql-', 'windows-', 'windows-server-', 'macos-']:
        count = document.ids[prefix + 'mykos-container-counts']
        assert count['parents'][-1]['attrs']['id'] == prefix + 'mykos-containers-status'
