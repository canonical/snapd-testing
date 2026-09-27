# Test Predictor Architecture Research Summary

**Date:** 2026-06-19  
**Repository:** `canonical/snapd-testing` — `ai/test-predictor/`

## Objective

Evaluate alternative ML architectures for spread test outcome prediction. The existing system uses an LSTM model to predict success probability for spread integration tests based on historical execution data. The goal is to add a second source of truth and explore architectures that better capture flakiness patterns.

## Data Format

All models consume the same input: time-series `.ts` files with columns:
- `scenario`, `verb`, `backend`, `system`, `name` (categorical, label-encoded and normalized to [0,1])
- `success` (binary target, masked at 0.5 for current timestep to prevent leakage)

Sequences are grouped by `(backend, system, name)`, windowed to 15 timesteps, and zero-padded.

---

## Model 1: LSTM (Existing)

### Architecture
```
Input(15, 6) → LSTM(64) → Dropout(0.2) → LSTM(32) → Dropout(0.2) → Dense(32, relu) → Dense(1, sigmoid)
```

### Training Process
1. **Select subset** — Group files by (backend, system, scenario), take N most recent per group
2. **Preprocess** — LabelEncode categoricals, normalize to [0,1]
3. **Build sequences** — Sliding window of 15 steps, mask current-step success
4. **Augment** — Synthetic failure/flaky/recovery/stable-pass patterns (~60% more data)
5. **Train** — 30 epochs, batch size 32, focal loss (γ=2.0, α=0.75), early stopping, class weighting
6. **Save** — `.keras` model + `metadata.pkl` (encoders + scaler)

### Accuracy Measurement
- **Training:** Keras built-in accuracy metric per-epoch on 85/15 train/val split
- **Evaluation:** Binary threshold at 0.5 — `(predicted >= 0.5) == actual_outcome`

### Characteristics
- ~20,000 trainable parameters
- Training time: ~3.6s on CPU (900 samples)
- Conservative predictions (52–65% range on synthetic data)
- Extensive post-processing heuristics for flaky pattern adjustment (~200 lines of rules)
- Focal loss + class weights handle imbalanced pass/fail ratio

### Results (synthetic, 900 samples)
- Training accuracy (val): 71.7%
- Evaluation accuracy: 80.0%

---

## Model 2: Echo State Network / Reservoir Computing (New — Implemented Today)

### Architecture
```
Input(15, 6) → Fixed Random Reservoir(300 neurons) → Ridge Regression Readout → probability ∈ [0,1]
```

### Training Process
1. **Preprocess** — Reuse LSTM's LabelEncoders for feature consistency
2. **Build sequences** — Identical grouping and windowing as LSTM
3. **Initialize reservoir** — Fixed random W_in (input weights) and W_res (recurrent weights), scaled to spectral radius 0.95
4. **Drive reservoir** — Feed each sequence through leaky-integrator dynamics, collect final states
5. **Fit readout** — Ridge regression (α=1.0) from 300-dim reservoir states to success probability
6. **Save** — `.pkl` with W_in, W_res, readout + `esn_metadata.pkl`

### State Update Equation
$$\mathbf{s}_t = (1 - \lambda)\mathbf{s}_{t-1} + \lambda \cdot \tanh(\mathbf{W}_{in}\mathbf{x}_t + \mathbf{W}_{res}\mathbf{s}_{t-1})$$

Where λ = 0.3 (leaking rate).

### Accuracy Measurement
- **Training:** `mean(round(readout_prediction) == target)` on full training set (no val split)
- **Evaluation:** Same binary threshold as LSTM

### Characteristics
- ~301 trainable parameters (readout weights + bias only)
- Training time: ~0.3s on CPU (12x faster than LSTM)
- Decisive predictions (17–100% range — better calibrated for extremes)
- Native flakiness detection via reservoir state variance across runs
- No data augmentation needed (implicit regularization from random projection)
- Deterministic given seed (reproducible reservoir initialization verified)

### Results (synthetic, 900 samples)
- Training accuracy: 78.1%
- Evaluation accuracy: 90.0%

### Key Hyperparameters
| Parameter | Value | Effect |
|-----------|-------|--------|
| Reservoir size | 300 | More neurons = more expressive, slower |
| Spectral radius | 0.95 | Controls memory length (near 1.0 = longer memory) |
| Leaking rate | 0.3 | How quickly reservoir forgets (lower = longer memory) |
| Input scaling | 0.5 | Amplitude of input projections |
| Ridge alpha | 1.0 | Regularization strength |

---

## Model 3: BDH — Baby Dragon Hatchling (Researched — Not Yet Implemented)

**Paper:** Kosowski et al., "The Dragon Hatchling: The Missing Link Between the Transformer and Models of the Brain" (arXiv:2509.26507, Sep 2025)

### Architecture (BDH-GPU variant)
```
For each layer l:
  x = ReLU(v* @ Dx)                          # Decode to neuron space
  a* = LinearAttention(Q=x, K=x, V=v*)       # Causal linear attention (Hebbian state)
  y = ReLU(LayerNorm(a*) @ Dy) * x           # Gated output
  v* = v* + LayerNorm(y @ E)                  # Residual update in hidden space

Output: v* @ readout → prediction
```

Linear attention: `(RoPE(Q) @ RoPE(K)^T).tril(diagonal=-1) @ V`

### Key Properties
- **State-space model** with linear attention — O(T) memory for inference
- **Hebbian learning** — synapse state ρ = Σ K^T V accumulates over time (unbounded context)
- **~3nd parameters** (e.g., n=128, d=64 → ~24K, comparable to LSTM)
- **Sparse positive activations** (~5% active neurons per token)
- **Monosemantic synapses** — individual ρ(i,j) entries map to specific concepts
- **Scale-free structure** — heavy-tailed degree distribution in effective graph
- Trained with standard backpropagation (PyTorch)
- Matches GPT2 performance at 10M–1B parameter scale on language tasks

### Relevance to Spread Test Prediction
- Unbounded context could capture long-range patterns (test failed 50 runs ago → still affects prediction)
- Interpretable synapses could explain *why* a test×system pair is predicted to fail
- ReLU gating naturally handles sparse failure signals
- However: overkill for current data scale; requires PyTorch (current stack is TensorFlow)

---

## Ensemble Architecture (Implemented Today)

The LSTM and ESN run as independent prediction services. A public API provides:

| Endpoint | Purpose |
|----------|---------|
| `/predict` | LSTM prediction (existing) |
| `/esn/predict` | ESN prediction |
| `/esn/flakiness` | Reservoir-state-variance flakiness score |
| `/ensemble/predict` | Weighted combination (65% LSTM + 35% ESN) |

### Ensemble Formula
$$P_{ensemble} = (1 - w) \cdot P_{LSTM} + w \cdot P_{ESN}$$

Where $w = 0.35$ (configurable via `ESN_ENSEMBLE_WEIGHT`).

The ensemble also reports an **agreement score**: $1 - |P_{LSTM} - P_{ESN}|$

### Critical Fix: Encoder Consistency
Both models must use identical LabelEncoder class orderings. The ESN trainer now loads the LSTM's fitted encoders after LSTM training completes, ensuring the same label maps to the same numeric value in both models.

---

## Comparative Results (Synthetic Data, 900 Training Samples)

| Metric | LSTM | ESN | Ensemble |
|--------|------|-----|----------|
| Training time | 3.6s | 0.3s | — |
| Trainable params | ~20,000 | ~301 | — |
| Evaluation accuracy | 80.0% | 90.0% | 90.0% |
| Prediction range | 52–65% | 17–100% | 40–77% |
| Model agreement | — | — | 70.7% |

### Why ESN Outperformed LSTM on Small Data
With only 900 samples, the LSTM's ~20K parameters are under-constrained. The ESN's 301 parameters fit comfortably, and its fixed random projection acts as implicit regularization — analogous to why random forests outperform deep networks on small tabular datasets. With production data (thousands of runs), the LSTM is expected to close the gap.

---

## Future Work

1. **BDH Implementation** — Implement BDH-GPU as a third predictor backend once data volume justifies it. Key prerequisite: rearchitect data pipeline to stream full run histories (not fixed 15-step windows) to exploit BDH's unbounded context advantage.

2. **ESN Validation Split** — Add a train/val split to ESN training to report generalization accuracy rather than training accuracy.

3. **Real Data Evaluation** — Run comparative predictions on production spread data from the deployed predictor service.

4. **Flakiness Calibration** — Correlate ESN reservoir-state-variance flakiness scores against known flaky tests to calibrate the sensitivity parameter.

5. **Ensemble Weight Tuning** — Once sufficient real data exists, optimize `ESN_ENSEMBLE_WEIGHT` via cross-validation rather than using a fixed 0.35.
