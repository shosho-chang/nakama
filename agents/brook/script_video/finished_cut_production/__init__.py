"""Deep public Interface for Podcast derivative Finished Cut Production.

Three surfaces are public, and nothing else:

* the aggregate — ``FinishedCutProduction``, which owns every production advance;
* the composition root — the wiring an inbound adapter (watcher, CLI) needs to
  build that aggregate for one episode, plus the typed values it must supply;
* ``build_current_release_reader`` — read-only exact-current access for a review
  surface that must not compose semantic workers, renderers or Resolve.

Reaching past these into ``_``-prefixed modules is a boundary violation: the
projection an adapter re-derives itself is the one that silently drifts.
"""

from ._approved_cut import ApprovedCutRegistration
from ._commands import CommandRejectedError
from ._composition import (
    CurrentReleaseReader,
    FinishedCutProductionApplication,
    ProductionCutoverConfiguration,
    ProductionPaths,
    ProductionResolveConfiguration,
    ProductionStatusView,
    build_current_release_reader,
    build_production_application,
)
from ._context import CanonicalSection, CueAnchor, CutSourceRange
from ._correction import (
    RunEventInspection,
    RunInspection,
    RunPolicyDiagnostic,
    RunStageInspection,
)
from ._engine import FinishedCutProduction
from ._records import (
    ArtifactView,
    ComponentView,
    CutView,
    EventView,
    FinishedCutInspection,
    RunView,
    StageName,
    Status,
)
from ._resolve import TimelineIdentity
from ._resolve_davinci import ResolveCutBinding, ResolveProjectBinding
from ._resolve_fusion import ResolveDatabaseIdentity, ResolveProjectLocator

__all__ = [
    "ApprovedCutRegistration",
    "ArtifactView",
    "CanonicalSection",
    "CommandRejectedError",
    "ComponentView",
    "CueAnchor",
    "CurrentReleaseReader",
    "CutSourceRange",
    "CutView",
    "EventView",
    "FinishedCutInspection",
    "FinishedCutProduction",
    "FinishedCutProductionApplication",
    "ProductionCutoverConfiguration",
    "ProductionPaths",
    "ProductionResolveConfiguration",
    "ProductionStatusView",
    "ResolveCutBinding",
    "ResolveDatabaseIdentity",
    "ResolveProjectBinding",
    "ResolveProjectLocator",
    "RunEventInspection",
    "RunInspection",
    "RunPolicyDiagnostic",
    "RunStageInspection",
    "RunView",
    "StageName",
    "Status",
    "TimelineIdentity",
    "build_current_release_reader",
    "build_production_application",
]
