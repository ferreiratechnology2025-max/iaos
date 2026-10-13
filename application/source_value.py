"""Trusted resolution of ``browser.type_source_value``: (source ordinal, field) -> the verbatim governed value, or a closed failure code."""
from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

from application.source_facts import DeepReadFact, SourceFact
from cognitive.agent_contracts import MAX_TEXT_CHARS
from cognitive.source_value_contract import SOURCE_VALUE_FIELDS

DeepFactExtractor = Callable[[str], Optional[str]]


def resolve_source_value(facts: Sequence[SourceFact], deep_facts: Sequence[DeepReadFact], source: int, field: str,
                         extractor: Optional[DeepFactExtractor] = None, require_admitted: bool = False) -> Tuple[Optional[str], str]:
    """Returns (value, "ok") or (None, code). The value is read only from the admitted source store: never from the model.

    * title/url come from the SourceFact of that ordinal; with ``require_admitted`` (research mode) the source must also own an
      ADMITTED deep read, so a search attempt that never became a valid source cannot supply a value;
    * deep_fact needs an admitted DeepReadFact correlated to that very SourceFact (same search execution) and a trusted extractor."""
    if field not in SOURCE_VALUE_FIELDS:
        return None, "field_unsupported"
    if not isinstance(source, int) or isinstance(source, bool) or not 1 <= source <= len(facts):
        return None, "source_out_of_range"
    fact = facts[source - 1]
    deep = next((d for d in reversed(list(deep_facts)) if d.search_execution_id == fact.source_id), None)
    if (require_admitted or field == "deep_fact") and deep is None:
        return None, "source_not_admitted"
    if field == "title":
        value: Optional[str] = fact.title
    elif field == "url":
        value = fact.url
    else:
        if extractor is None:
            return None, "field_unsupported"
        value = extractor(deep.visible_text_excerpt)
    if not isinstance(value, str) or not value:
        return None, "field_unavailable"
    if len(value) > MAX_TEXT_CHARS:
        return None, "value_too_long"
    return value, "ok"
