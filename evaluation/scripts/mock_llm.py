from typing import Dict, List

# Predefined mock responses from the AI Model for each sample.
# Designed to test precision/recall, exact matches, partial matches, false positives, and misses.
MOCK_RESPONSES: Dict[str, List[dict]] = {
    "sample_001": [
        # Exact match (security)
        {
            "file": "auth.py",
            "line_start": 4,
            "line_end": 4,
            "category": "security",
            "message": "AI found the hardcoded JWT secret at line 4."
        },
        # False Positive (maintainability - there is no such expected finding)
        {
            "file": "auth.py",
            "line_start": 7,
            "line_end": 8,
            "category": "maintainability",
            "message": "AI recommends using a constant for the token expiration timedelta."
        }
    ],
    "sample_002": [
        # Exact match (logic)
        {
            "file": "user_check.py",
            "line_start": 3,
            "line_end": 3,
            "category": "logic",
            "message": "AI found the bug: 'or superuser' is always truthy."
        }
    ],
    "sample_003": [
        # Partial match (performance - overlaps expected line 13, but reports wider range 10-14)
        {
            "file": "process_users.py",
            "line_start": 10,
            "line_end": 14,
            "category": "performance",
            "message": "AI detected list lookup inside the loop."
        }
    ],
    "sample_004": [
        # Exact match (maintainability)
        {
            "file": "cart.py",
            "line_start": 5,
            "line_end": 5,
            "category": "maintainability",
            "message": "AI identified mutable default list argument."
        }
    ],
    "sample_005": [
        # Miss: AI fails to report any findings for sample_005
    ],
    "sample_006": [
        # Exact match (security)
        {
            "file": "db_query.py",
            "line_start": 5,
            "line_end": 5,
            "category": "security",
            "message": "AI detected raw f-string SQL query execution."
        }
        # Miss: AI misses the ValueError logic bug on line 13
    ]
}
