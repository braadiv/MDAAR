"""
MDAAR v2.0.0 — Multi-role Dynamic Architecture for Automated Repositories.
Polyglot full-repository reverse engineering, dual-model planning/execution,
grounded multi-agent documentation, quality validation, and Hybrid BM25 RAG.
"""

__version__ = "2.0.0"

from mdaar.agents import (
    APIAgent,
    ArchitectureAgent,
    BaseDocAgent,
    DOC_SPECIFICATIONS,
    DatabaseAgent,
    DocAuditItem,
    GovernanceAgent,
    ImpactAnalyzer,
    MapperAgent,
    OperationsAgent,
    PlannerAgent,
    QualityAgent,
    UserGuideAgent,
)
from mdaar.analyzers import (
    APISpecParser,
    FileFactCard,
    GitMetadata,
    PolyglotFactExtractor,
    ProjectFactSheet,
    RepoScanner,
    RouteParser,
    ScanResult,
    ScannedFile,
    SchemaParser,
    SemanticFileChunker,
)
from mdaar.gateway import DualModelManager, ModelGateway
from mdaar.orchestrator import MDAARPipeline
from mdaar.rag import DocumentationChatEngine
from mdaar.security import IgnoreMatcher, SecretSanitizer
from mdaar.skills import SkillLoader

__all__ = [
    "__version__",
    "APIAgent",
    "APISpecParser",
    "ArchitectureAgent",
    "BaseDocAgent",
    "DOC_SPECIFICATIONS",
    "DatabaseAgent",
    "DocAuditItem",
    "DocumentationChatEngine",
    "DualModelManager",
    "FileFactCard",
    "GitMetadata",
    "GovernanceAgent",
    "IgnoreMatcher",
    "ImpactAnalyzer",
    "MDAARPipeline",
    "MapperAgent",
    "ModelGateway",
    "OperationsAgent",
    "PlannerAgent",
    "PolyglotFactExtractor",
    "ProjectFactSheet",
    "QualityAgent",
    "RepoScanner",
    "RouteParser",
    "ScanResult",
    "ScannedFile",
    "SchemaParser",
    "SecretSanitizer",
    "SemanticFileChunker",
    "SkillLoader",
    "UserGuideAgent",
]
