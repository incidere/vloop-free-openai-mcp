# Historical Reconstruction Feasibility Pilot — 2026-11-27

## Executive result

The bounded historical reconstruction pilot completed successfully on GitHub Actions run [45926152197](https://github.com/MJ-RWA/vloop-free-openai-mcp/actions/runs/45926152197), at branch revision `11fb319897d831a9152a3daccd40f1a27121d0ca`.

It recovered and verified mint-initialization transactions for all 8 sampled mints and exported 8 scorer-compatible feature rows. The deterministic sample contained 4 q1 negative labels and 4 q1 positive labels.

**This is a reconstruction feasibility result, not a backtest. No predictive metrics are reported.**

## Sample and recovery

| Measure | Result |
|---|---:|
| Sampled mints | 8 |
| q1 negative labels | 4 |
| q1 positive labels | 4 |
| Mint initializations verified | 8 / 8 |
| Launch windows scanned | 8 |
| Feature rows exported | 8 |
| Windows with a matching configured pool event | 0 / 8 |

The sample was selected as a deterministic chronological spread from the q1 `VALID` labels in the [source dataset](https://github.com/Saba-Heydaridoost/rugpull_detector). The q1 outcome labels were used only as labels; released q1 feature columns were not used as first-15-slot predictors. The pilot located mint initialization by timestamp-guided block search and verified the actual initialize-mint instruction before reconstructing the launch window.

## Feature coverage audit

| Evidence / feature | Pilot observation |
|---|---|
| Holder balances | Non-empty reconstructed holder maps for 6 of 8 rows; based on token-balance metadata available in fetched blocks |
| Early buys | Non-zero early-buy counts for 6 of 8 rows |
| Slot-0 buys | Non-zero for 5 of 8 rows |
| Mint authority | Active in 4 rows, inactive in 4 |
| Freeze authority | Active in 1 row, inactive in 7 |
| Matching configured pool events | None detected in the 8 windows |
| LP mint/burn evidence | No LP mints detected; burn evidence unavailable |
| LP lock evidence | Not reconstructed; all rows remain unverified |
| Initial liquidity and FDV | Unavailable; zero fields are placeholders, not observed zero values |
| Jito bundle count | Unavailable without provider-specific bundle metadata |
| Metadata mutability / Token-2022 fees | Not fully reconstructed historically; false/zero defaults must be treated as incomplete |

The observed transaction program IDs included the Pump.fun bonding-curve program for two sample mints. A bonding-curve launch is not itself proof of AMM pool creation; graduated Pump.fun assets migrate to PumpSwap. The absence of matching pool events in this small sample must not be interpreted as proof that no liquidity existed or that the configured venue coverage is complete. Additional DEX and launchpad decoders and broader samples are needed.

## Why no backtest is claimed

The eight rows are too few for the repository's minimum backtest cohort, and important fields are missing or unverified across the sample. In particular, liquidity depth, LP protection, and provider-specific bundle evidence are unavailable. Treating these missing fields as measured zeros would bias the scorer and invalidate evaluation.

Accordingly:

- No train/holdout evaluation was run.
- No precision, recall, F1, ROC AUC, average precision, Brier score, or calibration error is claimed.
- No trained or calibrated predictive model is claimed.
- The scorer remains a transparent heuristic, not a historically validated probability model.

The next research milestone is expanding venue coverage and reconstructing a substantially larger, sufficiently complete, leakage-controlled cohort before running the existing chronological backtest. The backtest threshold must not be lowered just to produce metrics.

## Artifact

The workflow uploaded `historical_reconstruction_pilot.json` and `historical_features.jsonl` as the [historical-reconstruction-pilot artifact](https://github.com/MJ-RWA/vloop-free-openai-mcp/actions/runs/45926152197/artifacts/15166558429). GitHub Actions artifacts are temporary and this one is retained for 14 days.

## Interpretation and limitations

The q1 labels represent later market outcomes under the source study's rule-based definition; they are not proof of malicious intent. The pilot demonstrates that historical mint-init and partial early-window reconstruction is feasible with the configured RPC endpoint. It does not establish exhaustive chain-history coverage, complete DEX coverage, fully observed feature rows, or scorer performance.
