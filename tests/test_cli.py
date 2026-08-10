"""Tests de las funciones puras del orquestador. El loop con ThreadPoolExecutor
no se testea directo (es integración); lo que sí se testea es que un paper
con excepción se convierta en un registro visible, no que desaparezca."""

from pathlib import Path

from refcheck.cli import error_paper, needs_second_pass, submission_label
from refcheck.report import paper_category, paper_comment


def test_submission_label_usa_el_numero_de_carpeta():
    pdf = Path("submissions/Submissions 7-209/167/Submission/paper largo.pdf")
    assert submission_label(pdf) == "167"


def test_submission_label_usa_el_nombre_si_no_hay_estructura_submission():
    pdf = Path("otra/carpeta/cualquiera.pdf")
    assert submission_label(pdf) == "cualquiera"


def test_error_paper_no_pierde_el_paper():
    """Antes de este fix, un paper con excepción se descartaba con
    `continue` en cli.py y desaparecía del reporte sin dejar rastro."""
    pdf = Path("submissions/Submissions 7-209/42/Submission/roto.pdf")
    paper = error_paper(pdf, RuntimeError("PDF corrupto"))

    assert paper["submission_id"] == "42"
    assert paper["references"] == []
    assert "PDF corrupto" in paper["processing_error"]
    assert paper_category(paper) == "ERROR_PROCESAMIENTO"
    assert "PDF corrupto" in paper_comment(paper)


def test_needs_second_pass_por_extraccion_degradada():
    assert needs_second_pass({"extraction_note": "GROBID falló"}) is True


def test_needs_second_pass_por_error_de_procesamiento():
    assert needs_second_pass({"processing_error": "boom"}) is True


def test_needs_second_pass_falso_si_salio_limpio():
    assert needs_second_pass({"extraction_note": None, "processing_error": None}) is False


def test_needs_second_pass_paper_recuperado_ya_no_lo_pide():
    """El paper que devuelve process_paper() en el reintento exitoso no
    debería pedir un tercer intento."""
    recovered = error_paper(Path("x/Submission/y.pdf"), RuntimeError("algo"))
    recovered["processing_error"] = None  # como si el reintento hubiera salido bien
    assert needs_second_pass(recovered) is False
