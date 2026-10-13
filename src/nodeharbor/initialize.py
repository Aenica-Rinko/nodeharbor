"""Create empty, local-only configuration without replacing existing user files."""
import os
from copy import deepcopy

import yaml

from .config import DEFAULTS
from .storage import app_lock


def initialize(paths):
    defaults = {
        "settings.yaml": deepcopy(DEFAULTS),
    }
    paths.ensure()
    if all((paths.config / name).exists() for name in defaults): return []
    created = []
    with app_lock(paths):
        for name, data in defaults.items():
            content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            try:
                with (paths.config / name).open("x", encoding="utf-8", newline="\n") as file:
                    file.write(content); file.flush(); os.fsync(file.fileno())
                created.append(name)
            except FileExistsError:
                pass
    return created
