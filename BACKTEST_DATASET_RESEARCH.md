# Historical dataset review: early Solana rug-pull benchmark

## Executive finding

A public, labeled research cohort is available for **Early Rug Pull Detection on Solana**:

- Paper: https://arxiv.org/abs/2611.13342
- Authors' code and released CSVs: https://github.com/Saba-Heydaridoost/rugpull_detector
- Underlying SolRPDS dataset: https://huggingface.co/datasets/DeFiLab/SolRPDS
- Research protocol and data-availability statement: https://hub.valency.io/works/W_qjyrc47u

This is a credible source of later outcome labels, but it is **not itself a 15-slot feature dataset**. The valid experiment is to reconstruct the first 15 slots independently, then join those early features to the later outcome labels. The q1 feature measurements must not be passed to this radar as predictors.

## q1 cohort audit

The released CSVs were inspected directly from the authors' repository on 2026-11-26:

- Label file: 2,700 q1 rows; 1,962 have VALID status (765 positive, 1,197 negative). The remaining rows are not valid binary labels (for example, UNKNOWN or IN_WINDOW_EVENT).
- Feature file: 1,961 q1 rows, with 765 positive and 1,196 negative; one valid label has no matching feature row.
- Feature cohort creation-time range: 2022-09-15 18:05:48 UTC through 2024-10-31 14:21:40 UTC.
- q1 feature cutoff: 750 seconds (12.5 minutes) after creation.

The released q1 features include creator, mint/freeze authority, buy/sell-volume changes, unique-wallet counts and liquidity snapshots. They do not supply the exact first-15-slot holder distribution, top-10 concentration, LP burn/lock proof, slot-0 funder graph, or verified Jito bundle membership required by this radar's event schema.

## Label validity and look-ahead control

The paper's operational positive label requires a liquidity decline greater than 80%, a subsequent price collapse greater than 90%, and persistent liquidity depletion for 30 minutes within a nominal 24-hour outcome horizon. Samples whose rug event is already visible by the relevant observation cutoff are excluded. These labels describe a subsequent market outcome; they are not proof of criminal intent. Slow rugs and freeze-only attacks can fall outside this definition.

This creates a useful, but carefully bounded, research opportunity:

1. Use the q1 CSV only to obtain token identifiers, creation metadata and the independently assigned later outcome labels.
2. Reconstruct each mint's launch and pool-creation slots from historical chain records.
3. Build predictor fields using only records with slot <= launch slot + 15. Do not use q1 volume, wallet-count, liquidity, or graph features as 15-slot inputs.
4. Attach the q1 VALID label as the later outcome, after verifying that the label protocol excludes events already visible during q1. Since q1's cutoff is 750 seconds after creation, it is later than the 15-slot decision window; nevertheless, confirm creation/pool timing and exclude any case whose actual rug event or relevant outcome evidence occurs inside the 15-slot feature interval.
5. Treat unknown and in-window labels as excluded, not as negatives. Keep the one valid label without a feature row out unless its token identifier can be independently matched without ambiguity.
6. Split chronologically by launch slot, keeping launches in the same slot in one partition. Fit any calibration only on the training partition and report untouched holdout metrics.

The q1 cohort therefore supports a **potential label join**, not a completed backtest. It must not be described as evidence that this scorer already achieves any particular precision, recall, AUC, or calibration.

## Historical transaction access: practical assessment

### 1. Old Faithful is the strongest public reconstruction route, with an important access detail

The Yellowstone Old Faithful project describes a genesis-to-tip Solana block archive. Triton publishes the OF1 epoch files at https://files.old-faithful.net, and the project provides a standard JSON-RPC/gRPC server over archived blocks:

- Overview: https://docs.old-faithful.net/
- OF1 file layout: https://docs.old-faithful.net/references/of1-files
- RPC server and HTTP-backed CAR configuration: https://docs.old-faithful.net/usage/rpc-server
- Sourcing data: https://docs.old-faithful.net/running-old-faithful/sourcing-data
- Triton archive: https://files.old-faithful.net
- Example CAR extraction tooling: https://github.com/yuno-research/solana_car

**Do not download an entire epoch CAR by default.** An Old Faithful epoch folder publishes a CAR plus several indexes, including slot-to-CID, CID-to-offset/size and transaction/signature indexes. Its RPC server accepts HTTP URLs for CAR and index files, provided the hosting endpoint supports HTTP Range requests. That creates a better pilot route: configure the RPC server against the remote epoch CAR and its matching indexes, then request only the target slots/transactions through RPC. This is still an integration task, not a guaranteed public hosted RPC endpoint; it depends on the remote files and range requests being reachable and the matching indexes being available.

The archive is large enough that bulk processing is not a sensible first step. The official documentation recommends local high-speed index storage for performance. Importantly, the RPC documentation says indexes can be remote over HTTP, but the GSFA index required for full `getSignaturesForAddress` support cannot currently be used remotely; remote-index operation therefore has a limitation for address-history discovery. The documentation also describes a signatures-only mode for remote GSFA usage, which must be tested against the exact methods needed by this collector. Do not assume a remote CAR plus remote indexes automatically provides every Solana RPC method. The pilot should therefore:
1. Resolve a handful of q1 token creation timestamps to candidate slots and epochs.
2. Check that each epoch folder exposes the CAR and the exact matching indexes.
3. Verify HTTP Range support for the CAR and index URLs.
4. Configure/build `faithful-cli rpc` for only those epochs, with remote CAR/index URIs where supported.
5. Fetch a few known slots and transactions, checking block slot, signatures, transaction metadata and completeness.
6. Only then scale to a bounded sample of labeled mints.

**Method-coverage caveat:** Old Faithful's published RPC page explicitly says the GSFA index required for `getSignaturesForAddress` cannot currently be used remotely. The first pilot should prioritize slot-addressable `getBlock` retrieval, and separately test whether the collector's address-history discovery can be served by a locally generated GSFA index or a compatible signatures-only setup. If the required account-signature scans cannot be supported, the reconstruction design must be changed to enumerate transactions by slot over a bounded window or use another archive/index service. A successful HTTP Range test alone is not proof that the needed RPC methods work.

If remote range access or required indexes are unavailable, the fallback is a local epoch CAR download and index build, which may require substantial disk space. Do not download the full archive for this bounty.

### 2. Solarchive is useful, but its currently published transaction coverage is not enough for this cohort

Solarchive offers free Parquet exports and CC BY 4.0 licensing. Its current historical-readiness table shows transaction partitions for October 2020 through March 2022 and again for November-December 2025, while the audited q1 cohort begins in September 2022 and ends in October 2024. It currently shows account/token snapshots (not transaction partitions) for the intervening months. Those snapshots do not replace transaction-level launch reconstruction.

- Project and download instructions: https://solarchive.org/
- Historical-readiness table: https://solarchive.org/search.php
- Dataset README: https://huggingface.co/datasets/solarchive/solarchive

There is a discrepancy between the broad coverage wording in the Hugging Face README and the month-by-month transaction readiness table on the project site. For this experiment, treat a date as available only after checking the actual daily transaction index and confirming that the needed transaction files exist. Do not assume the advertised full-history coverage means every 2022-2024 transaction partition is currently downloadable.

### 3. Ordinary RPC must be tested for archive depth and completeness

Solana's official guidance warns that providers differ in retained history, request limits and method support; `getFirstAvailableBlock` alone does not prove that every historical transaction is retrievable. For an RPC-based reconstruction, verify known old signatures and blocks across the entire target date range, and detect missing/null responses rather than silently treating them as empty history.

- Solana provider-selection guidance: https://solana.com/docs/rpc/providers
- `getBlock` method and transaction-version requirements: https://solana.com/developers/docs/rpc/http/getblock
- Archive RPC method overview: https://www.alchemy.com/overviews/solana-archival-data

A paid archive service is not assumed or required by this report. Do not purchase a plan or create a provider account without the user's explicit approval.

## Reconstruction design

For each valid q1 mint:

- Resolve the mint initialization transaction and slot; independently resolve the first relevant pool initialization transaction and slot.
- Define the decision anchor explicitly (the bounty's launch/pool event convention must be applied consistently), then cap every predictor query at anchor slot + 15.
- Recover full transaction metadata, pre/post token balances, account keys, inner instructions and logs for the bounded slots.
- Derive only fields supported by the recovered records: authority state, holder distribution and top-holder concentration where a complete holder snapshot is available, early buy/sell and wallet metrics, slot-0 funder relationships, and LP-mint/burn/lock evidence where the relevant program instruction and vault state can be verified.
- Keep Jito bundle membership unknown unless a provider supplies verifiable bundle metadata. Do not infer it from same-slot activity or a shared funder.
- Keep LP lock/burn evidence unknown when the transaction history or vault/supply accounting is incomplete. A lack of observed lock instructions is not proof that liquidity is unlocked.
- Record per-token coverage diagnostics: requested slot range, blocks found, transaction count, failed/missing block count, decoder coverage, and which scorer fields remain unavailable.

A token should be excluded from the primary metric cohort if the launch transaction, pool transaction, any required block in the 15-slot window, or required state evidence cannot be reconstructed reliably. Report exclusions and reasons; do not silently impute missing chain history as benign behavior.

## Backtest protocol and reporting

Once the reconstruction is complete:

1. Freeze the feature schema and scorer version before evaluating holdout data.
2. Deduplicate mints and keep same-slot launches together.
3. Use a chronological 70/30 train/holdout split by launch slot; never randomly mix future launches into training.
4. If calibration is fitted, fit it only on training labels and apply the frozen calibration to holdout predictions.
5. Report cohort counts, positive prevalence, coverage/exclusion reasons, confusion matrix, precision, recall, F1, Brier score, expected calibration error, ROC AUC and average precision. Include threshold-selection methodology and confidence intervals where feasible.
6. Compare against simple baselines (constant-prevalence predictor and a transparent authority-only or concentration-only baseline) so the scorer's incremental value is visible.
7. State clearly that the label is a market-outcome proxy and that performance on this cohort does not establish detection of every kind of fraud.

## Provenance, licensing and caveats

- The SolRPDS source is identified as CC BY 4.0: https://huggingface.co/datasets/DeFiLab/SolRPDS
- The authors' repository publishes the label and feature CSVs, but no explicit top-level LICENSE file was found during this review. Do not redistribute those CSVs inside this project unless the authors' data terms and upstream provider restrictions are clarified.
- Old Faithful's public archive is a data-access route, not a ready-made labeled benchmark. Respect its project/data terms and cite the archive if used.
- The paper's own data-availability statement says edge-level data and processed EDA artifacts are not included in the repository at present; the external SolRPDS data must be obtained separately.

**Current project status (2026-11-26):** a labeled research cohort and a technically plausible archive route have been identified. The q1 labels can potentially supervise a new first-15-slot reconstruction, but no first-15-slot rows have yet been reconstructed and no real holdout metrics have been produced. The latest documentation-only commit for this report passed the repository's GitHub Actions workflow; that CI result verifies the repository checks, not historical-data coverage or model performance. The scorer must continue to be described as heuristic until the reconstruction and holdout experiment are actually run.
