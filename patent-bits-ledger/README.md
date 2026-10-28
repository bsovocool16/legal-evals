# Patent Bits Ledger

A forecasting ledger that scores predictors in bits (log loss) on outcomes of US patent examination that postdate their training data or do not yet exist when the forecast is made. It asks two questions about pending applications, both of which turn on legal judgment about an office action, and both of which the USPTO answers publicly a few weeks or months later:

- **A (allowance).** An applicant has just responded to a non-final rejection. Will the examiner's next action allow the claims (a notice of allowance, or an Ex parte Quayle action), rather than issue another rejection?
- **F (dependent-claim folds).** An examiner has just issued a non-final rejection. For each dependent claim it names, will the applicant's response write that claim's limitation into an independent claim, either by cancelling the dependent and adding its words to the independent claim or by rewriting the dependent in independent form?

The predictors here are Claude models given the file as it stood on the as-of date, with no tools. One of them, `v1-sonnet` (Claude Sonnet 5.5), is the anchor: the permanent reference version that every later version is compared with and must beat. The full rules are in `PROTOCOL.md`. Version 1 of the protocol and its code were timestamped at 08:21 UTC on 2026-10-08, before the first prediction at 09:00 UTC, and each amendment was timestamped before the data it governs. The `PROTOCOL.md` published here is the text as amended through Amendment 4, lightly edited, so it matches no stamp; the stamped original is withheld (see below).

## Scoring in bits against base rates

For an outcome y with forecast p, the score is -log2 p if the event happened and -log2(1 - p) if it did not; lower is better, and probabilities are clipped to [0.01, 0.99]. Log loss rewards honest probabilities in expectation, and it is hard to game: there is no threshold to sit on, and confident errors are expensive.

Every predictor is compared with baselines computed from a calibration stream (C) of pre-cutoff cases, which is never used to score a predictor:

- `base`: the calibration rate. For A, 108 of 240 responses were followed by an allowance (45 percent). For F, 78 of 1,987 named dependent claims were folded (4 percent).
- `rule`: a one-feature rule. For F, claims the examiner said would be allowable if rewritten in independent form were folded 25 times in 198 (13 percent), against 53 in 1,789 for the others (3 percent). For A, the corresponding cell had only 14 units, too few, so the A rule equals `base`.
- For the live stream, a running rate and a logistic recalibration of the anchor model are added once 150 live outcomes exist.

Results are reported as the mean paired difference in bits from the baseline, with 95 percent intervals from a cluster bootstrap that resamples whole units (10,000 draws, fixed seed). A challenger replaces the anchor model only if, over at least 300 fresh A units (or 100 F units) scored after it was frozen, its one-sided 95 percent upper bound on the paired difference is below zero.

## Two streams

- **B (time-gated backfill).** Cases from the existing record whose outcomes fall after the predictors' training-data cutoff (June 2026 for the models used). Documents dated after an evaluation date are ignored. Backfill outcomes are public, so B slices can be used for development, but a slice used for tuning never counts toward confirming the version tuned on it.
- **P (weekly live batches).** Every Thursday, 60 responses and 60 rejections that are still pending are drawn from the USPTO listings by a fixed hash order, predicted within two days, committed and timestamped. These outcomes do not exist yet when the forecasts are made, so the stream cannot leak by construction. Its role is to check the backfill results: if a version's advantage over `base` is larger on B than on P (one-sided 95 percent bootstrap), its B results are flagged as possibly leaked or overfit.

Batch names give the stream (B backfill, P live, C calibration), then either the target (A or F), followed for backfill by the slice (`dev` or `2`), or, for live batches, which hold both targets, the batch day (for example `C-F`, `B-A-dev`, `P-2026-10-22`). A `.discarded-a1` suffix marks a calibration run discarded under Amendment 1.

Selection, packet building and outcome labelling are mechanical. Packets contain only documents dated on or before the as-of date, from a whitelist of sources, and the code refuses anything else. Outcomes are read from the file wrapper; a fold is labelled by a word-overlap rule between the dependent claim's limitation and the text the response inserted into an independent claim (`patent_bits/bits.py`).

## Timestamping

Each freeze point (the protocol and its code, each predictor version, and each batch's predictions) is committed and then timestamped by an independent RFC 3161 authority (freetsa.org). The SHA-256 of each file is written to `stamps/<name>/files.sha256`, that listing is sent to the authority, and the request, the signed reply and a summary are kept in the stamp folder. The authority's certificates are in `tsa/`. The signed time comes from the reply itself, not from the summary file, and it fixes when the forecasts existed. A live batch whose predictions are not stamped within two days of its batch day is void, and an outcome dated on or before the stamp is excluded.

To check every stamp:

```sh
python3 verify_stamps.py
```

or one stamp by hand:

```sh
openssl ts -verify -data stamps/P-2026-10-22-predictions/files.sha256 \
  -in stamps/P-2026-10-22-predictions/response.tsr \
  -CAfile tsa/freetsa-cacert.pem -untrusted tsa/freetsa-tsa.crt
cd stamped/P-2026-10-22-predictions && sha256sum -c ../../stamps/P-2026-10-22-predictions/files.sha256
```

### What is stamped, and what is published

`stamped/<stamp name>/` holds byte-identical copies of the stamped files, at the paths they had when they were stamped, so `sha256sum -c` works from inside that folder. All 17 signatures verify (`verify_stamps.py`, checked 2026-10-28).

| Stamps | Files | Published here |
|---|---|---|
| `B-A-dev-`, `B-F-dev-`, `B-A-2-`, `B-F-2-predictions`, `P-2026-10-08-`, `P-2026-10-15-`, `P-2026-10-22-predictions` | every prediction record of each batch | all, byte-identical; every file checks |
| `versions-v1`, `versions-v2` | the frozen predictor specifications | all, byte-identical |
| `sleep-lessons` | the two lesson lists of the Amendment 4 experiment | all, byte-identical |
| `protocol-v1`, `-a1`, `-a1c`, `-a1-code1`, `-a2`, `-a4` | the protocol, the code that implements it, its tests and the authority's certificates | the code, tests and certificates, byte-identical (6 of 8 files each). Withheld: the stamped `PROTOCOL.md` and the USPTO client `odp.py`, because they contain internal project references and configuration paths |
| `sleep-arms` | working files of the Amendment 4 experiment | none (model call transcripts and intermediate outputs) |

The rest of the folder is for reading and running, and is not byte-identical to anything stamped:

- `PROTOCOL.md`: the protocol as amended through Amendment 4, edited only to remove internal project references.
- `patent_bits/`: the ledger code adapted to this folder's layout (import paths, folder constants, and the API key read from `USPTO_API_KEY`). The logic is that of the stamped `protocol-v1-a4` code. Stamp lookups in this copy use the new paths, so it cannot recompute a batch's timestamp from the original listings; use `verify_stamps.py` for that.
- `tests/test_patent_bits.py`: the tests (48, no network), with the import paths adapted.
- `ledger/<batch>/`: the committed records of each batch (pool summary, selection log, manifest, predictions, outcomes and scores). `C-A.discarded-a1` and `C-F.discarded-a1` are the first calibration runs, discarded under Amendment 1 before any use and kept for the record. Packets, file-wrapper snapshots and full pools are not published.
- `versions/`, `calibration.json`, `sleep/`: frozen predictor specifications, the baseline constants, and the Amendment 4 lessons and recalibration constants.

## Results to date

Taken from the committed `ledger/<batch>/scores.json` files. Bits are per response (A) or per scored claim (F); a negative difference means fewer bits than the baseline, which is better. Intervals are 95 percent cluster-bootstrap intervals of the paired difference.

**B-A-dev (backfill, allowance).** 84 scored responses, 51 percent allowed, 77-day horizon.

| Predictor | Bits per response | Difference from `base` |
|---|---|---|
| `base` | 1.011 | |
| Claude Sonnet 5.5 (`v1-sonnet`, the anchor) | 0.837 | -0.174 (-0.306 to -0.034) |
| Claude Opus 5.5 (`v1-opus`) | 0.801 | -0.209 (-0.369 to -0.035) |

Opus minus Sonnet: -0.036 (-0.132 to +0.066). Both models under-forecast allowance in the middle probability bins.

**B-F-dev (backfill, folds; series 1).** 69 scored rejections, 919 scored claims, 4.5 percent folded.

| Predictor | Bits per claim | Difference from `base` |
|---|---|---|
| `base` | 0.264 | |
| `rule` | 0.254 | |
| `v1-sonnet` | 0.234 | -0.030 (-0.064 to -0.002) |
| `v1-opus` | 0.232 | -0.032 (-0.092 to +0.027) |

Two Opus units are excluded as infrastructure failures (the CLI served a different model than the one named), and one Opus answer written as prose instead of JSON scores 0.5 on each of its 18 claims.

**B-A-2 (descriptive slice for the Amendment 4 experiment).** 68 scored responses, 62 percent allowed, 64-day horizon. Amendment 4 tested whether written forecasting lessons, distilled from the B-A-dev cases and their outcomes, improve the anchor (`v2-sonnet-lessons`), against the same procedure run on shuffled outcomes (`v2-sonnet-shuffled`) and a plain logistic recalibration of the anchor fitted on B-A-dev (`recal-dev`).

| Predictor | Bits per response | Difference from `base` |
|---|---|---|
| `base` | 1.041 | |
| `v1-sonnet` | 0.929 | -0.112 (-0.259 to +0.038) |
| `v1-opus` | 1.115 | +0.073 (-0.152 to +0.310) |
| `v2-sonnet-lessons` | 1.064 | +0.023 (-0.175 to +0.224) |
| `v2-sonnet-shuffled` | 1.013 | -0.028 (-0.211 to +0.160) |
| `recal-dev` | 0.872 | -0.169 (-0.313 to -0.019) |

Lessons minus the anchor: +0.135 (+0.040 to +0.236), so the lessons made the anchor worse on this slice; recalibration minus the anchor: -0.057 (-0.109 to -0.003). The slice's outcomes fall in the same weeks as the training cases, so it can reflect period effects, and by design it does not count toward confirmation. The registered confirmation of the lessons version is on live A units from P-2026-10-15 onward.

**Not yet resolved.** The fold labeller was audited out of sample on B-F-dev: all 19 folds it labelled were correct, but it found only 19 of the 31 folds the readers saw (the readers were Claude agents, not people). Amendment 2 therefore starts a second F series with a higher-recall rule, to be fixed by Amendment 3 before any series-2 outcome is read; the backfill slice B-F-2 is predicted and stamped but held unresolved until then. The live batches P-2026-10-08, P-2026-10-15 and P-2026-10-22 (120 units each) are predicted and stamped; as of the last resolution run (2026-10-22) no live outcome was final. Further weekly batches are not included here. Live results, and therefore the leakage check, are still to come.

## Limits

Bits measure prediction, not the quality of an amendment or an argument. The live frame excludes responses acted on within about three weeks and rejections answered within a week, so the population is "not quick". The fold rule counts only claims cancelled and written into an independent claim, or rewritten in independent form. The Claude Code CLI version is recorded with every prediction but not frozen. Sixty units per target per week make single batches noisy; decisions use pooled units. `PROTOCOL.md` lists these and the other limits recorded in advance.
