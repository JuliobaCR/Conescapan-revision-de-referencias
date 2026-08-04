"""refcheck — verificación de referencias para la fase 1 de revisión."""

__version__ = "0.1.0"

from .extract import Reference, extract_references
from .verify import Verdict, Verifier

__all__ = ["Reference", "Verdict", "Verifier", "extract_references"]
