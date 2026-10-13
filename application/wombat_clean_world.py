"""CLEAN_WORLD_GATE for the Website Wombat artifact gate.

Wombat keeps the site in the browser profile, so "clean" must be guaranteed by the identity of the profile,
not by looking at page content alone. A gate may only run on a profile volume that
  * is named like a disposable Wombat profile created for this purpose,
  * is the one actually mounted at /profiles in the running container,
  * has never been recorded in the used-profiles ledger (one gate per profile, ever),
and, as a second independent layer, the fresh page must show the "Start a new site" surface with no trace
of the target artifact. Any failure aborts BEFORE the agent exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

PROFILE_VOLUME = re.compile(r"^openbot-profile-wombat-clean-[0-9]{8}-[0-9]{6}$")
START_SURFACE = "Start a new site"


@dataclass(frozen=True)
class CleanWorldReport:
    ok: bool
    facts: Mapping[str, str]


def check_clean_world(*, requested_volume: str, mounted_volume: str | None, used_volumes: Sequence[str],
                      verification_facts: Mapping[str, object], aria: str, holder: str, requested: bool,
                      title: str, subtitle: str) -> CleanWorldReport:
    named = bool(PROFILE_VOLUME.match(requested_volume or ""))
    mounted = bool(mounted_volume) and mounted_volume == requested_volume
    fresh = (requested_volume or "") not in {v.strip() for v in used_volumes}
    title_present = bool(verification_facts.get("TITLE")) or title in aria
    subtitle_present = bool(verification_facts.get("SUBTITLE")) or subtitle in aria
    button_present = bool(verification_facts.get("BUTTON"))
    surface = START_SURFACE in aria
    unique = verification_facts.get("PREVIEW_CANDIDATES") == 1
    clean_profile = named and mounted and fresh
    facts = {
        "PROFILE_VOLUME_NAME_VALID": _yn(named),
        "PROFILE_MOUNTED_IS_REQUESTED": _yn(mounted),
        "PROFILE_REUSED": _yn(not fresh),
        "CLEAN_PROFILE": _yn(clean_profile),
        "TARGET_TITLE_PRESENT": _yn(title_present),
        "TARGET_SUBTITLE_PRESENT": _yn(subtitle_present),
        "TARGET_BUTTON_PRESENT": _yn(button_present),
        "START_NEW_SITE_SURFACE_PRESENT": _yn(surface),
        "PREVIEW_REGION_UNIQUE": _yn(unique),
        "PREVIEW_TARGET_ARTIFACT_PRESENT": _yn(title_present and subtitle_present and button_present),
        "HOLDER": holder,
        "PENDING_TAKEOVER": _yn(requested),
    }
    ok = (clean_profile and not (title_present or subtitle_present or button_present) and surface and unique
          and holder == "bot" and not requested)
    return CleanWorldReport(ok, facts)


def _yn(value: bool) -> str:
    return "YES" if value else "NO"
