"""Read user-supplied key material; no keys are distributed with this project."""
import json
import os
from pathlib import Path


def load_key(name):
    path = os.environ.get('PS3_KEYS_FILE')
    if not path:
        raise ValueError('Set PS3_KEYS_FILE to your private keys.json before decrypting')
    value = bytes.fromhex(json.loads(Path(path).read_text())[name])
    if len(value) != 16:
        raise ValueError(f'{name} must contain exactly 16 bytes')
    return value
