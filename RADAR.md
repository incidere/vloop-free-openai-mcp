# SPL launch radar: operation, data contract and evaluation

## Components

- `spl_rug_radar.py` is the dependency-free heuristic scorer. Keep this file under 8,192 bytes.
- `solana_ingest.py` is a read-only JSON-RPC/WebSocket adapter. It subscribes to SPL Token and Token-2022 mint logs plus explicitly configured DEX program IDs.
- `backtest.py` fits an isotonic calibration map on the earlier chronological partition and reports metrics on a later holdout.
- `calibrated_scorer.py` applies the generated calibration artifact to one JSON event on stdin.

## Live ingestion

Python 3.12; no pip packages or wallet are needed. A public RPC endpoint is the default, but a dedicated RPC provider is recommended because holder enumeration and historical block reads can be rate-limited.

```sh
python solana_ingest.py \
  --rpc https://api.mainnet-beta.solana.com \
  --ws wss://api.mainnet-beta.solana.com \
  --dex-program 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 \
  --dex-program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA \
  --dex-program YOUR_OTHER_DEX_PROGRAM_ID \
  --calibration calibration.json
```

The adapter includes binary decoders for Raydium AMM v4 `Initialize2` and PumpSwap `create_pool` (program ID `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`, discriminator and account order verified against the official PumpSwap IDL), in addition to parsed-instruction decoding for other venues. Parsed pool mint fields are required for generic venues; the parser excludes the LP share mint and does not guess pool assets from every token balance in a router transaction, avoiding false launch correlations. Repeat `--dex-program` for every additional venue. Add `--lock-program` for locker programs whose parsed lock instructions expose LP mint, amount and unlock timestamp; configured locker-program notifications are joined by LP mint after pool creation and re-scored while the launch remains inside the 15-slot window. Pass `--calibration calibration.json` after running the backtest to apply the trained isotonic map to live scores. Without that artifact, output remains explicitly heuristic. The adapter joins mint and pool notifications by mint address in either arrival order, accepts only pool events within the 15-slot launch window, and keeps bounded in-flight correlation state across WebSocket reconnects within the running process (not across process restarts). The adapter emits JSON lines to stdout. Pipe them to a file if you want to retain live observations.

The adapter intentionally refuses to infer LP lock verification from an address or an untrusted claim. A lock counts only when a configured locker program returns a parsed lock instruction with the LP mint, amount and a future unlock timestamp, and the named destination token account is independently confirmed to hold at least that amount of the same LP mint. Locker formats that do not expose a verifiable destination token account remain unverified. Burn verification is deliberately conservative: it requires an observed LP mint-to amount plus an explicit successful parsed `burn`/`burnChecked` instruction for that LP mint. The adapter subscribes to Token and Token-2022 burn logs and joins later burn transactions to the observed pool by LP mint, provided the burn is observed within the 15-slot launch window. A current-supply delta alone is never accepted, because later minting followed by burning can make that net delta ambiguous. Observed successful burn transactions are accumulated per LP mint (bounded to the most recent 64 transactions) so multiple partial burns are counted cumulatively during the running process. Burns outside the live observation window, before the pool correlation is established, or missed during disconnects may be undercounted. The SQLite journal persists observed burns and already-correlated pool context; pending mint-only or pool-only correlation entries are not persisted. Unknown or unsupported venue layouts remain unverified.

### Coverage and honest limitations

Solana's standard JSON-RPC does not expose a universal decoded DEX-pool schema. Configure the exact venue program IDs and use parsed transaction instructions; some venues require a venue-specific decoder. The generic adapter cannot safely decode every binary DEX instruction. It cannot infer Jito bundle membership from ordinary RPC logs alone; it records same-slot buyer/funder concentration and same-wallet round trips, and leaves the explicit Jito bundle count at zero unless a provider-specific adapter supplies verified bundle metadata. Token-2022 transfer-fee configuration and mutable token-metadata flags are extracted when the RPC provider exposes parsed extension state; the larger configured older/newer fee is reported conservatively. Missing extension fields remain unknown; textual boolean values are parsed explicitly (`false` is never treated as true), and out-of-range transfer-fee values are ignored. Thin-liquidity/FDV still requires venue-specific reserve and valuation decoding and is not fabricated when unavailable.

LP burn observations are persisted in a local SQLite journal (`lp_burn_journal.sqlite3` by default; override with `--burn-journal`). Successful parsed burn transactions are deduplicated by signature and retained up to the latest 64 per LP mint. The journal bounds restored launch/pool associations to 10,000 LP mints and removes burn rows for evicted associations. The journal also stores the correlated launch/pool context, allowing the adapter to restore both the LP association and burn observations after restart or WebSocket reconnect; restored burns are aggregated only within the launch's 15-slot window. Keep the journal file with the deployment if continuity across restarts is required; deleting it discards that history. The journal stores parsed transaction responses locally and should be treated as operational data.

The adapter is read-only: it never signs or sends transactions. It requires an RPC endpoint that supports `logsSubscribe`, `getTransaction`, `getBlock`, `getProgramAccounts`, `getTokenSupply`, and `getTokenLargestAccounts`.

## Historical backtest and calibration

Input is newline-delimited JSON. Each row contains the scorer's normalized event fields plus `label` (1 = confirmed rug under the chosen label definition, 0 = non-rug). Include only features available no later than 15 slots after launch. Do not include post-launch outcome features in the event object.

Example shape:

```json
{"mint":"...","launch_slot":100,"event_slot":112,"mint_authority_active":true,"freeze_authority_active":false,"holders":{"walletA":500,"walletB":250},"lp_burn_pct":0,"lp_burn_verified":false,"label":1}
```

Run:

```sh
python backtest.py historical_launches.jsonl --min-samples 50 --calibration calibration.json
```

Rows are sorted by launch slot; the first 70% are used to fit a pool-adjacent-violators isotonic map and the final 30% are a holdout. The report includes confusion counts, precision, recall, F1, Brier score, 10-bin ECE, tie-aware ROC AUC and average precision for raw and calibrated holdout predictions. The chronological split keeps every launch from the same slot on one side of the boundary, preventing same-slot launches from leaking across train and holdout; the resulting split is approximately 70/30. The generated `calibration.json` is applied with:

```sh
echo '{"mint":"...","launch_slot":100,"event_slot":110}' | python calibrated_scorer.py calibration.json
```

Do not publish a performance claim until the input file, label definition, sample size, date range, class balance and holdout metrics are included with the submission. Synthetic fixtures are for unit tests only and are not a historical backtest. SolRPDS labels inactivity/liquidity-pool outcomes, which are a proxy and not equivalent to manually confirmed fraud; document that distinction if you use it.

## Tests and size gate

```sh
python -B -m unittest discover -s . -v
python -B -c "from pathlib import Path; p=Path('spl_rug_radar.py'); assert p.stat().st_size <= 8192, p.stat().st_size; print(p.stat().st_size)"
```

## Bounded historical mint discovery

```sh
python historical_collect.py --days 1 --max-blocks 100 \
  --out historical_mint_candidates.jsonl \
  --checkpoint historical_mint_checkpoint.json
```

The collector timestamp-aligns the requested lookback. In its default bounded
mode (`--max-blocks 100`), it deterministically samples at most that many
candidate slot numbers and calls `getBlock` only for those slots; skipped
slots can reduce the number of actual blocks fetched. This avoids indexing
the entire slot range before applying the limit. It resumes from a checkpoint
and appends deduplicated mint-initialization candidates to JSONL.
Use `--max-blocks 0` only when you explicitly intend exhaustive discovery:
that mode enumerates every produced slot using `getBlocks` chunks and may
require hundreds of RPC requests for a one-day window, so public endpoints
may rate-limit or time out. Bounded sampling is not exhaustive and can miss
launches.

Each output row contains the mint, launch slot, block time, transaction
signature and an intentionally null label. This is discovery output only:
sampling can miss launches, and a mint initialization alone does not establish
a DEX pool, a rug, or a safe launch. Do not feed these rows directly into
`backtest.py`. The next stage must associate candidates with configured
pool-creation transactions, reconstruct features available within 15 slots,
and attach independently reviewed outcome labels. Preserve signatures and
label provenance for auditability.


## Old Faithful historical RPC capability probe

Old Faithful can serve archived Solana blocks through its `faithful-cli rpc` JSON-RPC server. Remote CAR and index URIs are supported when the hosting endpoint accepts HTTP Range requests. However, the official RPC documentation notes that the GSFA index needed for full `getSignaturesForAddress` support cannot currently be used remotely. The default server build also does not enable `jsonParsed`; that requires the documented `make jsonParsed-linux` build target. See https://docs.old-faithful.net/usage/rpc-server and https://github.com/rpcpool/yellowstone-faithful.

Once an Old Faithful-compatible endpoint is running, probe representative historical slots before attempting a dataset reconstruction:

```powershell
$env:OLD_FAITHFUL_RPC_URL = "http://127.0.0.1:8899"
py -3.12 faithful_probe.py --slot 150000000 --slot 160000000 --timeout 90
```

Or pass `--rpc` directly. Add `--address <PUBKEY>` only to diagnose address-history lookup; failure of that method does not prevent the bounded slot-scan path. The probe tests `getVersion`, `getFirstAvailableBlock`, `getSlot`, `getBlocks`, `getBlockTime`, `getBlock`, and a sample `getTransaction`. It first tries `jsonParsed`, then falls back to raw `json` and reports which encoding worked. Raw JSON availability means the historical block is retrievable, but it does not by itself provide parsed SPL instruction fields; raw instruction decoding is still required if `jsonParsed` is unavailable.

Probe at least one known slot from each target epoch, including a slot whose signature and transaction metadata have been independently checked. A null response, unsupported method, missing transaction metadata, or a successful sample from only one epoch is not evidence of complete history. Save the JSON output with the reconstruction audit. Do not start large-scale collection until block coverage and transaction metadata are verified across the cohort's full date range.
### CI and RPC rate limits

The regular `SPL radar checks` workflow runs syntax checks, unit tests, and the scorer-size limit only. The historical RPC capability probe is kept in its own manually dispatched workflow so it does not consume the same Helius request budget as a reconstruction run.

A recent concurrent probe recorded HTTP 429 responses for several historical samples. One sampled block was retrieved with complete transaction metadata; this is not evidence of general archive reliability. Run the manual probe separately and allow rate limits to clear before starting a reconstruction.
### Historical feature-row reconstruction

The reconstruction pilot now attempts to emit a scorer-compatible feature_rows array in its JSON report and a historical_features.jsonl file for the backtest runner. It reconstructs token-account balances from pre/post token-balance metadata in the produced slots after mint initialization, parses mint/freeze authority changes, observes LP mint/burn instructions when a configured pool decoder exposes the LP mint, and computes early-activity counters over the launch-to-15-slot window.

This is still an exploratory reconstruction, not yet a validated historical dataset. In particular:

- Holder balances are reconstructed only from token-account changes visible in the fetched launch window; accounts or balance changes omitted by RPC metadata can make the distribution incomplete.
- Metadata mutability and Token-2022 transfer-fee state are not yet fully reconstructed from historical extension instructions.
- LP locks are left unverified. LP burn evidence is limited to explicit LP mint/burn instructions found in the sampled window.
- Initial SOL liquidity and FDV are unavailable in this pass and remain zero defaults; they must not be interpreted as observed zero liquidity or valuation.
- Jito bundle count remains zero without provider-specific bundle metadata.
- The source q1 labels are later market-outcome labels, not evidence of malicious intent. The first-15-slot feature rows must be audited for coverage and leakage before model evaluation.

The workflow exports the feature JSONL alongside the audit report. backtest.py intentionally requires at least 50 complete in-window labeled rows and both classes in each chronological partition; the current bounded pilot is not expected to satisfy that threshold. Do not lower the threshold merely to manufacture performance metrics.


## Helius historical RPC probe (user-run)

A user-run probe against a Helius Mainnet RPC endpoint reported successful `getHealth`, `getSlot`, and `getFirstAvailableBlock` calls. It sampled one block and one transaction at approximately 365, 730, 1,095, and 1,460 days before the probe anchor. All four sampled `getBlocks`/`getBlock`/`getTransaction` paths returned successfully:

| Approximate age | Sample slot | Transactions in sampled block | Sample transaction |
|---:|---:|---:|---|
| 365 days | 382,619,204 | 1,128 | OK |
| 730 days | 303,713,493 | 2,051 | OK |
| 1,095 days | 232,538,996 | 3,704 | OK |
| 1,460 days | 163,465,783 | 208 | OK |

The endpoint reported `getFirstAvailableBlock = 0`. This is encouraging evidence of deep historical access, but the probe sampled only four blocks and one transaction per block. It does not establish exhaustive retention, full transaction metadata, address-history support, `jsonParsed` support, throughput, or uninterrupted access across every target cohort date. Before bulk reconstruction, separately test `getSignaturesForAddress` on known addresses, `getBlock` with `jsonParsed` and raw `json`, versioned transactions, and several consecutive blocks around selected launch timestamps. Do not include the RPC URL or API key in committed files, logs, or reports. Rotate any credential exposed in chat before large-scale use.


## Repeatable Helius probe via GitHub Actions

The repository includes a manual workflow, `.github/workflows/historical-rpc-probe.yml`, and a credential-safe runner, `helius_history_probe.py`. To run it from the fork:

1. Rotate any RPC credential previously pasted into chat or otherwise exposed.
2. In the fork, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Set the secret name to `HELIUS_RPC_URL` and its value to the rotated Mainnet RPC URL. Never put the URL in workflow inputs or commit it.
4. Open **Actions → Historical RPC capability probe → Run workflow** on this branch. The default slots repeat the four historical samples above; up to 20 comma-separated slots may be supplied.
5. Download the `historical-rpc-probe` artifact and inspect per-slot block retrieval, encoding, transaction metadata coverage, and token-balance rows.

The runner intentionally omits the endpoint URL, credentials, and provider response bodies from its report and logs. This is still a capability probe, not a dataset collector or a backtest. A successful run is only a gate to the next controlled reconstruction pilot; it does not establish exhaustive historical coverage or predictive performance.


### Automated Helius capability probe (2026-11-26)

The credential-safe GitHub Actions probe completed successfully on run [45799157819](https://github.com/MJ-RWA/vloop-free-openai-mcp/actions/runs/45799157819), using the four historical slots above. The report showed:

| Sample slot | Transactions | Transactions with metadata | Token balance rows | Encoding | getBlock latency |
|---:|---:|---:|---:|---|---:|
| 382,619,204 | 1,128 | 1,128 (100%) | 1,946 | jsonParsed | 157 ms |
| 303,713,493 | 2,051 | 2,051 (100%) | 3,293 | jsonParsed | 256 ms |
| 232,538,996 | 3,704 | 3,704 (100%) | 2,328 | jsonParsed | 378 ms |
| 163,465,783 | 208 | 208 (100%) | 172 | jsonParsed | 72 ms |

All four requested slots were returned by `getBlocks`; all four `getBlock` calls succeeded using `jsonParsed`, with metadata present for every transaction in each sampled block. This is a stronger capability result than the earlier single-transaction test and supports proceeding to a bounded reconstruction pilot.

It remains a four-block sample, not continuous historical coverage. It does not validate address-signature pagination, missing/skipped slots across a date range, full cohort coverage, throughput under sustained collection, or correctly labeled outcomes. No scorer performance metric can be inferred from this probe. The artifact is retained by GitHub Actions for 14 days.


## Bounded labeled historical reconstruction pilot

`historical_reconstruction_pilot.py` provides a small feasibility audit using the public q1 rule-based labels from the Early Rug Pull Detection on Solana research repository. It selects a deterministic chronological spread of q1 `VALID` positive and negative mints. It first uses each label row's `token_creation_time` to binary-search `getBlockTime`, scans a bounded nearby slot window, and verifies the actual `initializeMint`/`initializeMint2` instruction. This avoids walking thousands of address signatures and fetching one transaction at a time for every candidate. If timestamp-based discovery cannot verify the mint initialization, it falls back to `getSignaturesForAddress` pagination and `getTransaction`. After locating the launch, it inspects the following 15 produced slots for mint-related transactions and configured pool initializations.

Run locally with `HELIUS_RPC_URL` set in the environment:

```powershell
$env:HELIUS_RPC_URL = "https://your-private-rpc-endpoint"
py -3.12 historical_reconstruction_pilot.py --per-class 4
```

Or run **Actions → Historical reconstruction pilot** after configuring the `HELIUS_RPC_URL` repository secret. The report is uploaded as the `historical-reconstruction-pilot` artifact.

Dataset source: [Saba-Heydaridoost/rugpull_detector](https://github.com/Saba-Heydaridoost/rugpull_detector), `dataset/rule_based_labels.csv`. The pilot uses only q1 `VALID` labels; it does not use the released q1 feature columns as first-15-slot predictors. The q1 outcome label is determined after its 750-second observation cutoff and up to the 24-hour outcome horizon. A market-outcome label is not proof of criminal intent.

This is a bounded feasibility pilot, not an exhaustive dataset build and not a model evaluation. It reports mint-init recovery and first-15-slot pool-event observations only. It does not yet construct complete scorer feature vectors, verify all DEX venues, or calculate precision, recall, ROC AUC, Brier score, or calibration. Do not describe its results as backtest performance. The research repository's dataset licensing and attribution terms must be checked before redistributing any source data; do not commit the downloaded CSV or copy its rows into this repository.
