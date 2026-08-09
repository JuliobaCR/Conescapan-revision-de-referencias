"""
Solapamiento textual ENTRE los papers del mismo batch. 100% local.

Esto no reemplaza a CrossCheck (que compara contra el corpus mundial). Cubre
un hueco distinto que CrossCheck no ve hasta después de publicar: submisiones
múltiples del mismo trabajo, salami slicing y auto-plagio entre papers del
mismo grupo dentro de una misma edición de la conferencia.

Método: shingles de w palabras -> hashes -> Jaccard + containment.
Comparación exhaustiva O(n²), aceptable hasta ~1000 papers. Si crecés más,
cambiá a MinHash + LSH (datasketch) sin tocar el resto del pipeline.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from .extract import safe_path

SHINGLE_SIZE = 7          # 7 palabras: sensible a copia literal, tolerante a edición menor
JACCARD_FLAG = 0.12       # umbral de reporte; calibrar con un batch real
CONTAINMENT_FLAG = 0.25   # un paper corto contenido en uno largo


@dataclass
class OverlapPair:
    a: str
    b: str
    jaccard: float
    containment: float     # respecto al documento más corto
    shared_shingles: int

    @property
    def severity(self) -> str:
        if self.containment >= 0.5:
            return "ALTO"
        if self.containment >= 0.35 or self.jaccard >= 0.25:
            return "MEDIO"
        return "BAJO"


def body_text(pdf_path: str | Path) -> str:
    """Texto del cuerpo, sin la bibliografía.

    Las referencias comparten fraseo por naturaleza y dispararían falsos
    positivos en todo paper del mismo subcampo.

    Un PDF ilegible (path demasiado largo para Windows, archivo corrupto,
    etc.) no debe tirar abajo el cruce de solapamiento completo — mucho
    menos después de ya haber gastado la cuota de las APIs verificando
    referencias. Se degrada a "sin texto" (se excluye del cruce) en vez de
    propagar la excepción.
    """
    import fitz

    try:
        doc = fitz.open(safe_path(pdf_path))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] no se pudo abrir {pdf_path} para el cruce de solapamiento ({exc})")
        return ""
    text = "\n".join(p.get_text() for p in doc)
    doc.close()

    cut = re.search(r"\n\s*(REFERENCES?|REFERENCIAS|BIBLIOGRAF[ÍI]A)\s*\n", text, re.IGNORECASE)
    if cut:
        text = text[: cut.start()]
    return text


def shingles(text: str, w: int = SHINGLE_SIZE) -> set[int]:
    """Normaliza agresivo y devuelve hashes de 8 bytes de cada ventana."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if len(words) < w:
        return set()
    out = set()
    for i in range(len(words) - w + 1):
        chunk = " ".join(words[i: i + w]).encode()
        out.add(int.from_bytes(hashlib.blake2b(chunk, digest_size=8).digest(), "big"))
    return out


def compare(sigs: dict[str, set[int]]) -> list[OverlapPair]:
    pairs: list[OverlapPair] = []
    for (na, sa), (nb, sb) in combinations(sigs.items(), 2):
        if not sa or not sb:
            continue
        inter = len(sa & sb)
        if inter == 0:
            continue
        jac = inter / len(sa | sb)
        cont = inter / min(len(sa), len(sb))
        if jac >= JACCARD_FLAG or cont >= CONTAINMENT_FLAG:
            pairs.append(OverlapPair(na, nb, round(jac, 4), round(cont, 4), inter))
    pairs.sort(key=lambda p: p.containment, reverse=True)
    return pairs


def scan_batch(pdf_paths: list[Path], labels: dict[Path, str] | None = None) -> list[OverlapPair]:
    """labels: nombre para mostrar por path (p.ej. el ID de submission).

    Sin esto, la clave por defecto sería p.name — y dos submissions
    distintos que se llamen igual ("paper.pdf" es común) colisionarían en
    el diccionario, y uno desaparecería del cruce sin ningún error visible.
    str(p) (path completo) es la clave por defecto porque, a diferencia del
    nombre de archivo, siempre es única.
    """
    labels = labels or {}
    sigs = {labels.get(p, str(p)): shingles(body_text(p)) for p in pdf_paths}
    return compare(sigs)
