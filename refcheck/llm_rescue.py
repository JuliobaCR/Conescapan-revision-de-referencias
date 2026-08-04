"""
Rescate de referencias UNPARSEABLE con un LLM local (Ollama).

Solo entra en juego cuando GROBID (y el fallback regex) no lograron sacar ni
título ni identificador de una referencia. Le pasamos el string crudo y le
pedimos JSON estructurado: título, autores, año, venue, DOI.

Lo que este módulo NUNCA hace: preguntarle al modelo si la obra existe, si
la cita es real, o cualquier cosa que dependa de su "conocimiento" en vez de
extracción de texto. Un LLM alucina bibliografía con la misma confianza que
la transcribe; acá solo reformatea el string crudo, no inventa metadatos
nuevos. La decisión de si la obra existe sigue siendo de verify.py, contra
registros reales (Crossref/DBLP/OpenAlex/Semantic Scholar).

Requiere Ollama corriendo local:

    docker compose up -d ollama
    docker exec -it <container> ollama pull qwen2.5:7b-instruct
"""

from __future__ import annotations

import json
import re

import requests

from .extract import Reference

DEFAULT_MODEL = "qwen2.5:7b-instruct"

SYSTEM_PROMPT = (
    "Extraés metadatos de una referencia bibliográfica cruda. Devolvés "
    "ÚNICAMENTE JSON válido con estas claves: title (string o null), "
    "authors (lista de apellidos, sin nombres de pila), year (entero o "
    "null), venue (revista o conferencia, string o null), doi (string o "
    "null). No agregues texto fuera del JSON. No completes datos que no "
    "estén en el string: si no aparece el año, year es null. No opines "
    "sobre si la obra existe o es real ni la busques en tu memoria; solo "
    "transcribís lo que ves en el string."
)


class OllamaClient:
    def __init__(self, url: str = "http://localhost:11434",
                 model: str = DEFAULT_MODEL, timeout: int = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def alive(self) -> bool:
        try:
            return requests.get(f"{self.url}/api/tags", timeout=5).ok
        except requests.RequestException:
            return False

    def extract_json(self, raw_reference: str) -> dict | None:
        try:
            r = requests.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": raw_reference},
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0},
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
        except requests.RequestException:
            return None

        content = (r.json().get("message") or {}).get("content", "")
        return _parse_json(content)


def _parse_json(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def rescue(ref: Reference, client: OllamaClient) -> Reference | None:
    """Intenta completar title/authors/year/venue/doi desde ref.raw.

    Devuelve una referencia nueva con los campos rellenados, o None si el
    modelo no devolvió nada usable (sin título, o el resultado sigue sin
    ser consultable). Nunca decide si la obra existe.
    """
    if not ref.raw or ref.is_queryable:
        return None

    data = client.extract_json(ref.raw)
    if not data:
        return None

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    authors_raw = data.get("authors")
    authors = [str(a).strip() for a in authors_raw if str(a).strip()] \
        if isinstance(authors_raw, list) else []

    year = data.get("year")
    year = int(year) if isinstance(year, (int, str)) and str(year).strip().isdigit() else None

    venue = data.get("venue")
    venue = venue.strip() if isinstance(venue, str) and venue.strip() else None

    doi = data.get("doi")
    doi = doi.strip().lower() if isinstance(doi, str) and doi.strip() else None

    rescued = Reference(
        index=ref.index, raw=ref.raw, title=title.strip(), authors=authors,
        year=year, venue=venue, doi=doi or ref.doi, arxiv_id=ref.arxiv_id,
        ref_type=ref.ref_type, source=f"{ref.source}+llm",
    )
    return rescued if rescued.is_queryable else None


def rescue_batch(refs: list[Reference], client: OllamaClient) -> tuple[list[Reference], int]:
    """Aplica rescue() a cada referencia no consultable. Devuelve
    (lista actualizada, cuántas se rescataron)."""
    out: list[Reference] = []
    n = 0
    for ref in refs:
        rescued = rescue(ref, client)
        if rescued is not None:
            out.append(rescued)
            n += 1
        else:
            out.append(ref)
    return out, n
