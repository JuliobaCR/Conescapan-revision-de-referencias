"""Tests que no tocan la red. Corren en CI en segundos.

Cubren lo que puede romperse en silencio: el parseo del TEI y las reglas de
decisión. Un bug acá no falla ruidosamente, solo produce veredictos malos.
"""

from pathlib import Path

import pytest

from refcheck import dedupe
from refcheck.extract import Reference, normalize, parse_tei_refs
from refcheck.verify import author_overlap, decide, score_candidate

FIXTURE = "tests/fixtures/sample_tei.xml"


@pytest.fixture(scope="module")
def refs():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_tei_refs(fh.read())


# ---------------------------------------------------------------- extracción

def test_parsea_las_tres_referencias(refs):
    assert len(refs) == 3


def test_articulo_en_actas(refs):
    r = refs[0]
    assert r.title == "Attention Is All You Need"
    assert r.authors == ["Vaswani", "Shazeer"]
    assert r.year == 2017
    assert r.arxiv_id == "1706.03762"      # sin el prefijo "arXiv:"
    assert r.raw.startswith("A. Vaswani")


def test_doi_rescatado_del_string_crudo(refs):
    """El DOI no venía en un <idno>, sino embebido en el raw."""
    assert refs[1].doi == "10.48550/arxiv.1907.05737"


def test_libro_sin_analytic(refs):
    """Sin <analytic>, el título vive en <monogr> y no debe quedar como venue."""
    r = refs[2]
    assert r.title == "Deep Learning"
    assert r.venue is None
    assert r.authors == ["Goodfellow"]


def test_titulo_corto_con_autor_es_consultable(refs):
    """Regresión: "Deep Learning" tiene 13 caracteres y aun así debe buscarse."""
    assert refs[2].is_queryable


def test_sin_titulo_ni_id_no_es_consultable():
    assert not Reference(index=1, raw="IEEE Std 802.11-2020").is_queryable


def test_normalize_quita_acentos_y_puntuacion():
    assert normalize("Búsqueda de Arquitecturas: un Análisis") == \
        "busqueda de arquitecturas un analisis"


def test_query_key_es_estable():
    a = Reference(index=1, title="Deep  Learning!", authors=["Goodfellow"], year=2016)
    b = Reference(index=9, title="deep learning", authors=["goodfellow"], year=2016)
    assert a.query_key == b.query_key


# ------------------------------------------------------------------- scoring

CANDIDATO = {
    "title": "Attention is All you Need",
    "authors": ["Vaswani", "Shazeer", "Parmar"],
    "year": 2017,
    "venue": "NeurIPS",
}


def test_referencia_legitima_se_verifica():
    r = Reference(index=1, title="Attention Is All You Need",
                  authors=["Vaswani", "Shazeer"], year=2017)
    v = decide(r, CANDIDATO, "dblp")
    assert v.status == "VERIFIED"
    assert v.issues == []


def test_cita_quimerica_se_marca():
    """Título real, autores inventados: la firma de una cita generada por LLM."""
    r = Reference(index=2, title="Attention Is All You Need",
                  authors=["Martinez", "Rojas"], year=2017)
    v = decide(r, CANDIDATO, "dblp")
    assert v.status == "METADATA_MISMATCH"
    assert any("autor" in i for i in v.issues)


def test_año_muy_desviado_se_marca():
    r = Reference(index=3, title="Attention Is All You Need",
                  authors=["Vaswani"], year=2011)
    v = decide(r, CANDIDATO, "dblp")
    assert v.status == "METADATA_MISMATCH"


def test_un_año_de_diferencia_se_tolera():
    """Preprint en un año, actas en el siguiente: caso normal, no es error."""
    r = Reference(index=4, title="Attention Is All You Need",
                  authors=["Vaswani"], year=2018)
    assert decide(r, CANDIDATO, "dblp").status == "VERIFIED"


def test_referencia_inexistente():
    r = Reference(index=5, title="Quantum Butterfly Search for Neural Wombats",
                  authors=["Perez"], year=2023)
    assert decide(r, CANDIDATO, "dblp").status == "NOT_FOUND"


def test_sin_autores_extraidos_no_penaliza():
    """Si GROBID no sacó autores no podemos concluir nada: no es un mismatch."""
    r = Reference(index=6, title="Attention Is All You Need", year=2017)
    assert decide(r, CANDIDATO, "dblp").status == "VERIFIED"
    assert author_overlap([], ["Vaswani"]) == -1.0


def test_candidato_ausente_es_not_found():
    r = Reference(index=7, title="Lo que sea", authors=["X"], year=2020)
    assert decide(r, None, None).status == "NOT_FOUND"


def test_titulo_parcial_da_coincidencia_debil():
    r = Reference(index=8, title="Attention is All you Need in Vision Transformers "
                                 "for Fine Grained Recognition", authors=["Vaswani"])
    v = decide(r, CANDIDATO, "dblp")
    assert v.status in ("POSSIBLE_MATCH", "METADATA_MISMATCH", "VERIFIED")
    assert v.confidence > 0.5


def test_solape_de_autores_es_tolerante_a_tildes():
    assert author_overlap(["Jiménez"], ["Jimenez", "Mora"]) == 1.0


def test_venue_distinto_se_reporta():
    r = Reference(index=9, title="Attention Is All You Need",
                  authors=["Vaswani"], year=2017, venue="IEEE Transactions on Cats")
    _, issues = score_candidate(r, CANDIDATO)
    assert any("venue" in i for i in issues)


# -------------------------------------------------------------------- dedupe

def test_documento_contenido_en_otro():
    base = ("la busqueda de arquitecturas neuronales aplicada al reconocimiento "
            "visual de grano fino permite explorar espacios de diseno ") * 6
    extendido = base + ("un apartado adicional sobre mariposas del neotropico "
                        "y su clasificacion taxonomica ") * 3
    pares = dedupe.compare({"a.pdf": dedupe.shingles(base),
                            "b.pdf": dedupe.shingles(extendido)})
    assert len(pares) == 1
    assert pares[0].containment == pytest.approx(1.0)
    assert pares[0].severity == "ALTO"


def test_documentos_distintos_no_disparan():
    a = "analisis espectral de corrientes en transformadores de distribucion " * 8
    b = "modelos de lenguaje aplicados a la deteccion de desinformacion " * 8
    assert dedupe.compare({"a.pdf": dedupe.shingles(a),
                           "b.pdf": dedupe.shingles(b)}) == []


def test_texto_muy_corto_no_produce_shingles():
    assert dedupe.shingles("apenas tres palabras") == set()


def test_scan_batch_no_colisiona_por_nombre_de_archivo_igual(monkeypatch):
    """Bug real visto con submissions de CONESCAPAN: dos papers distintos,
    de carpetas distintas, ambos subidos como "paper.pdf". Sin labels por
    path, el diccionario interno los pisaba y uno desaparecía del cruce."""
    a = Path("7/Submission/paper.pdf")
    b = Path("66/Submission/paper.pdf")
    monkeypatch.setattr(dedupe, "body_text", lambda p: "contenido de prueba " * 10)
    captured = {}
    monkeypatch.setattr(dedupe, "compare", lambda sigs: captured.update(sigs) or [])

    dedupe.scan_batch([a, b], labels={a: "7", b: "66"})

    assert set(captured.keys()) == {"7", "66"}


def test_scan_batch_sin_labels_no_colisiona_por_path_completo(monkeypatch):
    """Sin labels, la clave por defecto es el path completo (único), no el
    nombre de archivo (que puede repetirse)."""
    a = Path("7/Submission/paper.pdf")
    b = Path("66/Submission/paper.pdf")
    monkeypatch.setattr(dedupe, "body_text", lambda p: "contenido de prueba " * 10)
    captured = {}
    monkeypatch.setattr(dedupe, "compare", lambda sigs: captured.update(sigs) or [])

    dedupe.scan_batch([a, b])

    assert len(captured) == 2


# --------------------------------------------------- integración (opt-in)
# Corren con: pytest -m network
# Fuera del CI a propósito: no queremos que un outage de Crossref rompa el build.

@pytest.mark.network
def test_doi_real_resuelve_en_crossref():
    from refcheck.verify import CrossrefDOI
    r = Reference(index=1, title="Deep Residual Learning for Image Recognition",
                  authors=["He"], year=2016, doi="10.1109/cvpr.2016.90")
    cand = CrossrefDOI().search(r)
    assert cand is not None
    assert "He" in cand["authors"]
    assert decide(r, cand, "crossref-doi").status == "VERIFIED"


@pytest.mark.network
def test_doi_inventado_no_resuelve():
    from refcheck.verify import CrossrefDOI
    r = Reference(index=2, title="Lo que sea", doi="10.9999/inventado.2023.404")
    assert CrossrefDOI().search(r) is None
