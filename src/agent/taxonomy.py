"""Sector taxonomy for company enrichment.

Primary sector must be one of SECTORS. Adjacent sectors must also be from
SECTORS. sector_tags may include any of these plus free-form sub-tags.

ADJACENCY is a hint map for the downstream universe builder; the enrichment
agent may produce different adjacencies if grounded evidence supports it.
"""

SECTORS: list[str] = [
    "Banking & Financial Services",
    "Insurance",
    "Capital Markets & Asset Management",
    "Real Estate Development",
    "Construction & Engineering",
    "Oil & Gas - Upstream",
    "Oil & Gas - Downstream / Petrochemicals",
    "Power & Utilities",
    "Telecommunications",
    "Technology & Software",
    "Retail & Consumer Goods",
    "Hospitality, Travel & Tourism",
    "Healthcare & Pharmaceuticals",
    "Logistics, Shipping & Ports",
    "Aviation & Aerospace",
    "Manufacturing & Industrial",
    "Media, Entertainment & Advertising",
    "Education & Training",
    "Professional Services",
    "Conglomerates / Family Groups / Holdings",
]

ADJACENCY: dict[str, list[str]] = {
    "Banking & Financial Services": [
        "Capital Markets & Asset Management",
        "Insurance",
        "Technology & Software",
    ],
    "Insurance": [
        "Banking & Financial Services",
        "Healthcare & Pharmaceuticals",
        "Capital Markets & Asset Management",
    ],
    "Capital Markets & Asset Management": [
        "Banking & Financial Services",
        "Insurance",
        "Professional Services",
    ],
    "Real Estate Development": [
        "Construction & Engineering",
        "Hospitality, Travel & Tourism",
        "Conglomerates / Family Groups / Holdings",
    ],
    "Construction & Engineering": [
        "Real Estate Development",
        "Oil & Gas - Downstream / Petrochemicals",
        "Manufacturing & Industrial",
    ],
    "Oil & Gas - Upstream": [
        "Oil & Gas - Downstream / Petrochemicals",
        "Power & Utilities",
        "Manufacturing & Industrial",
    ],
    "Oil & Gas - Downstream / Petrochemicals": [
        "Oil & Gas - Upstream",
        "Manufacturing & Industrial",
        "Logistics, Shipping & Ports",
    ],
    "Power & Utilities": [
        "Oil & Gas - Upstream",
        "Construction & Engineering",
        "Manufacturing & Industrial",
    ],
    "Telecommunications": [
        "Technology & Software",
        "Media, Entertainment & Advertising",
        "Professional Services",
    ],
    "Technology & Software": [
        "Telecommunications",
        "Banking & Financial Services",
        "Media, Entertainment & Advertising",
    ],
    "Retail & Consumer Goods": [
        "Logistics, Shipping & Ports",
        "Hospitality, Travel & Tourism",
        "Manufacturing & Industrial",
    ],
    "Hospitality, Travel & Tourism": [
        "Aviation & Aerospace",
        "Retail & Consumer Goods",
        "Real Estate Development",
    ],
    "Healthcare & Pharmaceuticals": [
        "Insurance",
        "Manufacturing & Industrial",
        "Education & Training",
    ],
    "Logistics, Shipping & Ports": [
        "Retail & Consumer Goods",
        "Aviation & Aerospace",
        "Manufacturing & Industrial",
    ],
    "Aviation & Aerospace": [
        "Logistics, Shipping & Ports",
        "Hospitality, Travel & Tourism",
        "Manufacturing & Industrial",
    ],
    "Manufacturing & Industrial": [
        "Construction & Engineering",
        "Logistics, Shipping & Ports",
        "Retail & Consumer Goods",
    ],
    "Media, Entertainment & Advertising": [
        "Technology & Software",
        "Telecommunications",
        "Professional Services",
    ],
    "Education & Training": [
        "Professional Services",
        "Healthcare & Pharmaceuticals",
        "Technology & Software",
    ],
    "Professional Services": [
        "Banking & Financial Services",
        "Capital Markets & Asset Management",
        "Conglomerates / Family Groups / Holdings",
    ],
    "Conglomerates / Family Groups / Holdings": [
        "Real Estate Development",
        "Retail & Consumer Goods",
        "Hospitality, Travel & Tourism",
    ],
}

EMPLOYEE_BANDS: list[str] = [
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1k",
    "1k-5k",
    "5k-10k",
    "10k+",
]

REVENUE_BANDS: list[str] = [
    "<$10M",
    "$10-50M",
    "$50-250M",
    "$250M-1B",
    "$1-10B",
    ">$10B",
]
