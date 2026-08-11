#!/usr/bin/env python3
"""Anonimator baseline adapter (attested, maintainer-produced row).

Shells out to the installed, proprietary Anonimator CLI (`python -m
anonymize.cli`) via `subprocess` -- never imports it (see
`tests/test_no_product_import.py`). The harness has no dependency on this
adapter; if the product venv is not found this script exits with a clear
message rather than failing silently or falling back to anything else.

Two run modes, both driving the exact same CLI/engine, both producing the
same predictions contract:

- `--mode daemon` (default): starts `anonymize.cli daemon --ner-engine
  kpwr-n82`, which loads the transformer model **once** and then serves one
  `detect` command per document over stdin/stdout. This amortizes the
  ~10s/doc model-load cost that a fresh `detect` subprocess would otherwise
  pay per document (~340 docs in the full corpus), without touching
  `cli.py` or importing any product code -- it is the CLI's own documented
  batch-serving mode. Verified against a single-shot
  `detect --predictions-output` run to confirm the daemon path and the flag
  path agree on content (see the harness run notes in results/).
- `--mode percall`: the original one-`detect`-subprocess-per-document path
  using `--predictions-output` directly. Slow (~10s/doc, all of it model
  reload) but the most literal reproduction of the CLI's own predictions
  export. Kept for spot-checking / small runs.

Label normalization (this adapter's own responsibility, not cli.py's):
entity `type` values read from the CLI's own on-disk entities JSON (or,
in `percall` mode, from the `--predictions-output` JSONL) are mapped to the
17 public labels. This is the same string-to-string rename table that
cli.py's internal `_pred_label_map` applies for `--predictions-output` --
not a reimplementation of any detection logic, and not a modification of
`cli.py`.

The one entry worth calling out is `GPE -> LOC`. The kpwr-n82 tagset
distinguishes `nam_loc_gpe_*` (city/settlement names, canonicalized
internally to `GPE`) from other `nam_loc_*`/`nam_fac_*` spans (canonicalized
to `LOC`) -- see `ner_engines.canonicalize_kpwr_label` -- while the corpus
has a single public `LOC` label, so both must fold into it. `cli.py` used to
be missing the `GPE` key, which silently dropped every model-detected
city/settlement name from `--predictions-output`; that was fixed product-side
in 2430d5c (2026-07-23), so `daemon` mode (entities JSON, mapped here) and
`percall` mode (the CLI's own export) now agree on LOC. Keeping the key in
this table is what makes the adapter correct against product builds on either
side of that fix, including released sidecar binaries.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.loader import load_corpus, TRACKED_LABELS

# This repo (pl-pii-bench) is a sibling of products/ inside the desktop
# monorepo, same convention as scripts/sync-corpus.sh.
DEFAULT_PRODUCT_REPO = (Path(__file__).resolve().parent.parent / ".." / "products" / "anonimator").resolve()

# Adapter-owned normalization: CLI/engine internal entity `type` -> public
# label. Mirrors cli.py's own `_pred_label_map`; see module docstring on the
# GPE -> LOC entry.
ENTITY_TYPE_TO_PUBLIC = {
    "PER": "PERSON",
    "persName": "PERSON",
    "ORG": "ORG",
    "orgName": "ORG",
    "LOC": "LOC",
    "GPE": "LOC",  # kpwr-n82 nam_loc_gpe_* (city/settlement names); see docstring.
    "PESEL": "PESEL",
    "NIP": "NIP",
    "REGON": "REGON",
    "DOWOD": "DOWOD",
    "IBAN": "IBAN",
    "POSTAL": "POSTAL",
    "DOB": "DOB",
    "PHONE": "PHONE",
    "EMAIL": "EMAIL",
    "PASSPORT": "PASSPORT",
    "DRIVING_LICENSE": "DRIVING_LICENSE",
    "PAYMENT_CARD": "PAYMENT_CARD",
    "VIN": "VIN",
    "VEHICLE_PLATE": "VEHICLE_PLATE",
}

# The same normalization, used against the label spelling that appears in
# cli.py's own `--predictions-output` JSONL `label` field (percall mode reads
# that file, not entities JSON -- cli.py has already applied its own map by
# then, so these keys are mostly a pass-through and exist to keep the two
# modes on one code path).
CLI_PREDICTIONS_LABEL_TO_PUBLIC = dict(ENTITY_TYPE_TO_PUBLIC)


def _default_python(product_repo: Path) -> Path:
    return product_repo / "toolkit" / "packages" / "py-anonymize" / ".venv" / "bin" / "python"


def _default_model_path(product_repo: Path) -> Path:
    return product_repo / "bench" / "models" / "ner-candidates" / "kpwr-n82"


def _require_paths(python_path: Path, model_path: Path) -> None:
    if not python_path.exists():
        print(
            f"error: this adapter requires the proprietary Anonimator product venv; "
            f"no python found at {python_path}. The pl-pii-bench harness itself has no "
            f"dependency on it -- this row is attested by the maintainer and reproducible "
            f"only by someone with the product installed.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not model_path.exists():
        print(f"error: kpwr-n82 model path not found: {model_path}", file=sys.stderr)
        sys.exit(1)


def _entity_to_span(ent: dict, extracted_text: str) -> dict | None:
    public_label = ENTITY_TYPE_TO_PUBLIC.get(ent.get("type"))
    if public_label is None or public_label not in TRACKED_LABELS:
        return None
    start = ent.get("span_start")
    end = ent.get("span_end")
    text = ent.get("original") or ""
    if not text and extracted_text and start is not None and end is not None:
        text = extracted_text[start:end]
    return {"label": public_label, "start": start, "end": end, "text": text}


def write_predictions(docs, out_path: Path, predict) -> None:
    """Write exactly one prediction envelope for each normalized document."""
    with out_path.open("w", encoding="utf-8") as fh:
        for i, doc in enumerate(docs, start=1):
            print(f"  [{i}/{len(docs)}] {doc.doc_id}", file=sys.stderr)
            spans = predict(doc)
            fh.write(
                json.dumps(
                    {"doc_id": doc.doc_id, "lane": doc.lane, "spans": spans},
                    ensure_ascii=False,
                )
                + "\n"
            )


# --------------------------------------------------------------------------
# Daemon mode: one long-lived `anonymize.cli daemon` process, one
# detect-command round-trip per document.
# --------------------------------------------------------------------------

class DaemonError(RuntimeError):
    pass


class AnonimatorDaemon:
    def __init__(self, python_path: Path, product_repo: Path, ner_engine: str, model_path: Path, log_path: Path):
        import os
        merged_env = dict(os.environ)
        merged_env["ANONIMIZE_KPWR_N82_MODEL_PATH"] = str(model_path)

        self._log_fh = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["arch", "-arm64", str(python_path), "-m", "anonymize.cli", "daemon", "--ner-engine", ner_engine],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log_fh,
            text=True,
            bufsize=1,
            cwd=str(product_repo),
            env=merged_env,
        )
        self._q: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)  # sentinel: stdout closed

    def _read_event(self, timeout: float):
        try:
            line = self._q.get(timeout=timeout)
        except queue.Empty:
            raise DaemonError("timed out waiting for daemon output")
        if line is None:
            raise DaemonError("daemon stdout closed unexpectedly (see log)")
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def wait_ready(self, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            obj = self._read_event(max(1.0, deadline - time.time()))
            if obj is not None and obj.get("event") == "daemon_ready":
                return
        raise DaemonError("daemon did not report ready in time")

    def detect(self, cmd_id: str, text: str, entities_path: Path, timeout: float = 60.0) -> dict | None:
        """Runs one detect command, returns the parsed entities JSON dict, or
        None on error (with a warning printed to stderr)."""
        cmd = {"command": "detect", "id": cmd_id, "direct_text": text, "entities_path": str(entities_path)}
        self.proc.stdin.write(json.dumps(cmd) + "\n")
        self.proc.stdin.flush()

        deadline = time.time() + timeout
        error_seen = None
        while time.time() < deadline:
            obj = self._read_event(max(1.0, deadline - time.time()))
            if obj is None:
                continue
            event = obj.get("event")
            if event == "error":
                error_seen = obj.get("message")
            elif event == "command_complete" and obj.get("id") == cmd_id:
                if error_seen:
                    print(f"warning: {cmd_id}: daemon reported error: {error_seen}", file=sys.stderr)
                    return None
                if not entities_path.exists():
                    print(f"warning: {cmd_id}: daemon completed but wrote no entities file", file=sys.stderr)
                    return None
                return json.loads(entities_path.read_text(encoding="utf-8"))
        raise DaemonError(f"{cmd_id}: timed out waiting for command_complete")

    def shutdown(self) -> None:
        try:
            self.proc.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        finally:
            self._log_fh.close()


def run_daemon_mode(docs, python_path: Path, product_repo: Path, ner_engine: str, model_path: Path, out_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pl-pii-bench-anonimator-") as tmp:
        tmp_dir = Path(tmp)
        log_path = tmp_dir / "daemon-stderr.log"
        print(f"Starting anonimator daemon (ner_engine={ner_engine}) ...", file=sys.stderr)
        daemon = AnonimatorDaemon(python_path, product_repo, ner_engine, model_path, log_path)
        t0 = time.time()
        try:
            daemon.wait_ready()
        except DaemonError as e:
            print(f"error: daemon failed to start: {e}\n--- log tail ---", file=sys.stderr)
            print(log_path.read_text(encoding="utf-8")[-4000:], file=sys.stderr)
            sys.exit(1)
        print(f"Daemon ready after {time.time() - t0:.1f}s", file=sys.stderr)

        entities_path = tmp_dir / "entities.json"
        n_ok, n_err = 0, 0
        with out_path.open("w", encoding="utf-8") as fh:
            for i, doc in enumerate(docs, start=1):
                if i % 25 == 0 or i == 1 or i == len(docs):
                    print(f"  [{i}/{len(docs)}] {doc.doc_id}", file=sys.stderr)
                try:
                    data = daemon.detect(doc.doc_id, doc.text, entities_path, timeout=90.0)
                except DaemonError as e:
                    print(f"warning: {doc.doc_id}: {e}; restarting daemon", file=sys.stderr)
                    daemon.shutdown()
                    daemon = AnonimatorDaemon(python_path, product_repo, ner_engine, model_path, log_path)
                    daemon.wait_ready()
                    data = None
                if data is None:
                    n_err += 1
                    fh.write(json.dumps({"doc_id": doc.doc_id, "lane": doc.lane, "spans": []}, ensure_ascii=False) + "\n")
                    continue
                n_ok += 1
                extracted = data.get("extracted_markdown", "")
                spans = []
                for ent in data.get("entities", []):
                    span = _entity_to_span(ent, extracted)
                    if span is not None:
                        spans.append(span)
                fh.write(json.dumps({"doc_id": doc.doc_id, "lane": doc.lane, "spans": spans}, ensure_ascii=False) + "\n")
                try:
                    entities_path.unlink()
                except OSError:
                    pass

        daemon.shutdown()
        print(f"Wrote {len(docs)} documents to {out_path} ({n_ok} ok, {n_err} failed)")


# --------------------------------------------------------------------------
# Percall mode: one `detect --predictions-output` subprocess per document
# (the literal, slow reproduction of the CLI's own export feature).
# --------------------------------------------------------------------------

def run_one_document_percall(python_path: Path, product_repo: Path, doc_id: str, text: str, ner_engine: str, model_path: Path, tmp_dir: Path) -> list[dict]:
    import os
    input_path = tmp_dir / f"{doc_id}.txt"
    input_path.write_text(text, encoding="utf-8")
    predictions_path = tmp_dir / f"{doc_id}.predictions.jsonl"

    env = dict(os.environ)
    env["ANONIMIZE_KPWR_N82_MODEL_PATH"] = str(model_path)

    result = subprocess.run(
        [
            "arch", "-arm64", str(python_path), "-m", "anonymize.cli", "detect", str(input_path),
            "--ner-engine", ner_engine,
            "--predictions-output", str(predictions_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(product_repo),
        env=env,
    )
    if result.returncode != 0:
        print(f"warning: {doc_id}: anonymize.cli detect failed: {result.stderr.strip()[-2000:]}", file=sys.stderr)
        return []

    if not predictions_path.exists():
        return []

    spans = []
    for line in predictions_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        public_label = CLI_PREDICTIONS_LABEL_TO_PUBLIC.get(row.get("label"))
        if public_label is None or public_label not in TRACKED_LABELS:
            continue
        spans.append({
            "label": public_label,
            "start": row.get("start"),
            "end": row.get("end"),
            "text": row.get("text", ""),
        })
    return spans


def run_percall_mode(docs, python_path: Path, product_repo: Path, ner_engine: str, model_path: Path, out_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pl-pii-bench-anonimator-") as tmp:
        tmp_dir = Path(tmp)
        write_predictions(
            docs,
            out_path,
            lambda doc: run_one_document_percall(
                python_path,
                product_repo,
                doc.doc_id,
                doc.text,
                ner_engine,
                model_path,
                tmp_dir,
            ),
        )
    print(f"Wrote {len(docs)} documents to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonimator baseline adapter (requires proprietary product venv)")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lanes", nargs="*", default=None)
    parser.add_argument("--ner-engine", default="kpwr-n82")
    parser.add_argument("--product-repo", type=Path, default=DEFAULT_PRODUCT_REPO, help="Path to the products/anonimator checkout")
    parser.add_argument("--python", type=Path, default=None, help="Path to the product venv's python (default: <product-repo>/toolkit/packages/py-anonymize/.venv/bin/python)")
    parser.add_argument("--model-path", type=Path, default=None, help="kpwr-n82 model path (default: <product-repo>/bench/models/ner-candidates/kpwr-n82)")
    parser.add_argument("--mode", choices=["daemon", "percall"], default="daemon")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N loaded documents (debugging)")
    args = parser.parse_args()

    product_repo = args.product_repo.resolve()
    # NB: python_path is intentionally NOT fully resolved (realpath) -- the
    # venv's bin/python is a symlink into a bare interpreter outside the
    # venv, and Python's venv activation (pyvenv.cfg discovery) keys off the
    # invoked path's directory, not the symlink target. Resolving it here
    # would silently drop us out of the venv (and lose the `anonymize`
    # package installed into it).
    python_path = args.python or _default_python(product_repo)
    model_path = (args.model_path or _default_model_path(product_repo)).resolve()
    _require_paths(python_path, model_path)

    lanes = set(args.lanes) if args.lanes else None
    docs = [d for d in load_corpus(args.corpus, lanes=lanes) if d.text is not None]
    if args.limit is not None:
        docs = docs[: args.limit]

    print(f"product_repo={product_repo}", file=sys.stderr)
    print(f"python={python_path}", file=sys.stderr)
    print(f"model_path={model_path}", file=sys.stderr)
    print(f"ner_engine={args.ner_engine} mode={args.mode} docs={len(docs)}", file=sys.stderr)

    if args.mode == "daemon":
        run_daemon_mode(docs, python_path, product_repo, args.ner_engine, model_path, args.out)
    else:
        run_percall_mode(docs, python_path, product_repo, args.ner_engine, model_path, args.out)


if __name__ == "__main__":
    main()
