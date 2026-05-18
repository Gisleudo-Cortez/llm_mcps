"""Category config and validation constants. Schema is defined via LanceModel in db.py."""

CATEGORY_CONFIG: dict[str, dict] = {
    # (half_life_days, temporal_weight, max_age_days)
    # temporal_weight = (1 - alpha) in the scoring formula
    "live":           {"half_life_days": 0.021,  "temporal_weight": 0.70, "max_age_days": 0.083},  # 30min HL, 2h max
    "news":           {"half_life_days": 1.0,    "temporal_weight": 0.55, "max_age_days": 7.0},
    "web_search":     {"half_life_days": 3.0,    "temporal_weight": 0.45, "max_age_days": 30.0},
    "docs_technical": {"half_life_days": 30.0,   "temporal_weight": 0.35, "max_age_days": 180.0},
    "research":       {"half_life_days": 180.0,  "temporal_weight": 0.25, "max_age_days": 730.0},
    "reference":      {"half_life_days": 1825.0, "temporal_weight": 0.10, "max_age_days": 3650.0},
    "static":         {"half_life_days": 36500.0,"temporal_weight": 0.01, "max_age_days": None},
}

# Similarity thresholds — higher = stricter matching per category
SIMILARITY_THRESHOLDS: dict[str, float] = {
    "live":           0.98,
    "news":           0.92,
    "web_search":     0.90,
    "docs_technical": 0.93,
    "research":       0.91,
    "reference":      0.90,
    "static":         0.88,
}

VALID_CATEGORIES = set(CATEGORY_CONFIG.keys())
VALID_DOCUMENT_KINDS = {"STATIC", "VERSIONED", "EVENT"}
VALID_VALIDITY_STATES = {"VALID", "TEMPORAL", "EXPIRED"}

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2
