"""
Chequeo del PDF completo antes de intentar extraer referencias.

GROBID va a intentar sacar "referencias" de cualquier PDF que le pasemos,
incluida una presentación de diapositivas exportada a PDF o un escaneo sin
texto. El resultado en esos casos no tiene sentido — en vez de mostrar un
manuscrito con puros UNPARSEABLE sin explicación, lo marcamos para revisión
humana con el motivo, antes de gastar tiempo en la cascada de verificación.

Heurística, no certeza: un paper con muchas figuras a página completa puede
disparar el aviso de "poco texto" sin ser una presentación. Por eso esto es
un flag con motivo visible en el reporte, nunca un descarte automático.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .extract import safe_path


@dataclass
class DocFlags:
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"needs_review": self.needs_review, "reasons": self.reasons}


def inspect_pdf(pdf_path: str | Path) -> DocFlags:
    """Heurísticas baratas sobre orientación de página y densidad de texto."""
    import fitz  # pymupdf

    flags = DocFlags()
    try:
        doc = fitz.open(safe_path(pdf_path))
    except Exception as exc:  # noqa: BLE001
        flags.needs_review = True
        flags.reasons.append(f"no se pudo abrir el PDF ({exc})")
        return flags

    n_pages = doc.page_count
    if n_pages == 0:
        doc.close()
        flags.needs_review = True
        flags.reasons.append("el PDF no tiene páginas")
        return flags

    landscape = 0
    total_chars = 0
    for page in doc:
        r = page.rect
        if r.width > r.height * 1.05:
            landscape += 1
        total_chars += len(page.get_text())
    doc.close()

    landscape_frac = landscape / n_pages
    avg_chars = total_chars / n_pages

    if landscape_frac > 0.5:
        flags.needs_review = True
        flags.reasons.append(
            f"{landscape_frac:.0%} de las páginas son horizontales — formato típico "
            "de una presentación (PowerPoint/Slides) exportada a PDF, no de un artículo"
        )

    if avg_chars < 200 and n_pages <= 80:
        flags.needs_review = True
        flags.reasons.append(
            f"muy poco texto extraíble (~{avg_chars:.0f} caracteres/página) — puede ser "
            "una presentación con viñetas, o un PDF escaneado sin OCR"
        )

    return flags
