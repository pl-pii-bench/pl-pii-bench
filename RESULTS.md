# pl-pii-bench v1.0.0 public results

This summary is generated from the authoritative release manifest. It contains public split aggregates only. Predictions, detailed reports, reproduction commands, and maintainer paths are excluded.

| Split | Documents | Anonimator relaxed recall | Presidio relaxed recall | spaCy PL relaxed recall | GLiNER PII Polish relaxed recall | BardsAI EU PII relaxed recall |
|---|---:|---:|---:|---:|---:|---:|
| `core` | 33 | 99.5% | 95.8% | 64.2% | 58.0% | 69.7% |
| `inflection` | 441 | 100.0% | 96.4% | 96.4% | 97.3% | 37.0% |
| `identifiers` | 12 | 75.0% | 44.4% | 3.2% | 16.7% | 31.2% |
| `address` | 14 | 93.9% | 79.3% | 79.3% | 29.9% | 68.3% |
| `negative`* | 14 | 32 FP | 362 FP | 346 FP | 123 FP | 513 FP |
| `robustness` | 231 | 76.6% | 68.0% | 56.5% | 54.9% | 61.6% |
| `pdf` | 14 | 99.5% | 95.2% | 63.1% | 55.1% | 64.7% |

\* The `negative` split has no planted entities, so relaxed recall is undefined; the value shown is the false-positive count instead.

| Engine | Reproduction versions |
|---|---|
| Anonimator | version 0.1.0 |
| Presidio | presidio-analyzer 2.2.364, spaCy 3.8.14, pl_core_news_lg 3.8.0 |
| spaCy PL | spaCy 3.8.14, pl_core_news_lg 3.8.0 |
| GLiNER PII Polish | gliner n/a |
| BardsAI EU PII | transformers n/a, torch n/a, model `bardsai/eu-pii-anonimization-multilang-v2-preview` @ `8e0b19766bb0dd4916d096b4f540dd46c138c760` |

Source commit: `29b51c335b26454b5c9271df9eeb343dcd28a8d4`

Public artifact-set SHA-256: `62cedba703ee5197194a3d5db49a8114ea7875d0504060808135e85a00ab20a6`
