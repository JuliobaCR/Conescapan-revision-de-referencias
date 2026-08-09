#!/usr/bin/env python3
"""
Fase 1 de revisión: extracción y verificación de referencias.

    python pipeline.py ./submissions --out ./reporte

Qué hace y qué no:
  ✓ Extrae la bibliografía de cada PDF (GROBID local)
  ✓ Verifica cada referencia contra Crossref, DBLP, OpenAlex, Semantic Scholar
  ✓ Marca citas quiméricas: título real con autores o año que no corresponden
  ✓ Detecta solapamiento textual entre los manuscritos del lote
  ✗ NO detecta plagio contra literatura publicada — eso es IEEE CrossCheck
  ✗ NO decide aceptación o rechazo — produce una lista de casos a revisar

El texto de los manuscritos nunca sale de esta máquina. A las APIs solo van
cadenas de metadatos de trabajos ya publicados.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import dedupe, llm_rescue, report, triage
from .extract import ExtractionCache, extract_references
from .verify import Verifier


def submission_label(pdf: Path) -> str:
    """Si el PDF viene de .../<N>/Submission/archivo.pdf (export típico de un
    sistema de revisión), usa N como ID — mucho más útil que el nombre de
    archivo para ubicar el submission real cuando hay 190 papers."""
    parts = pdf.parts
    for i, part in enumerate(parts):
        if part == "Submission" and i > 0 and parts[i - 1].isdigit():
            return parts[i - 1]
    return pdf.stem


def process_paper(pdf: Path, verifier: Verifier, grobid_url: str,
                   ollama_client: llm_rescue.OllamaClient | None = None,
                   extract_cache: ExtractionCache | None = None) -> dict:
    doc_flags = triage.inspect_pdf(pdf)
    refs, extraction_note = extract_references(pdf, grobid_url, cache=extract_cache)
    n_rescued = 0
    if ollama_client is not None:
        refs, n_rescued = llm_rescue.rescue_batch(refs, ollama_client)
    out = []
    for ref in refs:
        verdict = verifier.verify(ref)
        row = ref.to_dict()
        row["verdict"] = verdict.to_dict()
        out.append(row)
    return {
        "submission_id": submission_label(pdf),
        "file": pdf.name,
        "path": str(pdf),
        "references": out,
        "llm_rescued": n_rescued,
        "doc_flags": doc_flags.to_dict(),
        "extraction_note": extraction_note,
        "processing_error": None,
    }


def error_paper(pdf: Path, exc: BaseException) -> dict:
    """Un paper cuyo procesamiento explotó por algo no previsto en ninguna
    otra capa (PDF corrupto, encoding raro, lo que sea). Antes esto se
    perdía silenciosamente — un print() que se descartaba y el paper
    directamente no aparecía en el reporte, sin que el revisor se enterara
    de que ese manuscrito nunca se analizó. Ahora queda en el dashboard con
    su propia categoría, para que sea imposible no notarlo."""
    return {
        "submission_id": submission_label(pdf),
        "file": pdf.name,
        "path": str(pdf),
        "references": [],
        "llm_rescued": 0,
        "doc_flags": {"needs_review": True, "reasons": []},
        "extraction_note": None,
        "processing_error": str(exc),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Revisión de referencias, fase 1")
    ap.add_argument("input", help="carpeta con PDFs o un PDF suelto")
    ap.add_argument("--out", default="reporte", help="carpeta de salida")
    ap.add_argument("--grobid", default="http://localhost:8070")
    ap.add_argument("--cache", default="refcheck_cache.db")
    ap.add_argument("--extract-cache", default="refcheck_extract_cache.db",
                    help="cache de qué extrajo GROBID/regex de cada PDF; evita "
                         "volver a mandar todo el batch a GROBID en corridas repetidas")
    ap.add_argument("--no-extract-cache", action="store_true",
                    help="ignorar la cache de extracción (forzar re-extracción de todo)")
    ap.add_argument("--workers", type=int, default=3,
                    help="papers en paralelo; subilo con cuidado, las APIs "
                         "tienen rate limit compartido")
    ap.add_argument("--no-overlap", action="store_true",
                    help="omitir el cruce de solapamiento entre manuscritos")
    ap.add_argument("--llm-rescue", action="store_true",
                    help="usar un LLM local (Ollama) para rescatar referencias "
                         "UNPARSEABLE antes de verificarlas; nunca decide si "
                         "la obra existe, solo reformatea el string crudo")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default=llm_rescue.DEFAULT_MODEL)
    args = ap.parse_args()

    src = Path(args.input)
    pdfs = sorted(src.rglob("*.pdf")) if src.is_dir() else [src]
    if not pdfs:
        print(f"No hay PDFs en {src}", file=sys.stderr)
        return 1

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    ollama_client = None
    if args.llm_rescue:
        ollama_client = llm_rescue.OllamaClient(args.ollama_url, args.ollama_model)
        if not ollama_client.alive():
            print(f"[warn] Ollama no responde en {args.ollama_url}; "
                  "las referencias UNPARSEABLE no se van a rescatar", file=sys.stderr)
            ollama_client = None

    verifier = Verifier(args.cache)
    extract_cache = None if args.no_extract_cache else ExtractionCache(args.extract_cache)
    results: list[dict] = []
    t0 = time.time()

    print(f"Procesando {len(pdfs)} manuscritos…\n")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_paper, p, verifier, args.grobid, ollama_client, extract_cache): p
            for p in pdfs
        }
        for i, fut in enumerate(as_completed(futures), 1):
            pdf = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(pdfs)}] #{submission_label(pdf)} {pdf.name} — "
                      f"ERROR DE PROCESAMIENTO, queda marcado en el reporte ({exc})")
                results.append(error_paper(pdf, exc))
                continue
            flagged = sum(
                1 for r in res["references"]
                if r["verdict"]["status"] in report.NEEDS_REVIEW
            )
            extra = f", {res['llm_rescued']} rescatadas por LLM" if res.get("llm_rescued") else ""
            warn = " — ⚠ revisar manualmente (no parece un paper)" \
                if res["doc_flags"]["needs_review"] else ""
            degraded = " — ⚠ extracción con fallback (GROBID falló)" \
                if res.get("extraction_note") else ""
            print(f"  [{i}/{len(pdfs)}] #{res['submission_id']} {pdf.name} — "
                  f"{len(res['references'])} refs, {flagged} por revisar{extra}{warn}{degraded}")
            results.append(res)

    def _sort_key(r: dict):
        sid = r["submission_id"]
        return (0, int(sid)) if sid.isdigit() else (1, sid)

    results.sort(key=_sort_key)

    overlaps = []
    if not args.no_overlap and len(pdfs) > 1:
        print("\nCruzando manuscritos por solapamiento textual…")
        try:
            labels = {p: submission_label(p) for p in pdfs}
            overlaps = dedupe.scan_batch(pdfs, labels=labels)
            print(f"  {len(overlaps)} pares por encima del umbral")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] el cruce de solapamiento falló ({exc}); "
                  "el reporte sigue sin esa sección, el resto del trabajo no se pierde")

    def _safe_render(label: str, fn, *fn_args) -> None:
        try:
            fn(*fn_args)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] no se pudo generar {label} ({exc})")

    # JSON primero: es el más simple de generar y el que menos se puede
    # permitir perder — de ahí se puede reconstruir todo lo demás a mano.
    _safe_render("resultados.json", report.render_json, results, outdir / "resultados.json")
    _safe_render("papers.csv", report.render_papers_csv, results, outdir / "papers.csv")
    _safe_render("referencias.csv", report.render_csv, results, outdir / "referencias.csv")
    _safe_render("reporte.html", report.render_html, results, overlaps, outdir / "reporte.html")

    total = sum(len(r["references"]) for r in results)
    flagged = sum(
        1 for r in results for x in r["references"]
        if x["verdict"]["status"] in report.NEEDS_REVIEW
    )
    n_errors = sum(1 for r in results if r.get("processing_error"))
    n_degraded = sum(1 for r in results if r.get("extraction_note"))
    extra_summary = ""
    if n_errors:
        extra_summary += f" · {n_errors} papers con error de procesamiento (ver dashboard)"
    if n_degraded:
        extra_summary += f" · {n_degraded} papers con extracción degradada (GROBID falló)"
    print(f"\n{total} referencias · {flagged} requieren revisión manual "
          f"({flagged / max(total,1):.0%}) · {time.time() - t0:.0f}s{extra_summary}")
    print(f"Reporte: {outdir / 'reporte.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
