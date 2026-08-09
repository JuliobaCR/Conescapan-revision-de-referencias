"""Tests de las funciones puras del orquestador. El loop con ThreadPoolExecutor
no se testea directo (es integración); lo que sí se testea es que un paper
con excepción se convierta en un registro visible, no que desaparezca."""

from pathlib import Path

from refcheck.cli import error_paper, submission_label
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
