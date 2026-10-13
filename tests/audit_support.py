"""Host-scope probe for tests. Claim and evidence PRODUCERS are production code
(claims/producer.py, evidence/exporter.py, composed by assurance/chain.py)."""
import sys

from evidence.exporter import volume_filesystem


def ON_WINDOWS_NTFS(env):
    return sys.platform == "win32" and volume_filesystem(env.base) == "NTFS"