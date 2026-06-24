"""
LayerService - Classify files into functional layers for intent-based review.

Instead of reviewing file-by-file, we group files by their functional purpose
(API, AUTH, DB, etc.) to enable more meaningful AI review.

Process:
1. Classify each changed file by path patterns
2. Group files into Layer records
3. Map diff hunks to LayerRange records
4. Calculate risk scores per layer
5. Determine review order
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from models.layer import Layer, LayerRange, LayerType
from models.snapshot import Snapshot, SnapshotStatus
from services.context_service import ContextService, FileContext, SnapshotContext

logger = get_logger(__name__)


@dataclass
class LayerPattern:
    """Pattern definition for layer classification."""

    layer_type: LayerType
    patterns: list[str]  # Regex patterns
    risk_base: int = 50  # Base risk score for this layer
    review_order: int = 50  # Default review order


# Layer classification patterns
LAYER_PATTERNS: list[LayerPattern] = [
    LayerPattern(
        layer_type=LayerType.API,
        patterns=[
            r'api/', r'routes/', r'controllers/', r'endpoints/',
            r'handlers/', r'views\.py$', r'views/',
        ],
        risk_base=60,
        review_order=20,  # Review early (public surface)
    ),
    LayerPattern(
        layer_type=LayerType.AUTH,
        patterns=[
            r'auth/', r'security/', r'middleware/auth', r'jwt',
            r'oauth', r'permission', r'rbac', r'acl',
        ],
        risk_base=85,  # High risk - security sensitive
        review_order=10,  # Review first
    ),
    LayerPattern(
        layer_type=LayerType.DB,
        patterns=[
            r'models/', r'repositories/', r'alembic/', r'migrations/',
            r'database/', r'db/', r'orm/', r'schema',
        ],
        risk_base=70,
        review_order=15,  # Review early (data integrity)
    ),
    LayerPattern(
        layer_type=LayerType.SERVICE,
        patterns=[
            r'services/', r'business/', r'domain/', r'core/',
            r'usecases/', r'interactors/',
        ],
        risk_base=65,
        review_order=25,
    ),
    LayerPattern(
        layer_type=LayerType.MODEL,
        patterns=[
            r'schemas/', r'entities/', r'dtos/', r'types/',
            r'pydantic', r'dataclass',
        ],
        risk_base=50,
        review_order=30,
    ),
    LayerPattern(
        layer_type=LayerType.CONFIG,
        patterns=[
            r'config/', r'settings/', r'infra/config', r'\.env',
            r'docker-compose', r'Dockerfile', r'\.yaml$', r'\.yml$',
        ],
        risk_base=75,  # Config changes can be risky
        review_order=5,  # Review very early
    ),
    LayerPattern(
        layer_type=LayerType.TEST,
        patterns=[
            r'tests/', r'test_', r'_test\.py$', r'\.test\.',
            r'spec/', r'__tests__/', r'conftest',
        ],
        risk_base=20,  # Low risk
        review_order=80,  # Review later
    ),
    LayerPattern(
        layer_type=LayerType.UI,
        patterns=[
            r'components/', r'pages/', r'templates/', r'static/',
            r'frontend/', r'web/', r'\.tsx$', r'\.jsx$', r'\.vue$',
        ],
        risk_base=40,
        review_order=60,
    ),
    LayerPattern(
        layer_type=LayerType.INFRA,
        patterns=[
            r'infra/', r'deploy/', r'\.github/', r'ci/', r'cd/',
            r'terraform/', r'k8s/', r'kubernetes/', r'helm/',
        ],
        risk_base=70,
        review_order=35,
    ),
    LayerPattern(
        layer_type=LayerType.UTIL,
        patterns=[
            r'utils/', r'helpers/', r'lib/', r'common/', r'shared/',
            r'tools/', r'scripts/',
        ],
        risk_base=40,
        review_order=70,
    ),
]


@dataclass
class ClassifiedFile:
    """A file with its layer classification."""

    file_path: str
    layer_type: LayerType
    status: str  # 'added', 'modified', 'deleted', 'renamed'
    additions: int = 0
    deletions: int = 0
    hunks: list[dict[str, Any]] = field(default_factory=list)


class LayerService:
    """
    Classify files into functional layers for intent-based review.

    Responsibilities:
    - Classify files by path patterns
    - Group files into Layer records
    - Map diff hunks to LayerRange records
    - Calculate risk scores
    - Determine review order
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize layer service.

        Args:
            db: Database session
        """
        self.db = db
        self._compiled_patterns: dict[LayerType, list[re.Pattern]] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for performance."""
        for layer_pattern in LAYER_PATTERNS:
            self._compiled_patterns[layer_pattern.layer_type] = [
                re.compile(pattern, re.IGNORECASE)
                for pattern in layer_pattern.patterns
            ]

    def classify_file(self, file_path: str) -> LayerType:
        """
        Classify a file into a layer based on its path.

        Args:
            file_path: File path to classify

        Returns:
            LayerType for the file
        """
        for layer_pattern in LAYER_PATTERNS:
            patterns = self._compiled_patterns.get(layer_pattern.layer_type, [])
            for pattern in patterns:
                if pattern.search(file_path):
                    return layer_pattern.layer_type

        return LayerType.UNKNOWN

    async def build_layers(
        self,
        snapshot: Snapshot,
        context: SnapshotContext,
    ) -> list[Layer]:
        """
        Build functional layers from snapshot context.

        Args:
            snapshot: Snapshot being processed
            context: Parsed context with file information

        Returns:
            List of created Layer records
        """
        logger.info(f'Building layers for snapshot {snapshot.id[:8]}')

        # Clear any existing layers for this snapshot
        await self.db.execute(
            delete(Layer).where(Layer.snapshot_id == str(snapshot.id))
        )
        await self.db.flush()

        # Classify all files
        classified: dict[LayerType, list[ClassifiedFile]] = defaultdict(list)

        for file_ctx in context.files:
            layer_type = self.classify_file(file_ctx.file_path)

            classified_file = ClassifiedFile(
                file_path=file_ctx.file_path,
                layer_type=layer_type,
                status=file_ctx.status,
                additions=file_ctx.additions,
                deletions=file_ctx.deletions,
                hunks=file_ctx.hunks,
            )

            classified[layer_type].append(classified_file)

        # Create Layer records for each group
        layers = []
        for layer_type, files in classified.items():
            if not files:
                continue

            # Get pattern config for this layer
            pattern_config = next(
                (p for p in LAYER_PATTERNS if p.layer_type == layer_type),
                LayerPattern(layer_type=LayerType.UNKNOWN, patterns=[]),
            )

            # Calculate risk score
            risk_score = self._calculate_risk_score(files, pattern_config)

            # Generate label and intent
            label = self._generate_label(layer_type, files)
            intent = self._generate_intent(layer_type, files)

            # Create Layer
            layer = Layer(
                snapshot_id=str(snapshot.id),
                layer_type=layer_type.value,
                label=label,
                intent=intent,
                files=[f.file_path for f in files],
                symbol_count=0,  # TODO: Extract from code graph
                risk_score=risk_score,
                review_order=pattern_config.review_order,
            )

            self.db.add(layer)
            await self.db.flush()
            await self.db.refresh(layer)

            # Create LayerRange records for each hunk
            for classified_file in files:
                for hunk in classified_file.hunks:
                    layer_range = LayerRange(
                        layer_id=layer.id,
                        file_path=classified_file.file_path,
                        start_line=hunk.get('new_start', 1),
                        end_line=hunk.get('new_start', 1) + hunk.get('new_count', 1) - 1,
                        hunk_content=self._format_hunk_content(hunk),
                    )
                    self.db.add(layer_range)

            layers.append(layer)

            logger.debug(
                f'Created layer {layer_type.value}: {len(files)} files, '
                f'risk={risk_score}'
            )

        await self.db.flush()

        # Sort layers by review order
        layers.sort(key=lambda l: l.review_order)

        logger.info(
            f'Built {len(layers)} layers for snapshot {snapshot.id[:8]}'
        )

        return layers

    def _calculate_risk_score(
        self,
        files: list[ClassifiedFile],
        pattern_config: LayerPattern,
    ) -> int:
        """
        Calculate risk score for a layer.

        Factors:
        - Base risk for layer type
        - Number of files changed
        - Total lines changed
        - File status (deletions are riskier)

        Args:
            files: Files in this layer
            pattern_config: Pattern configuration

        Returns:
            Risk score 0-100
        """
        base_risk = pattern_config.risk_base

        # Adjust based on file count
        file_count = len(files)
        if file_count > 5:
            base_risk += 10
        elif file_count > 10:
            base_risk += 20

        # Adjust based on total changes
        total_changes = sum(f.additions + f.deletions for f in files)
        if total_changes > 100:
            base_risk += 10
        elif total_changes > 300:
            base_risk += 20

        # Deletions are riskier
        total_deletions = sum(f.deletions for f in files)
        if total_deletions > 50:
            base_risk += 5

        # Cap at 100
        return min(100, base_risk)

    def _generate_label(
        self,
        layer_type: LayerType,
        files: list[ClassifiedFile],
    ) -> str:
        """Generate human-readable label for layer."""
        type_labels = {
            LayerType.API: 'API Endpoints',
            LayerType.AUTH: 'Authentication',
            LayerType.DB: 'Database',
            LayerType.SERVICE: 'Business Logic',
            LayerType.MODEL: 'Data Models',
            LayerType.CONFIG: 'Configuration',
            LayerType.TEST: 'Tests',
            LayerType.UI: 'User Interface',
            LayerType.INFRA: 'Infrastructure',
            LayerType.UTIL: 'Utilities',
            LayerType.UNKNOWN: 'Other Files',
        }

        label = type_labels.get(layer_type, 'Unknown')
        return f'{label} ({len(files)} files)'

    def _generate_intent(
        self,
        layer_type: LayerType,
        files: list[ClassifiedFile],
    ) -> str:
        """Generate brief intent description for layer."""
        total_additions = sum(f.additions for f in files)
        total_deletions = sum(f.deletions for f in files)

        # Determine action type
        if all(f.status == 'added' for f in files):
            action = 'Adding new'
        elif all(f.status == 'deleted' for f in files):
            action = 'Removing'
        elif total_additions > total_deletions * 2:
            action = 'Expanding'
        elif total_deletions > total_additions * 2:
            action = 'Refactoring'
        else:
            action = 'Modifying'

        type_intents = {
            LayerType.API: 'API endpoints',
            LayerType.AUTH: 'authentication logic',
            LayerType.DB: 'database models/migrations',
            LayerType.SERVICE: 'business services',
            LayerType.MODEL: 'data schemas',
            LayerType.CONFIG: 'configuration',
            LayerType.TEST: 'test coverage',
            LayerType.UI: 'UI components',
            LayerType.INFRA: 'infrastructure',
            LayerType.UTIL: 'utility functions',
            LayerType.UNKNOWN: 'miscellaneous files',
        }

        subject = type_intents.get(layer_type, 'code')
        return f'{action} {subject}'

    def _format_hunk_content(self, hunk: dict[str, Any]) -> str:
        """Format hunk dictionary into diff text."""
        lines = []
        lines.append(
            f'@@ -{hunk.get("old_start", 0)},{hunk.get("old_count", 0)} '
            f'+{hunk.get("new_start", 0)},{hunk.get("new_count", 0)} @@'
        )

        for deleted in hunk.get('deleted_lines', []):
            lines.append(f'-{deleted.get("content", "")}')
        for added in hunk.get('added_lines', []):
            lines.append(f'+{added.get("content", "")}')

        return '\n'.join(lines)

    async def get_layers_for_snapshot(
        self,
        snapshot_id: str,
    ) -> list[Layer]:
        """Get all layers for a snapshot."""
        result = await self.db.execute(
            select(Layer)
            .where(Layer.snapshot_id == snapshot_id)
            .order_by(Layer.review_order)
        )
        return list(result.scalars().all())

    async def get_layer_with_ranges(
        self,
        layer_id: str,
    ) -> Layer | None:
        """Get a layer with its ranges loaded."""
        from sqlalchemy.orm import selectinload

        result = await self.db.execute(
            select(Layer)
            .options(selectinload(Layer.ranges))
            .where(Layer.id == layer_id)
        )
        return result.scalar_one_or_none()
