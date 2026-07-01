from pathlib import Path


def get_backend_root() -> Path:
    """Return stable backend root (src/backend), independent of process cwd."""
    return Path(__file__).resolve().parents[2]


def resolve_storage_path(raw_path: str, *, default_relative: str) -> Path:
    """Resolve storage paths relative to backend root when not absolute."""
    candidate = Path(str(raw_path or default_relative).strip())
    if candidate.is_absolute():
        return candidate
    return get_backend_root() / candidate
