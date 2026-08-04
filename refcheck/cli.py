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

from . import dedupe, llm_rescue, report
from .extract import extract_references
from .verify import Verifier


def process_paper(pdf: Path, verifier: Verifier, grobid_url: str,
                   ollama_client: llm_rescue.OllamaClient | None = None) -> dict:
    refs = extract_references(pdf, grobid_url)
    n_rescued = 0
    if ollama_client is not None:
        refs, n_rescued = llm_rescue.rescue_batch(refs, ollama_client)
    out = []
    for ref in refs:
        verdict = verifier.verify(ref)
        row = ref.to_dict()
        row["verdict"] = verdict.to_dict()
        out.append(row)
    return {"file": pdf.name, "path": str(pdf), "references": out, "llm_rescued": n_rescued}


def main() -> int:
    ap = argparse.ArgumentParser(description="Revisión de referencias, fase 1")
    ap.add_argument("input", help="carpeta con PDFs o un PDF suelto")
    ap.add_argument("--out", default="reporte", help="carpeta de salida")
    ap.add_argument("--grobid", default="http://localhost:8070")
    ap.add_argument("--cache", default="refcheck_cache.db")
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
    pdfs = sorted(src.glob("*.pdf")) if src.is_dir() else [src]
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
    results: list[dict] = []
    t0 = time.time()

    print(f"Procesando {len(pdfs)} manuscritos…\n")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_paper, p, verifier, args.grobid, ollama_client): p
            for p in pdfs
        }
        for i, fut in enumerate(as_completed(futures), 1):
            pdf = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(pdfs)}] {pdf.name} — error: {exc}")
                continue
            flagged = sum(
                1 for r in res["references"]
                if r["verdict"]["status"] in report.NEEDS_REVIEW
            )
            extra = f", {res['llm_rescued']} rescatadas por LLM" if res.get("llm_rescued") else ""
            print(f"  [{i}/{len(pdfs)}] {pdf.name} — "
                  f"{len(res['references'])} refs, {flagged} por revisar{extra}")
            results.append(res)

    results.sort(key=lambda r: r["file"])

    overlaps = []
    if not args.no_overlap and len(pdfs) > 1:
        print("\nCruzando manuscritos por solapamiento textual…")
        overlaps = dedupe.scan_batch(pdfs)
        print(f"  {len(overlaps)} pares por encima del umbral")

    report.render_html(results, overlaps, outdir / "reporte.html")
    report.render_csv(results, outdir / "referencias.csv")
    report.render_json(results, outdir / "resultados.json")

    total = sum(len(r["references"]) for r in results)
    flagged = sum(
        1 for r in results for x in r["references"]
        if x["verdict"]["status"] in report.NEEDS_REVIEW
    )
    print(f"\n{total} referencias · {flagged} requieren revisión manual "
          f"({flagged / max(total,1):.0%}) · {time.time() - t0:.0f}s")
    print(f"Reporte: {outdir / 'reporte.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
