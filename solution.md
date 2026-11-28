# SPL Mint Anomaly & Rug-Pull Radar Scorer

This document outlines the implementation of a standalone Python 3.12 heuristic scorer designed to evaluate newly initialized SPL mint and DEX pool creation events. The scorer outputs a calibrated probability score indicating early momentum versus rug-pull risk within 15 blocks of launch.

## Features

- **Mint/Freeze Authority State**: Checks the authority state of the mint to determine if it can be frozen or manipulated post-launch.
- **Top-10 Holder Concentration (Gini Coefficient)**: Calculates the Gini coefficient of the top 10 holders to assess wealth distribution.
- **Liquidity Pool (LP) Burn/Lock Verification**: Validates if the liquidity pool tokens are burned or locked to ensure liquidity is not easily extracted.
- **Slot-Level Bundler Clustering**: Analyzes transaction bundling to detect potential collusion or manipulation.

## Implementation