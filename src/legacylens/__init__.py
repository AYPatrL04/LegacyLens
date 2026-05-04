"""Legacy Lens backend package."""

from .engine import LegacyLensEngine
from .models import AnalysisRequest, AnalysisResponse, Finding, ProjectContext

__all__ = [
    "AnalysisRequest",
    "AnalysisResponse",
    "Finding",
    "LegacyLensEngine",
    "ProjectContext",
]

__version__ = "0.1.0"
