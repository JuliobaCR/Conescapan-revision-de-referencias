"""Tests del dashboard: funciones puras, sin red ni PDFs."""

from refcheck.report import (
    extraction_source_counts,
    issue_type_counts,
    match_link,
    paper_category,
    paper_comment,
    top_flagged_papers,
)

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


# ------------------------------------------------------------------ analíticas

def test_extraction_source_counts():
    results = [
        {"processing_error": None, "extraction_note": None},
        {"processing_error": None, "extraction_note": "GROBID falló"},
        {"processing_error": "boom", "extraction_note": None},
    ]
    counts = extraction_source_counts(results)
    assert counts == {"grobid": 1, "regex_fallback": 1, "error": 1}


def test_issue_type_counts_categoriza_por_palabra_clave():
    results = [{
        "references": [
            {"verdict": {"issues": ["ningún autor citado coincide con el registro"]}},
            {"verdict": {"issues": ["año citado 2020 vs. registrado 2019"]}},
            {"verdict": {"issues": ["venue citado «X» vs. «Y»"]}},
            {"verdict": {"issues": ["título citado muy corto (2 palabras)"]}},
            {"verdict": {"issues": ["coincidencia parcial de título (85%)"]}},
            {"verdict": {"issues": ["algo que no matchea ninguna palabra clave"]}},
        ],
    }]
    counts = issue_type_counts(results)
    assert counts == {
        "autor": 1, "anio": 1, "venue": 1,
        "titulo_corto": 1, "titulo_parcial": 1, "otro": 1,
    }


def test_top_flagged_papers_ordena_de_peor_a_mejor():
    peor = {"submission_id": "1", "file": "a.pdf", "references": [_ref("NOT_FOUND")] * 4,
            "processing_error": None}
    mejor = {"submission_id": "2", "file": "b.pdf",
             "references": [_ref("VERIFIED"), _ref("VERIFIED"), _ref("VERIFIED"), _ref("NOT_FOUND")],
             "processing_error": None}
    top = top_flagged_papers([mejor, peor])
    assert [p["submission_id"] for p, _, _ in top] == ["1", "2"]
    assert top[0][2] == 1.0    # 100% a revisar
    assert top[1][2] == 0.25   # 25% a revisar


def test_top_flagged_papers_ignora_los_que_no_se_procesaron():
    roto = {"submission_id": "9", "file": "c.pdf", "references": [], "processing_error": "boom"}
    assert top_flagged_papers([roto]) == []
