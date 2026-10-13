"""Manual Skill library: a small, explicit list of real upstream Skills, each byte-pinned and loaded raw.

No resolver, no ranking, no search: a Skill is selected only by its exact id. Unknown ids fail closed.
Every entry is verified against its pinned git blob when the library is loaded; the markdown is never altered.
Imports nothing from governance/runtime/assurance/evidence and defines no tool/capability/authorization type.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from skills.raw import RawSkillError, RawSkillSource, load_raw_skill

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "library.json"
DEFAULT_SOURCES = HERE / "sources"
REQUIRED = ("id", "name", "source_repo", "source_revision", "source_path", "blob_sha", "license", "file")


class UnknownSkillError(KeyError):
    pass


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    raw: RawSkillSource

    @property
    def source(self) -> str:
        return f"{self.raw.repo}@{self.raw.revision[:12]}:{self.raw.path}"


class SkillLibrary:
    def __init__(self, entries):
        ids = [e.id for e in entries]
        if len(set(ids)) != len(ids):
            raise RawSkillError("duplicate skill id in library")
        self._entries = {e.id: e for e in entries}

    def list_skills(self) -> list:
        return [{"id": e.id, "name": e.name, "source": e.source} for e in self._entries.values()]

    def get(self, skill_id: str) -> SkillEntry:
        if not isinstance(skill_id, str) or skill_id not in self._entries:
            raise UnknownSkillError(f"unknown skill id: {skill_id!r}")
        return self._entries[skill_id]


def load_library(manifest: Path = DEFAULT_MANIFEST, sources: Path = DEFAULT_SOURCES) -> SkillLibrary:
    items = json.loads(Path(manifest).read_text(encoding="utf-8"))["skills"]
    entries = []
    for it in items:
        missing = [k for k in REQUIRED if not it.get(k)]
        if missing:
            raise RawSkillError(f"manifest entry {it.get('id')!r} missing {missing}")
        raw = load_raw_skill(Path(sources) / it["file"], it["source_repo"], it["source_revision"],
                             it["source_path"], it["blob_sha"], it["license"])
        entries.append(SkillEntry(id=it["id"], name=it["name"], raw=raw))
    return SkillLibrary(entries)
