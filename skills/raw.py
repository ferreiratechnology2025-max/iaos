"""RawSkillSource: an upstream Skill file, byte-pinned and unmodified, offered to cognition only.

The markdown is the Skill. This module does not parse, adapt, summarize, sanitize or translate it. It only
  * verifies that the text is exactly the pinned git blob (CRLF normalized to LF, as git does with autocrlf),
  * hands the unchanged text to a caller that builds a model prompt.
It defines no tool, capability, authorization or execution object and imports nothing from governance/runtime/assurance/evidence.
Whatever the markdown says ("run this command", "engineers must have authority ..."), it stays text in a prompt.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path


class RawSkillError(ValueError):
    pass


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass(frozen=True)
class RawSkillSource:
    repo: str
    revision: str
    path: str
    blob: str
    license: str
    text: str

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_raw_skill(file: Path, repo: str, revision: str, path: str, blob: str, license: str) -> RawSkillSource:
    data = Path(file).read_bytes().replace(b"\r\n", b"\n")
    if git_blob_sha1(data) != blob:
        raise RawSkillError("raw skill content does not match the pinned blob")
    return RawSkillSource(repo=repo, revision=revision, path=path, blob=blob, license=license, text=data.decode("utf-8"))


def raw_skill_context(raw: RawSkillSource) -> str:
    """The Skill Context for this source is the original markdown, unchanged."""
    if not isinstance(raw, RawSkillSource):
        raise TypeError("raw_skill_context needs a RawSkillSource")
    return raw.text
