from __future__ import annotations

from typing import Any


def parse_detail_batch(
    summaries: list[dict[str, Any]],
    config_dict: dict[str, Any],
    start_index: int,
) -> list[tuple[int, dict[str, Any] | None]]:
    """Run listing detail parsing in an isolated process."""
    from parser.ricardo import ParserConfig, RicardoParser, SearchSummary

    config = ParserConfig(**config_dict)
    parser = RicardoParser(config)
    results: list[tuple[int, dict[str, Any] | None]] = []

    try:
        with parser._session() as session:
            for offset, summary_dict in enumerate(summaries):
                global_index = start_index + offset
                summary = SearchSummary(**summary_dict)
                try:
                    if not session.is_alive():
                        results.append((global_index, None))
                        return results
                    item = parser._parse_listing(session, summary)
                    results.append((global_index, item.to_dict() if item else None))
                except Exception:
                    results.append((global_index, None))
                    if not session.is_alive():
                        return results
    except Exception:
        return results

    return results
