"""Tests del fallback sin GROBID: encabezados no estándar y numeración sin
corchetes. Motivado por templates reales vistos en submissions de CONESCAPAN
("VI. References" con numeral romano, "REFERENCES" en mayúsculas a dos
columnas)."""

import fitz
import pytest

from refcheck.extract import HEADINGS, extract_refs_regex

# --------------------------------------------------------------- encabezados

@pytest.mark.parametrize("heading", [
    "References",
    "REFERENCES",
    "Referencias",
    "Bibliografía",
    "Bibliografia",
    "VI. References",          # numeral romano, visto en un submission real
    "6. References",
    "Referencias bibliográficas",
    "Works Cited",
    "Literature Cited",
])
def test_headings_reconocidos(heading):
    text = f"cuerpo del paper\n\n{heading}\n\n[1] algo"
    assert HEADINGS.search(text) is not None


def test_heading_no_confunde_texto_normal():
    """"References" dentro de una oración no debe dispararse (línea no vacía)."""
    text = "As shown in the references section below, ...\nmás texto"
    assert HEADINGS.search(text) is None


# ----------------------------------------------------------- extracción real

def _make_pdf(tmp_path, body: str):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), body, fontsize=10)
    path = tmp_path / "test.pdf"
    doc.save(path)
    doc.close()
    return path


def test_fallback_con_corchetes(tmp_path):
    body = (
        "References\n"
        "[1] A. Vaswani et al., Attention Is All You Need, NeurIPS, 2017.\n"
        "[2] Y. LeCun et al., Deep Learning, Nature, 2015.\n"
    )
    pdf = _make_pdf(tmp_path, body)
    refs = extract_refs_regex(pdf)
    assert [r.index for r in refs] == [1, 2]
    assert refs[0].year == 2017


def test_fallback_numerado_sin_corchetes(tmp_path):
    """Plan C: template que numera "1." en vez de "[1]"."""
    body = (
        "VI. References\n"
        "1. A. Vaswani et al., Attention Is All You Need, NeurIPS, 2017.\n"
        "2. Y. LeCun et al., Deep Learning, Nature, 2015.\n"
        "3. I. Goodfellow et al., GANs, NeurIPS, 2014.\n"
    )
    pdf = _make_pdf(tmp_path, body)
    refs = extract_refs_regex(pdf)
    assert [r.index for r in refs] == [1, 2, 3]
    assert refs[0].year == 2017


def test_fallback_no_confunde_isbn_con_marcador(tmp_path):
    """Sin al menos 1,2,3 consecutivos al inicio, no debe activarse el plan C
    (un ISBN o rango de páginas no debe partir la lista en pedazos falsos)."""
    body = (
        "References\n"
        "[1] Some Book, ISBN 978-0-13-468599-1, 2020.\n"
    )
    pdf = _make_pdf(tmp_path, body)
    refs = extract_refs_regex(pdf)
    assert len(refs) == 1  # se extrae por [1], no se confunde con "978-0-13..."
