"""Exclude retired integration fields, including older collector caches."""
def public_fields(value):
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items()
            if not (isinstance(key, str) and (key.casefold() == 'rti' or key.casefold().startswith('rti_')))}


def details(payload):
    return {**payload, 'details': {key: public_fields(value)
            for key, value in payload.get('details', {}).items()}}


def extended(payload):
    return {**payload, 'nodes': {key: public_fields(value)
            for key, value in payload.get('nodes', {}).items()}}
