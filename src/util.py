import os


def resolve_path(p: str | None, base_dir: str | None = None) -> str:
    """Resolve a path relative to base_dir if not absolute."""
    if not p or os.path.isabs(p):
        return p or ""
    if base_dir:
        return os.path.normpath(os.path.join(base_dir, p))
    return p
