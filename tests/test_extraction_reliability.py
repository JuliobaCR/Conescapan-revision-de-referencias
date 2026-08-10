"""Tests de la capa de resiliencia de extracción: reintento a GROBID, cache
de extracción por PDF, y que un PDF que ni el fallback puede abrir no tire
una excepción. Motivado por el batch real: GROBID falla en solicitudes
puntuales bajo carga con --workers > 1."""

import time

import fitz
import requests

from refcheck.extract import ExtractionCache, Reference, extract_references, extract_refs_regex

TEI_VACIO = ("<TEI xmlns='http://www.tei-c.org/ns/1.0'>"
             "<text><back><listBibl></listBibl></back></text></TEI>")


class FakeResponse:
    def __init__(self, text="", ok=True, status=200):
        self.text = text
        self.ok = ok
        self.status_code = status

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"status {self.status_code}")


def _make_pdf(tmp_path, name="x.pdf"):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    return p


# ---------------------------------------------------------------- reintento

def test_reintenta_una_vez_antes_de_caer_a_regex(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    calls = {"n": 0}

    def fake_post(url, files=None, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("caída puntual")
        return FakeResponse(text=TEI_VACIO)

    monkeypatch.setattr(requests, "post", fake_post)

    refs, note = extract_references(pdf)

    assert calls["n"] == 2          # 1 intento fallido + 1 reintento exitoso
    assert note is None             # se recuperó, no hace falta avisar nada
    assert refs == []


def test_cae_a_regex_con_nota_si_falla_incluso_reintentando(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def fake_post(url, files=None, data=None, timeout=None):
        raise requests.ConnectionError("GROBID no arranca")

    monkeypatch.setattr(requests, "post", fake_post)

    refs, note = extract_references(pdf)

    assert refs == []               # el PDF fake no tiene "References" -> regex no encuentra nada
    assert note is not None
    assert "reintentar" in note


def test_grobid_no_responde_no_reintenta_va_directo_a_regex(tmp_path, monkeypatch):
    """Si /api/isalive ya dice que no hay servicio, no tiene sentido
    reintentar la subida del PDF — cae directo al fallback."""
    pdf = _make_pdf(tmp_path)
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse(ok=False))
    calls = {"n": 0}

    def fake_post(url, files=None, data=None, timeout=None):
        calls["n"] += 1
        return FakeResponse(text=TEI_VACIO)

    monkeypatch.setattr(requests, "post", fake_post)

    _refs, note = extract_references(pdf)

    assert calls["n"] == 0
    assert "no responde" in note


# -------------------------------------------------------- fallback resiliente

def test_extract_refs_regex_no_explota_si_no_puede_abrir_el_pdf(monkeypatch):
    def boom(path):
        raise RuntimeError("no such file")

    monkeypatch.setattr(fitz, "open", boom)
    assert extract_refs_regex("un/path/cualquiera.pdf") == []


# -------------------------------------------------------------- ExtractionCache

def test_extraction_cache_roundtrip(tmp_path):
    pdf = _make_pdf(tmp_path)
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    refs = [Reference(index=1, title="Algo", source="grobid")]

    assert cache.get(pdf) is None       # nada cacheado todavía

    cache.put(pdf, refs, None)
    got_refs, got_note = cache.get(pdf)

    assert got_note is None
    assert len(got_refs) == 1
    assert got_refs[0].title == "Algo"


def test_extraction_cache_invalida_si_el_archivo_cambia(tmp_path):
    pdf = _make_pdf(tmp_path)
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    cache.put(pdf, [Reference(index=1, title="Viejo")], None)

    time.sleep(0.01)
    pdf.write_bytes(b"%PDF-1.4\n%contenido distinto y mas largo\n")

    assert cache.get(pdf) is None        # mtime/tamaño cambiaron -> no sirve lo viejo


def test_extract_references_usa_la_cache_en_la_segunda_llamada(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse())
    calls = {"n": 0}

    def fake_post(url, files=None, data=None, timeout=None):
        calls["n"] += 1
        return FakeResponse(text=TEI_VACIO)

    monkeypatch.setattr(requests, "post", fake_post)

    extract_references(pdf, cache=cache)
    extract_references(pdf, cache=cache)

    assert calls["n"] == 1               # la segunda vez no vuelve a llamar a GROBID


def test_no_cachea_resultados_degradados(tmp_path, monkeypatch):
    """Bug real: si GROBID está caído (transitoriamente — se quedó sin
    memoria a mitad de un batch de 191 papers) y se usa el fallback, ese
    resultado NO debe quedar cacheado. Si no, el paper queda "pegado" con
    el extractor débil para siempre, aunque GROBID ya esté sano de nuevo
    en la próxima corrida."""
    pdf = _make_pdf(tmp_path)
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse(ok=False))

    _refs, note = extract_references(pdf, cache=cache)

    assert note is not None          # se usó el fallback
    assert cache.get(pdf) is None    # pero no quedó cacheado


def test_si_cachea_un_resultado_limpio(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    cache = ExtractionCache(str(tmp_path / "cache.db"))
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse())
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(text=TEI_VACIO))

    _refs, note = extract_references(pdf, cache=cache)

    assert note is None
    assert cache.get(pdf) is not None
