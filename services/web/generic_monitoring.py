"""Exclude retired application-specific fields, including older collector caches."""
def details(payload):
    return {**payload, 'details': {key: ({k: v for k, v in value.items() if k != 'rti_version'} if isinstance(value, dict) else value)
            for key, value in payload.get('details', {}).items()}}


def extended(payload):
    return {**payload, 'nodes': {key: ({k: v for k, v in value.items() if k != 'rti'} if isinstance(value, dict) else value)
            for key, value in payload.get('nodes', {}).items()}}
