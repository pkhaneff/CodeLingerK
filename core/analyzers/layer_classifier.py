"""
Layer Classifier - Classify code into architectural layers
"""

import re
from typing import Optional

from core.logging_config import get_logger

logger = get_logger(__name__)


class LayerClassifier:
    """
    Classify files/symbols into architectural layers
    Based on file path patterns
    """

    # Layer patterns (regex)
    PATTERNS = {
        "Controller": [
            r".*/(controllers?|routes?)/.*",
            r".*/api/.*",
        ],
        "Service": [
            r".*/services?/.*",
            r".*/business/.*",
            r".*/use_?cases?/.*",
        ],
        "Repository": [
            r".*/repositories?/.*",
            r".*/data/.*",
            r".*/dao/.*",
        ],
        "Model": [
            r".*/models?/.*",
            r".*/entities?/.*",
            r".*/schemas?/.*",
        ],
        "Util": [
            r".*/utils?/.*",
            r".*/helpers?/.*",
            r".*/common/.*",
        ],
        "Core": [
            r".*/core/.*",
            r".*/domain/.*",
        ],
        "Parser": [
            r".*/parsers?/.*",
            r".*/analyzer/.*",
        ],
        "Graph": [
            r".*/graph/.*",
            r".*/neo4j/.*",
        ],
    }

    def classify(self, file_path: str) -> Optional[str]:
        """
        Classify a file into an architectural layer

        Args:
            file_path: Path to the file

        Returns:
            Layer name or None if unclassified
        """
        # Normalize path
        normalized_path = file_path.replace('\\', '/')

        # Check against patterns
        for layer, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.match(pattern, normalized_path, re.IGNORECASE):
                    logger.debug(f"Classified {file_path} as {layer}")
                    return layer

        logger.debug(f"Could not classify {file_path}, defaulting to Unknown")
        return "Unknown"

    def add_pattern(self, layer: str, pattern: str):
        """
        Add a new pattern for a layer

        Args:
            layer: Layer name
            pattern: Regex pattern
        """
        if layer not in self.PATTERNS:
            self.PATTERNS[layer] = []

        self.PATTERNS[layer].append(pattern)
        logger.info(f"Added pattern '{pattern}' to layer '{layer}'")
