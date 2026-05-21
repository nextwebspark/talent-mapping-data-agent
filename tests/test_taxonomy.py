from agent.taxonomy import ADJACENCY, EMPLOYEE_BANDS, REVENUE_BANDS, SECTORS


def test_sectors_unique_and_nonempty():
    assert len(SECTORS) == len(set(SECTORS))
    assert len(SECTORS) >= 15
    assert all(s.strip() == s and s for s in SECTORS)


def test_adjacency_keys_match_sectors():
    assert set(ADJACENCY.keys()) == set(SECTORS), "Every sector must have an adjacency entry"


def test_adjacency_values_are_taxonomy_sectors():
    taxonomy = set(SECTORS)
    for primary, adj in ADJACENCY.items():
        assert all(a in taxonomy for a in adj), f"{primary}: {adj} contains non-taxonomy entries"


def test_adjacency_no_self_reference():
    for primary, adj in ADJACENCY.items():
        assert primary not in adj, f"{primary} adjacent to itself"


def test_adjacency_size_reasonable():
    for primary, adj in ADJACENCY.items():
        assert 1 <= len(adj) <= 5, f"{primary} has {len(adj)} adjacents"


def test_bands_ordered_and_unique():
    assert len(EMPLOYEE_BANDS) == len(set(EMPLOYEE_BANDS))
    assert len(REVENUE_BANDS) == len(set(REVENUE_BANDS))
    assert "10k+" in EMPLOYEE_BANDS
    assert ">$10B" in REVENUE_BANDS
