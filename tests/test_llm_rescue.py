"""Tests del rescate por LLM local. Todo mockeado, sin red ni Ollama real."""

import requests

from refcheck.extract import Reference
from refcheck.llm_rescue import OllamaClient, _parse_json, rescue, rescue_batch


class FakeResponse:
    def __init__(self, payload, ok=True, status=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"status {self.status_code}")


def _client_returning(monkeypatch, content: str) -> OllamaClient:
    def fake_post(url, json=None, timeout=None):
        return FakeResponse({"message": {"content": content}})
    monkeypatch.setattr(requests, "post", fake_post)
    return OllamaClient()


# --------------------------------------------------------------- parseo JSON

def test_parse_json_directo():
    assert _parse_json('{"title": "X"}') == {"title": "X"}


def test_parse_json_con_texto_alrededor():
    """Algunos modelos meten prosa aunque se les pida solo JSON."""
    content = 'Acá está: {"title": "X", "year": 2020} espero que ayude'
    assert _parse_json(content) == {"title": "X", "year": 2020}


def test_parse_json_basura_devuelve_none():
    assert _parse_json("no hay json acá") is None


# --------------------------------------------------------------------- alive

def test_alive_false_si_no_hay_conexion(monkeypatch):
    def fake_get(url, timeout=None):
        raise requests.ConnectionError("no hay servicio")
    monkeypatch.setattr(requests, "get", fake_get)
    assert not OllamaClient().alive()


def test_alive_true_si_responde(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse({}))
    assert OllamaClient().alive()


# -------------------------------------------------------------------- rescue

def test_rescue_completa_campos_y_marca_la_fuente(monkeypatch):
    content = (
        '{"title": "Attention Is All You Need", "authors": ["Vaswani", "Shazeer"], '
        '"year": 2017, "venue": "NeurIPS", "doi": null}'
    )
    client = _client_returning(monkeypatch, content)
    ref = Reference(index=1, raw="A. Vaswani et al, Attention..., NeurIPS 2017", source="regex")

    out = rescue(ref, client)

    assert out is not None
    assert out.title == "Attention Is All You Need"
    assert out.authors == ["Vaswani", "Shazeer"]
    assert out.year == 2017
    assert out.venue == "NeurIPS"
    assert out.source == "regex+llm"
    assert out.raw == ref.raw          # el string crudo no se toca


def test_rescue_no_toca_referencias_ya_consultables(monkeypatch):
    client = _client_returning(monkeypatch, '{"title": "no debería llamarse"}')
    ref = Reference(index=1, raw="algo", title="Ya tengo título suficientemente largo")
    assert rescue(ref, client) is None


def test_rescue_devuelve_none_sin_titulo(monkeypatch):
    client = _client_returning(monkeypatch, '{"title": null, "year": 2020}')
    ref = Reference(index=1, raw="IEEE Std 802.11-2020")
    assert rescue(ref, client) is None


def test_rescue_devuelve_none_si_ollama_no_responde(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise requests.ConnectionError("caído")
    monkeypatch.setattr(requests, "post", fake_post)
    ref = Reference(index=1, raw="algo sin parsear")
    assert rescue(ref, OllamaClient()) is None


def test_rescue_batch_cuenta_solo_lo_rescatado(monkeypatch):
    content = '{"title": "Un Título Suficientemente Largo Para Ser Válido"}'
    client = _client_returning(monkeypatch, content)
    refs = [
        Reference(index=1, raw="sin parsear, string largo de verdad"),
        Reference(index=2, title="Ya consultable con su propio título largo"),
    ]

    out, n = rescue_batch(refs, client)

    assert n == 1
    assert out[0].title == "Un Título Suficientemente Largo Para Ser Válido"
    assert out[1].title == "Ya consultable con su propio título largo"
