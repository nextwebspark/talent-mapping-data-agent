"""Tests for the controlled sub-tag vocabulary."""

from __future__ import annotations

import re

from agent.subtags import SUB_TAGS, SUB_TAGS_BY_SECTOR, all_sub_tags
from agent.taxonomy import SECTORS

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def test_sub_tags_keys_match_sectors_exactly():
    assert set(SUB_TAGS_BY_SECTOR.keys()) == set(SECTORS)


def test_every_sector_has_at_least_5_sub_tags():
    for sector, tags in SUB_TAGS_BY_SECTOR.items():
        assert len(tags) >= 5, f"sector {sector!r} has only {len(tags)} sub-tags"


def test_all_sub_tags_are_kebab_case_ascii():
    for tag in all_sub_tags():
        assert KEBAB_RE.match(tag), f"sub-tag not kebab-case ASCII: {tag!r}"


def test_no_duplicate_sub_tags():
    flat = all_sub_tags()
    assert len(flat) == len(set(flat)), "duplicate sub-tags across sectors"


def test_sub_tags_set_matches_flat_list():
    assert SUB_TAGS == set(all_sub_tags())


def test_total_sub_tag_count_in_target_range():
    """Vocab should sit in the 200-400 band - if it drifts, revisit curation."""
    total = len(SUB_TAGS)
    assert 200 <= total <= 400, f"sub-tag count {total} outside curated 200-400 band"
