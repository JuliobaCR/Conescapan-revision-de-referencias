"""Tests del dashboard: funciones puras, sin red ni PDFs."""

from refcheck.report import match_link, paper_category, paper_comment

# ----------------------------------------------------------------- match_link

def test_match_link_prefiere_doi():
    m = {"doi": "10.1109/cvpr.2016.90", "url": "https://example.com/otra-cosa"}
    assert match_link(m) == "https://doi.org/10.1109/cvpr.2016.90"


def test_match_link_usa_url_si_no_hay_doi():
    m = {"doi": None, "url": "https://dblp.org/rec/conf/x/y"}
    assert match_link(m) == "https://dblp.org/rec/conf/x/y"


def test_match_link_none_si_no_hay_nada_resoluble():
    assert match_link({"doi": None, "url": None}) is None
    assert match_link({}) is None


def test_match_link_ignora_esquemas_no_http():
    """Defensivo: nunca renderizar un href con un esquema que no sea http(s),
    aunque los backends actuales (Crossref/DBLP/OpenAlex/S2/arXiv) no lo hagan."""
    assert match_link({"doi": None, "url": "javascript:alert(1)"}) is None


# -------------------------------------------------------------- paper_category

def _ref(status: str) -> dict:
    return {"verdict": {"status": status}}


def test_categoria_todas_verificadas():
    paper = {"doc_flags": {"needs_review": False}, "references": [_ref("VERIFIED")] * 3}
    assert paper_category(paper) == "OK"


def test_categoria_mixta():
    paper = {"doc_flags": {"needs_review": False},
             "references": [_ref("VERIFIED"), _ref("NOT_FOUND")]}
    assert paper_category(paper) == "MIXTO"


def test_categoria_ninguna_verificada():
    paper = {"doc_flags": {"needs_review": False}, "references": [_ref("NOT_FOUND")] * 2}
    assert paper_category(paper) == "SIN_VERIFICADAS"


def test_categoria_sin_referencias():
    paper = {"doc_flags": {"needs_review": False}, "references": []}
    assert paper_category(paper) == "SIN_REFERENCIAS"


def test_categoria_revisar_manual_gana_a_todo_lo_demas():
    """Si triage marcó el PDF como sospechoso, no importa qué haya
    encontrado la verificación — va a revisión manual igual."""
    paper = {"doc_flags": {"needs_review": True, "reasons": ["parece una presentación"]},
             "references": [_ref("VERIFIED")] * 5}
    assert paper_category(paper) == "REVISAR_MANUAL"


def test_categoria_extraccion_incompleta():
    """GROBID falló y se usó el fallback regex — el paper sí se procesó,
    pero puede faltar referencias sin listar. Distinto de REVISAR_MANUAL
    (que es sobre si el PDF es un paper de verdad) y de ERROR_PROCESAMIENTO
    (que es que no se pudo procesar nada)."""
    paper = {"doc_flags": {"needs_review": False}, "extraction_note": "GROBID falló",
             "references": [_ref("VERIFIED")] * 3}
    assert paper_category(paper) == "EXTRACCION_INCOMPLETA"


def test_categoria_error_procesamiento_gana_a_todo():
    paper = {"doc_flags": {"needs_review": True, "reasons": ["x"]},
             "extraction_note": "y", "processing_error": "boom",
             "references": [_ref("VERIFIED")] * 3}
    assert paper_category(paper) == "ERROR_PROCESAMIENTO"


# --------------------------------------------------------------- paper_comment

def test_comment_menciona_el_motivo_de_revision_manual():
    paper = {"doc_flags": {"needs_review": True, "reasons": ["80% de páginas horizontales"]},
             "references": []}
    assert "80% de páginas horizontales" in paper_comment(paper)


def test_comment_menciona_extraccion_incompleta():
    paper = {"doc_flags": {"needs_review": False}, "extraction_note": "GROBID falló tras reintentar",
             "references": [_ref("VERIFIED")]}
    assert "GROBID falló tras reintentar" in paper_comment(paper)


def test_comment_menciona_error_de_procesamiento():
    paper = {"doc_flags": {}, "processing_error": "PDF corrupto", "references": []}
    assert "PDF corrupto" in paper_comment(paper)


def test_comment_sin_referencias():
    paper = {"doc_flags": {"needs_review": False}, "references": []}
    assert "revisar la extracción" in paper_comment(paper)
