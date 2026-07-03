"""
fs_utils.py — filesystem path helpers shared across the passes, the pipeline,
and ingestion.

The pipeline writes deeply nested output paths (PROCESS_ROOT / product /
sub_folder / language / Pass_N / <product_subfolder_language>.md).  The squashed
filename repeats the path components, so these routinely exceed Windows' legacy
260-character MAX_PATH limit.  Any code that reads, checks, copies, or lists
those paths must use to_long_path() or it will silently fail to find files that
are really there.
"""

import os
import sys


def to_long_path(path: str) -> str:
    r"""
    On Windows, prefix an absolute path with the \\?\ marker so it can exceed the
    legacy 260-character MAX_PATH limit.  No-op on non-Windows platforms and for
    relative paths, and safe to call more than once (never double-prefixes).
    """
    if sys.platform == "win32" and path and os.path.isabs(path):
        path = os.path.normpath(path)
        if not path.startswith("\\\\?\\"):
            path = "\\\\?\\" + path
    return path
