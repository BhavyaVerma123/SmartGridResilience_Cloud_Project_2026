"""
preprocessing.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
Loads, cleans, and transforms two real-world smart grid datasets:

  Dataset 1 — MSU/ORNL Power System Attack Dataset
    • 15 CSV files × ~5,000 rows each  (~75,000 records total)
    • 129 features: Voltage, Phase Angle, Current, Frequency from 4 PMU relays
    • Labels: Natural (normal operation) | Attack (cyberattack/fault)
    • Used by: Anomaly Detection Agent

  Dataset 2 — Smart Grid Stability Augmented (Kaggle)
    • 60,000 rows × 14 features
    • Features: Reaction times, Power coefficients, Stability score
    • Labels: stable | unstable
    • Used by: Mitigation Agent (assess grid state after mitigation action)

PIPELINE STEPS
--------------
  1. Load & merge all raw files
  2. Drop irrelevant / low-variance columns
  3. Impute missing values (median strategy)
  4. Savitzky-Golay smoothing on PMU voltage & frequency signals
  5. Z-score normalization (StandardScaler)
  6. Encode labels to integers  (0 = NORMAL, 1 = ANOMALY)
  7. Save processed CSVs to  dataset/processed/
  8. Print a full summary report
────────────────────────────────────────────────────────────────────────────
"""

import os
import glob
import warnings
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.impute import SimpleImputer
import joblib

warnings.filterwarnings("ignore")

# ─── Path Configuration ────────────────────────────────────────────────────
ROOT_DIR   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR    = os.path.join(ROOT_DIR, "dataset", "raw")
PROC_DIR   = os.path.join(ROOT_DIR, "dataset", "processed")
MODEL_DIR  = os.path.join(ROOT_DIR, "src", "ml_model", "models")

os.makedirs(PROC_DIR, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)

# ─── Feature Groups (MSU/ORNL) ─────────────────────────────────────────────
# R1–R4 = 4 PMU relays installed at different grid substations
# PA = Phase Angle (degrees)    PM = Phasor Magnitude
# F  = Frequency (Hz)           DF = Rate of Change of Frequency (ROCOF)
# S  = Status flag              Z  = Impedance

PMU_VOLTAGE_COLS   = [f"R{r}-PM{m}:V"  for r in range(1,5) for m in [1,2,3,7,8,9]]
PMU_CURRENT_COLS   = [f"R{r}-PM{m}:I"  for r in range(1,5) for m in [4,5,6,10,11,12]]
PMU_PHASE_COLS     = [f"R{r}-PA{m}:VH" for r in range(1,5) for m in [1,2,3,7,8,9]]
PMU_FREQ_COLS      = [f"R{r}:F"        for r in range(1,5)]
PMU_ROCOF_COLS     = [f"R{r}:DF"       for r in range(1,5)]
PMU_STATUS_COLS    = [f"R{r}:S"        for r in range(1,5)]
NETWORK_LOG_COLS   = ["snort_log1", "snort_log2", "snort_log3", "snort_log4"]
RELAY_LOG_COLS     = ["relay1_log",  "relay2_log",  "relay3_log",  "relay4_log"]
LABEL_COL_MSU      = "marker"

# All numeric features we keep for training
MSU_FEATURE_COLS = (
    PMU_VOLTAGE_COLS + PMU_CURRENT_COLS +
    PMU_PHASE_COLS   + PMU_FREQ_COLS    +
    PMU_ROCOF_COLS   + PMU_STATUS_COLS  +
    NETWORK_LOG_COLS + RELAY_LOG_COLS
)


# ═══════════════════════════════════════════════════════════════════════════
#  DATASET 1 — MSU/ORNL Power System Attack Dataset
# ═══════════════════════════════════════════════════════════════════════════

def load_msu_dataset() -> pd.DataFrame:
    """Load and concatenate all 15 MSU/ORNL CSV files."""
    pattern = os.path.join(RAW_DIR, "binaryAllNaturalPlusNormalVsAttacks", "*.csv")
    files   = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(
            f"No MSU/ORNL CSV files found in:\n  {os.path.dirname(pattern)}\n"
            "Please download the dataset first (see dataset/dataset_description.pdf)."
        )

    print(f"[MSU] Found {len(files)} CSV files — loading...")
    frames = []
    for i, fp in enumerate(files, 1):
        df = pd.read_csv(fp)
        df["source_file"] = os.path.basename(fp)   # track origin
        frames.append(df)
        print(f"  [{i:02d}/{len(files)}] {os.path.basename(fp):15s} → {df.shape[0]:,} rows")

    combined = pd.concat(frames, ignore_index=True)
    print(f"[MSU] Combined shape: {combined.shape[0]:,} rows × {combined.shape[1]} cols")
    return combined


def preprocess_msu(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Full preprocessing pipeline for MSU/ORNL dataset.

    Returns
    -------
    X_scaled : pd.DataFrame   normalised feature matrix
    y        : np.ndarray     integer labels  (0=NORMAL, 1=ANOMALY)
    """
    print("\n[MSU] Starting preprocessing pipeline...")

    # ── Step 1: Select relevant columns ────────────────────────────────────
    available_features = [c for c in MSU_FEATURE_COLS if c in df.columns]
    missing_features   = [c for c in MSU_FEATURE_COLS if c not in df.columns]
    if missing_features:
        print(f"  [WARN] {len(missing_features)} expected columns not found — skipping them.")

    X_raw = df[available_features].copy()
    print(f"  [1/6] Selected {len(available_features)} feature columns")

    # ── Step 2: Encode label ────────────────────────────────────────────────
    # Natural → 0 (NORMAL)   |   Attack → 1 (ANOMALY)
    label_map = {"Natural": 0, "Attack": 1}
    if LABEL_COL_MSU in df.columns:
        y = df[LABEL_COL_MSU].map(label_map).fillna(0).astype(int).values
    else:
        raise KeyError(f"Label column '{LABEL_COL_MSU}' not found in dataset.")

    counts = pd.Series(y).value_counts()
    print(f"  [2/6] Labels encoded → NORMAL(0): {counts.get(0,0):,}  |  ANOMALY(1): {counts.get(1,0):,}")

    # ── Step 3: Impute missing values (median) ──────────────────────────────
    imputer = SimpleImputer(strategy="median")
    X_imputed = pd.DataFrame(
        imputer.fit_transform(X_raw),
        columns=available_features
    )
    nan_count = X_raw.isnull().sum().sum()
    print(f"  [3/6] Imputed {nan_count:,} missing values using median strategy")
    joblib.dump(imputer, os.path.join(MODEL_DIR, "msu_imputer.pkl"))

    # ── Step 4: Savitzky-Golay smoothing on PMU signals ─────────────────────
    # Smooths electrical noise in voltage & frequency readings
    # window=11, polyorder=3 is standard for PMU signal processing
    smooth_cols = [c for c in PMU_VOLTAGE_COLS + PMU_FREQ_COLS + PMU_ROCOF_COLS
                   if c in X_imputed.columns]
    for col in smooth_cols:
        try:
            X_imputed[col] = savgol_filter(X_imputed[col].values, window_length=11, polyorder=3)
        except Exception:
            pass   # skip if column too short
    print(f"  [4/6] Savitzky-Golay smoothing applied to {len(smooth_cols)} PMU signal columns")

    # ── Step 5: Z-score normalization (StandardScaler) ─────────────────────
    scaler   = StandardScaler()
    X_scaled = pd.DataFrame(
        scaler.fit_transform(X_imputed),
        columns=available_features
    )
    joblib.dump(scaler, os.path.join(MODEL_DIR, "msu_scaler.pkl"))
    print(f"  [5/6] Z-score normalization applied — mean≈0, std≈1 per feature")

    # ── Step 6: Remove near-zero-variance columns ───────────────────────────
    variances  = X_scaled.var()
    low_var    = variances[variances < 1e-6].index.tolist()
    X_scaled   = X_scaled.drop(columns=low_var)
    print(f"  [6/6] Dropped {len(low_var)} near-zero-variance columns → {X_scaled.shape[1]} features remain")

    print(f"[MSU] Preprocessing complete → {X_scaled.shape[0]:,} rows × {X_scaled.shape[1]} features")
    return X_scaled, y


# ═══════════════════════════════════════════════════════════════════════════
#  DATASET 2 — Smart Grid Stability Augmented
# ═══════════════════════════════════════════════════════════════════════════

STABILITY_LABEL_COL = "stabf"   # categorical: "stable" / "unstable"
STABILITY_SCORE_COL = "stab"    # numeric stability score (keep as feature)

def load_stability_dataset() -> pd.DataFrame:
    """Load the Smart Grid Stability CSV."""
    fp = os.path.join(RAW_DIR, "smart_grid_stability_augmented.csv")
    if not os.path.exists(fp):
        raise FileNotFoundError(
            f"Smart Grid Stability dataset not found at:\n  {fp}\n"
            "Download from: kaggle datasets download -d pcbreviglieri/smart-grid-stability"
        )
    df = pd.read_csv(fp)
    print(f"[STABILITY] Loaded: {df.shape[0]:,} rows × {df.shape[1]} cols")
    return df


def preprocess_stability(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Full preprocessing pipeline for Smart Grid Stability dataset.

    Returns
    -------
    X_scaled : pd.DataFrame   normalised feature matrix
    y        : np.ndarray     integer labels  (0=STABLE, 1=UNSTABLE)
    """
    print("\n[STABILITY] Starting preprocessing pipeline...")

    # ── Step 1: Encode label ────────────────────────────────────────────────
    # stable → 0  |  unstable → 1
    label_map = {"stable": 0, "unstable": 1}
    y = df[STABILITY_LABEL_COL].map(label_map).fillna(0).astype(int).values
    counts = pd.Series(y).value_counts()
    print(f"  [1/4] Labels encoded → STABLE(0): {counts.get(0,0):,}  |  UNSTABLE(1): {counts.get(1,0):,}")

    # ── Step 2: Select numeric feature columns ──────────────────────────────
    feature_cols = [c for c in df.columns if c not in [STABILITY_LABEL_COL]]
    X_raw = df[feature_cols].select_dtypes(include=[np.number]).copy()
    print(f"  [2/4] Selected {X_raw.shape[1]} numeric feature columns: {list(X_raw.columns)}")

    # ── Step 3: Impute + normalize ──────────────────────────────────────────
    imputer  = SimpleImputer(strategy="median")
    X_imp    = pd.DataFrame(imputer.fit_transform(X_raw), columns=X_raw.columns)
    nan_count = X_raw.isnull().sum().sum()
    print(f"  [3/4] Imputed {nan_count:,} missing values")
    joblib.dump(imputer, os.path.join(MODEL_DIR, "stability_imputer.pkl"))

    scaler   = StandardScaler()
    X_scaled = pd.DataFrame(scaler.fit_transform(X_imp), columns=X_raw.columns)
    joblib.dump(scaler, os.path.join(MODEL_DIR, "stability_scaler.pkl"))
    print(f"  [4/4] Z-score normalization applied")

    print(f"[STABILITY] Preprocessing complete → {X_scaled.shape[0]:,} rows × {X_scaled.shape[1]} features")
    return X_scaled, y


# ═══════════════════════════════════════════════════════════════════════════
#  SAVE PROCESSED DATA
# ═══════════════════════════════════════════════════════════════════════════

def save_processed(X: pd.DataFrame, y: np.ndarray, name: str) -> str:
    """Attach label column and save to dataset/processed/."""
    out = X.copy()
    out["label"] = y
    fp = os.path.join(PROC_DIR, f"{name}_processed.csv")
    out.to_csv(fp, index=False)
    size_mb = os.path.getsize(fp) / (1024 * 1024)
    print(f"  → Saved: {fp}  ({size_mb:.1f} MB)")
    return fp


# ═══════════════════════════════════════════════════════════════════════════
#  SUMMARY REPORT
# ═══════════════════════════════════════════════════════════════════════════

def print_summary(msu_X, msu_y, stab_X, stab_y):
    """Print a concise summary of both processed datasets."""
    sep = "═" * 65
    print(f"\n{sep}")
    print("  PREPROCESSING SUMMARY REPORT")
    print(sep)

    print("\n📦 Dataset 1 — MSU/ORNL Power System Attack")
    print(f"   Rows      : {len(msu_y):,}")
    print(f"   Features  : {msu_X.shape[1]}")
    print(f"   NORMAL    : {(msu_y==0).sum():,}  ({(msu_y==0).mean()*100:.1f}%)")
    print(f"   ANOMALY   : {(msu_y==1).sum():,}  ({(msu_y==1).mean()*100:.1f}%)")

    print("\n📦 Dataset 2 — Smart Grid Stability")
    print(f"   Rows      : {len(stab_y):,}")
    print(f"   Features  : {stab_X.shape[1]}")
    print(f"   STABLE    : {(stab_y==0).sum():,}  ({(stab_y==0).mean()*100:.1f}%)")
    print(f"   UNSTABLE  : {(stab_y==1).sum():,}  ({(stab_y==1).mean()*100:.1f}%)")

    print(f"\n✅ Scalers & imputers saved to: src/ml_model/models/")
    print(f"✅ Processed CSVs saved to    : dataset/processed/")
    print(f"\n   Next step → run  src/ml_model/train.py")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_preprocessing() -> dict:
    """
    Full end-to-end preprocessing pipeline.
    Returns dict with processed DataFrames and label arrays.
    """
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Smart Grid Resilience — Data Preprocessing Pipeline        ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    # ── Dataset 1: MSU/ORNL ──────────────────────────────────────────────
    msu_raw       = load_msu_dataset()
    msu_X, msu_y  = preprocess_msu(msu_raw)
    save_processed(msu_X, msu_y, "msu_ornl")

    # ── Dataset 2: Stability ─────────────────────────────────────────────
    stab_raw        = load_stability_dataset()
    stab_X, stab_y  = preprocess_stability(stab_raw)
    save_processed(stab_X, stab_y, "stability")

    print_summary(msu_X, msu_y, stab_X, stab_y)

    return {
        "msu_X": msu_X, "msu_y": msu_y,
        "stab_X": stab_X, "stab_y": stab_y,
    }


if __name__ == "__main__":
    run_preprocessing()
