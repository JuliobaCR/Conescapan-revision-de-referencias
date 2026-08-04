"""
Extracción de referencias desde PDFs para el pipeline de revisión.

Ruta principal:  PDF -> GROBID (/api/processReferences) -> TEI XML -> dicts
Ruta fallback:   PDF -> PyMuPDF -> corte en "REFERENCES" -> split por [n]

Nada sale de la máquina en esta etapa: GROBID corre local en Docker.

    docker run --rm -d -p 8070:8070 lfoppiano/grobid:0.8.1

Dependencias: requests, pymupdf  (rapidfuzz opcional para el dedupe)
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------

@dataclass
class Reference:
    """Una referencia extraída, lista para consultar contra Crossref/OpenAlex/DBLP."""
    index: int
    raw: str = ""                      # string original, para auditoría y fallback
    title: str | None = None
    authors: list[str] = field(default_factory=list)   # apellidos
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    ref_type: str | None = None        # journal / conference / book / unknown
    source: str = "grobid"             # grobid | regex

    @property
    def is_queryable(self) -> bool:
        """¿Tenemos lo mínimo para buscarla en una API?"""
        if self.doi or self.arxiv_id:
            return True
        if not self.title:
            return False
        # títulos largos son discriminantes por sí solos; los cortos
        # (libros: "Deep Learning") necesitan autor o año de apoyo
        return len(self.title) > 15 or bool(self.authors or self.year)

    @property
    def query_key(self) -> str:
        """Clave de cache: título normalizado + primer autor + año."""
        t = normalize(self.title or self.raw)[:120]
        a = normalize(self.authors[0]) if self.authors else ""
        return f"{t}|{a}|{self.year or ''}"

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(s: str | None) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Ruta principal: GROBID
# --------------------------------------------------------------------------

class GrobidClient:
    def __init__(self, url: str = "http://localhost:8070", timeout: int = 120):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def alive(self) -> bool:
        try:
            return requests.get(f"{self.url}/api/isalive", timeout=5).ok
        except requests.RequestException:
            return False

    def process_references(self, pdf_path: str | Path) -> str:
        """Devuelve TEI XML con solo la bibliografía. Rápido (~1-3s por paper).

        consolidateCitations=0 a propósito: no queremos que GROBID consulte
        servicios externos por su cuenta. Nosotros controlamos qué se envía
        y a dónde en la fase de verificación.
        """
        with open(pdf_path, "rb") as fh:
            r = requests.post(
                f"{self.url}/api/processReferences",
                files={"input": fh},
                data={"consolidateCitations": "0", "includeRawCitations": "1"},
                timeout=self.timeout,
            )
        r.raise_for_status()
        return r.text

    def process_fulltext(self, pdf_path: str | Path) -> str:
        """TEI completo: secciones + refs + marcadores de cita en el cuerpo.
        Más lento (~10-30s) pero permite detectar refs huérfanas (listadas
        pero nunca citadas) y citas huérfanas ([12] sin entrada en la lista).
        """
        with open(pdf_path, "rb") as fh:
            r = requests.post(
                f"{self.url}/api/processFulltextDocument",
                files={"input": fh},
                data={
                    "consolidateCitations": "0",
                    "includeRawCitations": "1",
                    "segmentSentences": "0",
                },
                timeout=self.timeout,
            )
        r.raise_for_status()
        return r.text


def _text(el) -> str | None:
    if el is None:
        return None
    t = " ".join(el.itertext()).strip()
    t = re.sub(r"\s+", " ", t)
    return t or None


def parse_tei_refs(tei_xml: str) -> list[Reference]:
    """TEI XML de GROBID -> lista de Reference."""
    root = ET.fromstring(tei_xml.encode("utf-8"))
    refs: list[Reference] = []

    for i, bibl in enumerate(root.findall(".//tei:listBibl/tei:biblStruct", TEI_NS), 1):
        ref = Reference(index=i)

        # string crudo (viene de includeRawCitations=1)
        raw_el = bibl.find("./tei:note[@type='raw_reference']", TEI_NS)
        ref.raw = _text(raw_el) or ""

        # título: <analytic> = artículo dentro de algo mayor;
        #         si no hay, el título vive en <monogr> (libro, tech report)
        analytic = bibl.find("./tei:analytic", TEI_NS)
        monogr = bibl.find("./tei:monogr", TEI_NS)

        if analytic is not None:
            ref.title = _text(analytic.find("./tei:title[@level='a']", TEI_NS))

        if monogr is not None:
            journal = _text(monogr.find("./tei:title[@level='j']", TEI_NS))
            book = _text(monogr.find("./tei:title[@level='m']", TEI_NS))
            ref.venue = journal or book
            if not ref.title:
                ref.title = ref.venue      # el título ES la monografía
                ref.venue = None
            ref.ref_type = "journal" if journal else ("book" if book else None)

        # autores (apellidos; los nombres de pila son ruido para el matching)
        scope = analytic if analytic is not None else monogr
        if scope is not None:
            for pers in scope.findall(".//tei:author/tei:persName", TEI_NS):
                surname = _text(pers.find("./tei:surname", TEI_NS))
                if surname:
                    ref.authors.append(surname)

        # año: <date when="2019"> o texto suelto
        if monogr is not None:
            date_el = monogr.find(".//tei:imprint/tei:date", TEI_NS)
            if date_el is not None:
                when = date_el.get("when") or _text(date_el) or ""
                m = re.search(r"(19|20)\d{2}", when)
                if m:
                    ref.year = int(m.group(0))

        # identificadores: si hay DOI, la verificación es exacta y gratis
        for idno in bibl.findall(".//tei:idno", TEI_NS):
            kind = (idno.get("type") or "").upper()
            val = _text(idno)
            if not val:
                continue
            if kind == "DOI":
                ref.doi = val.lower().replace("https://doi.org/", "")
            elif kind in ("ARXIV", "ARK"):
                ref.arxiv_id = re.sub(r"^arxiv[:\s]*", "", val, flags=re.IGNORECASE)

        # último recurso: DOI/arXiv escondidos en el string crudo
        if not ref.doi and ref.raw:
            m = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", ref.raw, re.IGNORECASE)
            if m:
                ref.doi = m.group(0).rstrip(".,;").lower()
        if not ref.arxiv_id and ref.raw:
            m = re.search(r"arxiv[:\s]*(\d{4}\.\d{4,5})", ref.raw, re.IGNORECASE)
            if m:
                ref.arxiv_id = m.group(1)

        refs.append(ref)

    return refs


# --------------------------------------------------------------------------
# Fallback sin GROBID (formato IEEE numerado)
# --------------------------------------------------------------------------

HEADINGS = re.compile(
    r"^\s*(references?|referencias|bibliograf[íi]a|bibliography)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_refs_regex(pdf_path: str | Path) -> list[Reference]:
    """Plan B: cortar el texto en el encabezado de bibliografía y partir por [n].

    Funciona razonablemente bien con el template IEEE (refs numeradas), mal
    con estilos autor-año. Usar solo si GROBID no está disponible.
    """
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    matches = list(HEADINGS.finditer(text))
    if not matches:
        return []
    tail = text[matches[-1].end():]

    # partir en cada marcador [n] que abre línea
    parts = re.split(r"\n?\s*\[(\d{1,3})\]\s*", tail)
    refs: list[Reference] = []
    for i in range(1, len(parts) - 1, 2):
        body = re.sub(r"\s+", " ", parts[i + 1]).strip()
        if len(body) < 20:
            continue
        ref = Reference(index=int(parts[i]), raw=body, source="regex")
        m = re.search(r"(19|20)\d{2}", body)
        if m:
            ref.year = int(m.group(0))
        m = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", body, re.IGNORECASE)
        if m:
            ref.doi = m.group(0).rstrip(".,;").lower()
        # título entre comillas: convención casi universal en IEEE
        m = re.search(r"[“\"']([^”\"']{15,300})[”\"']", body)
        if m:
            ref.title = m.group(1).strip(" ,.")
        refs.append(ref)
    return refs


# --------------------------------------------------------------------------
# Entrada única
# --------------------------------------------------------------------------

def extract_references(pdf_path: str | Path,
                       grobid_url: str = "http://localhost:8070") -> list[Reference]:
    client = GrobidClient(grobid_url)
    if client.alive():
        try:
            return parse_tei_refs(client.process_references(pdf_path))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] GROBID falló ({exc}); usando fallback regex")
    else:
        print("[warn] GROBID no responde; usando fallback regex")
    return extract_refs_regex(pdf_path)


if __name__ == "__main__":
    import json
    import sys

    refs = extract_references(sys.argv[1])
    ok = sum(r.is_queryable for r in refs)
    print(f"{len(refs)} referencias | {ok} consultables | "
          f"{sum(1 for r in refs if r.doi)} con DOI")
    print(json.dumps([r.to_dict() for r in refs], indent=2, ensure_ascii=False))
