from agent.taxonomy import EMPLOYEE_BANDS, REVENUE_BANDS, SECTORS


def test_sectors_unique_and_nonempty():
    assert len(SECTORS) == len(set(SECTORS))
    assert len(SECTORS) >= 15
    assert all(s.strip() == s and s for s in SECTORS)


def test_bands_ordered_and_unique():
    assert len(EMPLOYEE_BANDS) == len(set(EMPLOYEE_BANDS))
    assert len(REVENUE_BANDS) == len(set(REVENUE_BANDS))
    assert "10k+" in EMPLOYEE_BANDS
    assert ">$10B" in REVENUE_BANDS
