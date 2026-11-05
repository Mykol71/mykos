"""Creation boundaries for the initial public release; existing state is retained."""
EXPERIMENTAL_NETWORK_CREATION = False
VERIFIED_NETWORK_CREATION = frozenset(('native-site','peer'))
def require_network_creation(kind):
    if kind not in VERIFIED_NETWORK_CREATION | frozenset(('shared','host-shared','host-site','native-full','vm-vpn','macvlan')):
        raise ValueError('Unknown networking scenario')
    if kind not in VERIFIED_NETWORK_CREATION and not EXPERIMENTAL_NETWORK_CREATION:
        raise ValueError('This networking scenario is outside the initial public release; existing configuration is unchanged')
