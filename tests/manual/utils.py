from pathlib import Path
import hashlib
import string
import random

ZENODO_API_URL = "https://sandbox.zenodo.org/api"

def _load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"')
    
    return env

def get_test_dir(anchor: Path | None = None) -> Path:
    """Walk up from anchor (default: this file) until a directory named 'tests' is found."""
    current = (anchor or Path(__file__)).resolve().parent
    while current != current.parent:
        if current.name == "tests":
            return current
        current = current.parent
    raise FileNotFoundError("Could not find 'tests' directory")

def get_random_string(length=6):
    return "".join(random.choices(string.ascii_letters, k=length))