"""Grouped SolidWorks mixins for the PyWin32 adapter."""

from .analysis_methods import SolidWorksAnalysisMixin
from .bodies import SolidWorksBodiesMixin
from .documentation import SolidWorksDocumentationMixin
from .drawing_standard import SolidWorksDrawingStandardMixin
from .features import SolidWorksFeaturesMixin
from .io import SolidWorksIOMixin
from .selection import SolidWorksSelectionMixin
from .sketch import SolidWorksSketchMixin

__all__ = [
    "SolidWorksAnalysisMixin",
    "SolidWorksBodiesMixin",
    "SolidWorksDocumentationMixin",
    "SolidWorksDrawingStandardMixin",
    "SolidWorksFeaturesMixin",
    "SolidWorksIOMixin",
    "SolidWorksSelectionMixin",
    "SolidWorksSketchMixin",
]
