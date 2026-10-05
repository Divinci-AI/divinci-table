"""Which commit this table was built from. scripts/cloud_deploy.sh writes table/BUILD_SHA (gitignored) just
before the container image is built; a laptop checkout has none and reports "dev"."""
from pathlib import Path

_F = Path(__file__).resolve().parent / "BUILD_SHA"


def build_sha() -> str:
    try:
        s = _F.read_text().strip()
        return s if len(s) == 40 and all(c in "0123456789abcdef" for c in s) else "dev"
    except OSError:
        return "dev"
