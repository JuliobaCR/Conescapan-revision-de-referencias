"""
Verificación de referencias contra bases bibliográficas públicas.

Lo único que sale de la máquina en esta etapa son cadenas de metadatos
(título, apellido, año) de trabajos YA PUBLICADOS. El texto del manuscrito
bajo revisión nunca se envía a ningún servicio externo.

Cascada:  DOI directo -> DBLP -> Crossref -> OpenAlex -> Semantic Scholar
Se detiene apenas hay un match confiable, para no quemar rate limits.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import requests
from rapidfuzz import fuzz

from .extract import Reference, normalize

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------

CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "")        # polite pool
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO", "")
OPENALEX_KEY = os.getenv("OPENALEX_API_KEY", "")
S2_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

UA = f"refcheck/1.0 (revisión de conferencia; mailto:{CROSSREF_MAILTO or 'unset'})"

# Umbrales de decisión. Ajustables tras calibrar con un batch conocido.
T_STRONG = 92.0     # similitud de título para dar por verificada
T_WEAK = 80.0       # por debajo de esto, no es la misma obra
YEAR_TOLERANCE = 1  # preprint en 2019 publicado en 2020 es normal
SHORT_TITLE_WORDS = 4  # con menos palabras citadas, el título solo no alcanza para verificar


@dataclass
class Verdict:
    status: str = "NOT_FOUND"
    # VERIFIED | METADATA_MISMATCH | POSSIBLE_MATCH | NOT_FOUND | UNPARSEABLE
    confidence: float = 0.0
    source: str | None = None          # qué base la encontró
    matched: dict[str, Any] | None = None
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Cache: sin esto vas a consultar "Attention Is All You Need" 80 veces
# --------------------------------------------------------------------------

class Cache:
    """Un Verifier se comparte entre los threads del pool (--workers), así
    que la conexión también. SQLite prohíbe eso por default; se habilita con
    check_same_thread=False y se serializa el acceso con un lock — más
    simple que abrir una conexión por thread, y el volumen de un batch de
    papers no justifica nada más elaborado."""

    def __init__(self, path: str = "refcheck_cache.db"):
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "  key TEXT PRIMARY KEY, payload TEXT, ts REAL)"
            )
            self.con.commit()

    def get(self, key: str, max_age_days: int = 60) -> dict | None:
        with self.lock:
            row = self.con.execute(
                "SELECT payload, ts FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        if time.time() - row[1] > max_age_days * 86400:
            return None
        return json.loads(row[0])

    def put(self, key: str, payload: dict) -> None:
        with self.lock:
            self.con.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (key, json.dumps(payload), time.time()),
            )
            self.con.commit()


class RateLimiter:
    """Un limitador por host. Ser buen ciudadano evita que te bloqueen."""

    def __init__(self, min_interval: float = 0.6):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        delta = time.time() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.time()


# --------------------------------------------------------------------------
# Scoring: acá se decide si un candidato es realmente la obra citada
# --------------------------------------------------------------------------

def _surname_matches(a: str, b: str) -> bool:
    """¿"a" y "b" son el mismo apellido? Tolera el apellido compuesto
    hispano (paterno + materno): una cita que solo trae el primer apellido
    ("Martinez") debe matchear contra un registro con el apellido completo
    ("Martinez Betancourt") — es la forma de citar más común, no un error.

    Comparamos por el primer token de cada lado (el apellido paterno,
    convención hispana) en vez de por substring/token sueltos, para no
    confundir el apellido materno de una persona con el de otra distinta
    que comparta ese segundo apellido.
    """
    if fuzz.ratio(a, b) > 88:
        return True
    a0 = a.split()[0] if a.split() else a
    b0 = b.split()[0] if b.split() else b
    return fuzz.ratio(a0, b0) > 88


def author_overlap(cited: list[str], found: list[str]) -> float:
    """Fracción de apellidos citados que aparecen en el candidato."""
    if not cited or not found:
        return -1.0          # indeterminado, no penaliza
    c = {normalize(a) for a in cited if a}
    f = {normalize(a) for a in found if a}
    if not c or not f:
        return -1.0
    hits = sum(1 for a in c if any(_surname_matches(a, b) for b in f))
    return hits / len(c)


def score_candidate(ref: Reference, cand: dict) -> tuple[float, list[str]]:
    """Devuelve (similitud de título 0-100, lista de discrepancias)."""
    issues: list[str] = []

    t_sim = fuzz.token_set_ratio(
        normalize(ref.title or ref.raw), normalize(cand.get("title"))
    )

    ov = author_overlap(ref.authors, cand.get("authors", []))
    if ov == 0.0:
        issues.append("ningún autor citado coincide con el registro encontrado")
    elif 0.0 < ov < 0.5:
        issues.append(f"solo {ov:.0%} de los autores citados coinciden")

    if ref.year and cand.get("year"):
        delta = abs(ref.year - int(cand["year"]))
        if delta > YEAR_TOLERANCE:
            issues.append(f"año citado {ref.year} vs. registrado {cand['year']}")

    if (ref.venue and cand.get("venue") and fuzz.partial_token_set_ratio(
            normalize(ref.venue), normalize(cand["venue"])) < 55):
        issues.append(f"venue citado «{ref.venue}» vs. «{cand['venue']}»")

    return t_sim, issues


def _title_word_count(ref: Reference) -> int:
    return len(normalize(ref.title or ref.raw).split())


def decide(ref: Reference, cand: dict | None, source: str | None) -> Verdict:
    if cand is None:
        return Verdict(status="NOT_FOUND", confidence=0.0)

    t_sim, issues = score_candidate(ref, cand)
    ov = author_overlap(ref.authors, cand.get("authors", []))

    if t_sim >= T_STRONG:
        # Cita quimérica: título real, autores inventados. Firma típica de
        # una referencia generada por un LLM.
        if ov == 0.0:
            return Verdict("METADATA_MISMATCH", t_sim / 100, source, cand, issues)

        # Un título citado muy corto ("machine learning power grid") puede
        # dar token_set_ratio alto contra el paper equivocado por pura
        # coincidencia de palabras genéricas. Con pocas palabras, el título
        # solo no alcanza — hace falta que el autor o el año lo respalden.
        if _title_word_count(ref) < SHORT_TITLE_WORDS and ov <= 0:
            year_ok = (
                ref.year and cand.get("year")
                and abs(ref.year - int(cand["year"])) <= YEAR_TOLERANCE
            )
            if not year_ok:
                issues.insert(
                    0,
                    f"título citado muy corto ({_title_word_count(ref)} palabras) y sin "
                    "autor ni año que lo confirmen — la coincidencia de título sola no alcanza",
                )
                return Verdict("POSSIBLE_MATCH", t_sim / 100, source, cand, issues)

        status = "VERIFIED" if not issues else "METADATA_MISMATCH"
        return Verdict(status, t_sim / 100, source, cand, issues)

    if t_sim >= T_WEAK:
        issues.insert(0, f"coincidencia parcial de título ({t_sim:.0f}%)")
        return Verdict("POSSIBLE_MATCH", t_sim / 100, source, cand, issues)

    return Verdict("NOT_FOUND", t_sim / 100)


# --------------------------------------------------------------------------
# Adaptadores: cada base devuelve el mismo dict normalizado
#   {title, authors[apellidos], year, venue, doi, url}
# --------------------------------------------------------------------------

class Backend:
    name = "base"
    limiter = RateLimiter()

    def search(self, ref: Reference) -> dict | None:
        raise NotImplementedError

    def _get(self, url: str, **kw) -> dict | None:
        self.limiter.wait()
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25, **kw)
            if r.status_code == 429:
                time.sleep(5)
                r = requests.get(url, headers={"User-Agent": UA}, timeout=25, **kw)
            if not r.ok:
                return None
            return r.json()
        except (requests.RequestException, ValueError):
            return None


class CrossrefDOI(Backend):
    """Resolución exacta por DOI. Un 404 acá es señal fuerte de DOI inventado."""
    name = "crossref-doi"
    limiter = RateLimiter(0.4)

    def search(self, ref: Reference) -> dict | None:
        if not ref.doi:
            return None
        params = {"mailto": CROSSREF_MAILTO} if CROSSREF_MAILTO else {}
        data = self._get(f"https://api.crossref.org/works/{ref.doi}", params=params)
        if not data or "message" not in data:
            return None
        return _from_crossref(data["message"])


class CrossrefSearch(Backend):
    name = "crossref"
    limiter = RateLimiter(0.4)

    def search(self, ref: Reference) -> dict | None:
        if not ref.title:
            return None
        params = {
            "query.bibliographic": ref.title,
            "rows": 5,
            "select": "DOI,title,author,issued,container-title,type,URL",
        }
        if ref.authors:
            params["query.author"] = ref.authors[0]
        if CROSSREF_MAILTO:
            params["mailto"] = CROSSREF_MAILTO
        data = self._get("https://api.crossref.org/works", params=params)
        if not data:
            return None
        items = data.get("message", {}).get("items", [])
        return _best(ref, [_from_crossref(i) for i in items])


class DBLP(Backend):
    """Cobertura casi total de conferencias de CS. Primera parada para tu caso."""
    name = "dblp"
    limiter = RateLimiter(1.2)   # DBLP es sensible, no lo apures

    def search(self, ref: Reference) -> dict | None:
        if not ref.title:
            return None
        data = self._get(
            "https://dblp.org/search/publ/api",
            params={"q": ref.title[:200], "format": "json", "h": 5},
        )
        if not data:
            return None
        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        cands = []
        for h in hits:
            info = h.get("info", {})
            au = info.get("authors", {}).get("author", [])
            if isinstance(au, dict):
                au = [au]
            names = [a.get("text", "") if isinstance(a, dict) else str(a) for a in au]
            cands.append({
                "title": (info.get("title") or "").rstrip("."),
                "authors": [n.split()[-1] for n in names if n],
                "year": int(info["year"]) if info.get("year", "").isdigit() else None,
                "venue": info.get("venue"),
                "doi": (info.get("doi") or "").lower() or None,
                "url": info.get("ee") or info.get("url"),
            })
        return _best(ref, cands)


class OpenAlex(Backend):
    name = "openalex"
    limiter = RateLimiter(0.15)

    def search(self, ref: Reference) -> dict | None:
        if not ref.title:
            return None
        params = {"search": ref.title[:250], "per-page": 5}
        if OPENALEX_MAILTO:
            params["mailto"] = OPENALEX_MAILTO
        if OPENALEX_KEY:
            params["api_key"] = OPENALEX_KEY
        data = self._get("https://api.openalex.org/works", params=params)
        if not data:
            return None
        cands = []
        for w in data.get("results", []):
            auths = [
                a.get("author", {}).get("display_name", "")
                for a in w.get("authorships", [])
            ]
            cands.append({
                "title": w.get("title") or w.get("display_name"),
                "authors": [n.split()[-1] for n in auths if n],
                "year": w.get("publication_year"),
                "venue": ((w.get("primary_location") or {}).get("source") or {}).get(
                    "display_name"
                ),
                "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
                "url": w.get("id"),
            })
        return _best(ref, cands)


class SemanticScholar(Backend):
    name = "semantic-scholar"
    limiter = RateLimiter(1.1 if not S2_KEY else 0.35)

    def search(self, ref: Reference) -> dict | None:
        if not ref.title:
            return None
        headers = {"User-Agent": UA}
        if S2_KEY:
            headers["x-api-key"] = S2_KEY
        self.limiter.wait()
        try:
            r = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": ref.title[:250],
                    "limit": 5,
                    "fields": "title,authors,year,venue,externalIds,url",
                },
                headers=headers,
                timeout=25,
            )
            if not r.ok:
                return None
            data = r.json()
        except (requests.RequestException, ValueError):
            return None

        cands = []
        for p in data.get("data", []):
            cands.append({
                "title": p.get("title"),
                "authors": [
                    (a.get("name") or "").split()[-1]
                    for a in p.get("authors", []) if a.get("name")
                ],
                "year": p.get("year"),
                "venue": p.get("venue"),
                "doi": (p.get("externalIds") or {}).get("DOI"),
                "url": p.get("url"),
            })
        return _best(ref, cands)


class ArXiv(Backend):
    name = "arxiv"
    limiter = RateLimiter(3.0)   # arXiv pide explícitamente 1 req cada 3s

    def search(self, ref: Reference) -> dict | None:
        if not ref.arxiv_id:
            return None
        self.limiter.wait()
        try:
            r = requests.get(
                "http://export.arxiv.org/api/query",
                params={"id_list": ref.arxiv_id, "max_results": 1},
                headers={"User-Agent": UA},
                timeout=25,
            )
            if not r.ok:
                return None
        except requests.RequestException:
            return None

        import xml.etree.ElementTree as ET
        ns = {"a": "http://www.w3.org/2005/Atom"}
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            return None
        entry = root.find("a:entry", ns)
        if entry is None:
            return None
        title = (entry.findtext("a:title", "", ns) or "").strip()
        names = [
            (e.findtext("a:name", "", ns) or "").strip()
            for e in entry.findall("a:author", ns)
        ]
        pub = entry.findtext("a:published", "", ns) or ""
        return {
            "title": " ".join(title.split()),
            "authors": [n.split()[-1] for n in names if n],
            "year": int(pub[:4]) if pub[:4].isdigit() else None,
            "venue": "arXiv",
            "doi": None,
            "url": entry.findtext("a:id", "", ns),
        }


def _from_crossref(item: dict) -> dict:
    ct = item.get("container-title") or []
    issued = (item.get("issued") or {}).get("date-parts") or [[None]]
    titles = item.get("title") or []
    return {
        "title": titles[0] if titles else None,
        "authors": [a.get("family", "") for a in item.get("author", []) if a.get("family")],
        "year": issued[0][0] if issued and issued[0] else None,
        "venue": ct[0] if ct else None,
        "doi": (item.get("DOI") or "").lower() or None,
        "url": item.get("URL"),
    }


def _best(ref: Reference, cands: list[dict]) -> dict | None:
    """El candidato con mayor similitud de título, si supera el piso."""
    cands = [c for c in cands if c and c.get("title")]
    if not cands:
        return None
    scored = [(score_candidate(ref, c)[0], c) for c in cands]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored[0][0] >= T_WEAK else None


# --------------------------------------------------------------------------
# Orquestación de la cascada
# --------------------------------------------------------------------------

class Verifier:
    def __init__(self, cache_path: str = "refcheck_cache.db"):
        self.cache = Cache(cache_path)
        self.by_id = [CrossrefDOI(), ArXiv()]
        self.by_title = [DBLP(), CrossrefSearch(), OpenAlex(), SemanticScholar()]

    def verify(self, ref: Reference) -> Verdict:
        if not ref.is_queryable:
            return Verdict(
                status="UNPARSEABLE",
                issues=["no se pudo extraer título ni identificador de la referencia"],
            )

        cached = self.cache.get(ref.query_key)
        if cached:
            return Verdict(**cached)

        verdict = self._run(ref)
        self.cache.put(ref.query_key, verdict.to_dict())
        return verdict

    def _run(self, ref: Reference) -> Verdict:
        # 1. Identificadores: match exacto, una sola llamada
        for backend in self.by_id:
            cand = backend.search(ref)
            if cand:
                v = decide(ref, cand, backend.name)
                if v.status != "NOT_FOUND":
                    return v
            elif backend.name == "crossref-doi" and ref.doi:
                # El DOI existe en la cita pero no resuelve: bandera roja.
                v = self._by_title(ref)
                v.issues.insert(0, f"el DOI {ref.doi} no resuelve en Crossref")
                if v.status == "VERIFIED":
                    v.status = "METADATA_MISMATCH"
                return v

        # 2. Búsqueda por título
        return self._by_title(ref)

    def _by_title(self, ref: Reference) -> Verdict:
        best = Verdict()
        for backend in self.by_title:
            cand = backend.search(ref)
            v = decide(ref, cand, backend.name)
            if v.status == "VERIFIED":
                return v
            if v.confidence > best.confidence:
                best = v
        return best
