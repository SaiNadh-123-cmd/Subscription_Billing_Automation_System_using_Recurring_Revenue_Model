import os
import sys
import importlib.util
from importlib.abc import MetaPathFinder

# Ensure pure-Python fallback for Cython C-extensions blocked by Windows Application Control
class _DisableSqlalchemyPyd(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("sqlalchemy.") and fullname.endswith("_cy"):
            if path:
                for p in path:
                    py_file = os.path.join(p, fullname.split(".")[-1] + ".py")
                    if os.path.exists(py_file):
                        return importlib.util.spec_from_file_location(fullname, py_file)
        return None

if not any(isinstance(finder, _DisableSqlalchemyPyd) for finder in sys.meta_path):
    sys.meta_path.insert(0, _DisableSqlalchemyPyd())
