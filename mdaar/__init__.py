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
from mdaar.colab_ui import (
    launch_free_router_ui,
    launch_models_config_ui,
    launch_repo_config_ui,
)
from mdaar.gateway import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    FREE_GEMINI_FALLBACK_CHAIN,
    DualModelManager,
    FreeRouterServer,
    ModelGateway,
    diagnose_colab_secrets,
    ensure_local_llm_dependencies,
    launch_cloudflared_tunnel,
    resolve_setting,
)
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
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OPENAI_BASE_URL",
    "DEFAULT_OPENAI_MODEL",
    "FREE_GEMINI_FALLBACK_CHAIN",
    "DatabaseAgent",
    "DocAuditItem",
    "DocumentationChatEngine",
    "DualModelManager",
    "FileFactCard",
    "FreeRouterServer",
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
    "diagnose_colab_secrets",
    "ensure_local_llm_dependencies",
    "launch_cloudflared_tunnel",
    "launch_free_router_ui",
    "launch_models_config_ui",
    "launch_repo_config_ui",
    "resolve_setting",
]
