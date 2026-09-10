"""
train.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
Trains two ML models used by the Multi-Agent AI framework:

  Model A — Isolation Forest  (Anomaly Detection Agent)
    • Unsupervised algorithm — learns what "normal" PMU readings look like
    • Flags deviations as anomalies (attacks / faults)
    • Dataset: MSU/ORNL Power System Attack (msu_ornl_processed.csv)
    • Saved to: src/ml_model/models/anomaly_detector.pkl

  Model B — Random Forest Classifier  (Mitigation Agent)
    • Supervised algorithm — classifies grid as STABLE or UNSTABLE
    • Used AFTER mitigation action to confirm the grid recovered
    • Dataset: Smart Grid Stability (stability_processed.csv)
    • Saved to: src/ml_model/models/stability_classifier.pkl

WHY THESE TWO ALGORITHMS?
    Isolation Forest  → does NOT need all data to be labeled (real grid data
                        is mostly unlabeled). Excellent at catching rare anomalies.
    Random Forest     → ensemble of decision trees, highly accurate on tabular
                        data, fast inference, low memory — ideal for Fargate containers.
────────────────────────────────────────────────────────────────────────────
"""

import os
import time
import numpy as np
import pandas as pd
import joblib
import warnings
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, accuracy_score, f1_score
)

warnings.filterwarnings("ignore")

# ─── Path Configuration ────────────────────────────────────────────────────
ROOT_DIR   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROC_DIR   = os.path.join(ROOT_DIR, "dataset", "processed")
MODEL_DIR  = os.path.join(ROOT_DIR, "src", "ml_model", "models")
RESULTS_DIR = os.path.join(ROOT_DIR, "results")

os.makedirs(MODEL_DIR,   exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
#  HELPER UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def load_processed(name: str) -> tuple[pd.DataFrame, np.ndarray]:
    """Load a processed CSV and split into features (X) and label (y)."""
    fp = os.path.join(PROC_DIR, f"{name}_processed.csv")
    if not os.path.exists(fp):
        raise FileNotFoundError(
            f"Processed file not found: {fp}\n"
            "Run preprocessing.py first:  python src/ml_model/preprocessing.py"
        )
    df    = pd.read_csv(fp)
    y     = df["label"].values
    X     = df.drop(columns=["label"])
    print(f"[LOAD] {name}: {X.shape[0]:,} rows × {X.shape[1]} features")
    return X, y


def print_metrics(name: str, y_true: np.ndarray, y_pred: np.ndarray,
                  y_score: np.ndarray | None = None):
    """Print a formatted evaluation block."""
    sep = "─" * 55
    print(f"\n{sep}")
    print(f"  Evaluation — {name}")
    print(sep)
    acc = accuracy_score(y_true, y_pred)
    f1  = f1_score(y_true, y_pred, average="weighted", zero_division=0)
    print(f"  Accuracy  : {acc*100:.2f}%")
    print(f"  F1-Score  : {f1:.4f}")
    if y_score is not None:
        try:
            auc = roc_auc_score(y_true, y_score)
            print(f"  ROC-AUC   : {auc:.4f}")
        except Exception:
            pass
    print()
    print(classification_report(y_true, y_pred,
                                 target_names=["NORMAL/STABLE", "ANOMALY/UNSTABLE"],
                                 zero_division=0))
    cm = confusion_matrix(y_true, y_pred)
    print(f"  Confusion Matrix:")
    print(f"    TN={cm[0,0]:,}  FP={cm[0,1]:,}")
    print(f"    FN={cm[1,0]:,}  TP={cm[1,1]:,}")
    print(sep)
    return {"accuracy": acc, "f1": f1}


def save_results(name: str, metrics: dict, model_path: str):
    """Append training results to results/accuracy.txt."""
    fp = os.path.join(RESULTS_DIR, "accuracy.txt")
    with open(fp, "a") as f:
        f.write(f"\n{'='*55}\n")
        f.write(f"Model      : {name}\n")
        f.write(f"Accuracy   : {metrics['accuracy']*100:.2f}%\n")
        f.write(f"F1-Score   : {metrics['f1']:.4f}\n")
        f.write(f"Saved to   : {model_path}\n")
    print(f"  → Results appended to: {fp}")


# ═══════════════════════════════════════════════════════════════════════════
#  MODEL A — ISOLATION FOREST  (Anomaly Detection Agent)
# ═══════════════════════════════════════════════════════════════════════════

def train_anomaly_detector(X: pd.DataFrame, y: np.ndarray) -> IsolationForest:
    """
    Train an Isolation Forest on MSU/ORNL PMU data.

    Isolation Forest works by randomly isolating observations.
    Anomalies (attacks) are isolated much faster → shorter path = anomaly score.

    Parameters
    ----------
    contamination : proportion of anomalies in the dataset.
                    We compute it from actual label distribution.
    """
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  Training Model A: Isolation Forest                 ║")
    print("║  (Anomaly Detection Agent — MSU/ORNL Dataset)       ║")
    print("╚══════════════════════════════════════════════════════╝")

    # Compute actual contamination from labels
    contamination = float((y == 1).sum() / len(y))
    contamination = min(max(contamination, 0.01), 0.5)   # clamp to valid range
    print(f"\n  Contamination rate (actual attack ratio): {contamination:.3f}")

    # Split: use NORMAL samples to train (unsupervised style), test on all
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )
    print(f"  Train: {len(X_train):,} samples  |  Test: {len(X_test):,} samples")

    # Train only on normal samples (pure anomaly detection paradigm)
    X_normal_train = X_train[y_train == 0]
    print(f"  Training on {len(X_normal_train):,} NORMAL samples only...")

    t0 = time.time()
    model = IsolationForest(
        n_estimators=200,        # more trees = more stable predictions
        contamination=contamination,
        max_samples="auto",
        max_features=1.0,
        bootstrap=False,
        n_jobs=-1,               # use all CPU cores
        random_state=42
    )
    model.fit(X_normal_train)
    elapsed = time.time() - t0
    print(f"  Training complete in {elapsed:.1f}s")

    # Predict: IsolationForest returns -1 (anomaly) or +1 (normal)
    # Map to 0=NORMAL, 1=ANOMALY to match our label convention
    raw_preds  = model.predict(X_test)
    y_pred     = np.where(raw_preds == -1, 1, 0)

    # Anomaly score (higher = more anomalous) for ROC-AUC
    scores     = -model.score_samples(X_test)   # negate so higher = more anomalous

    metrics = print_metrics("Isolation Forest (Anomaly Detection)", y_test, y_pred, scores)

    # Save model
    model_path = os.path.join(MODEL_DIR, "anomaly_detector.pkl")
    joblib.dump(model, model_path)
    print(f"\n  ✅ Model saved: {model_path}")
    save_results("Isolation Forest — Anomaly Detection", metrics, model_path)

    return model


# ═══════════════════════════════════════════════════════════════════════════
#  MODEL B — RANDOM FOREST  (Mitigation / Stability Agent)
# ═══════════════════════════════════════════════════════════════════════════

def train_stability_classifier(X: pd.DataFrame, y: np.ndarray) -> RandomForestClassifier:
    """
    Train a Random Forest classifier on the Stability dataset.

    Used by the Mitigation Agent to verify grid stability AFTER
    executing a mitigation action (e.g., rerouting load, restarting container).
    If the grid is still predicted UNSTABLE, the agent escalates.
    """
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  Training Model B: Random Forest Classifier         ║")
    print("║  (Stability Agent — Smart Grid Stability Dataset)   ║")
    print("╚══════════════════════════════════════════════════════╝")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"\n  Train: {len(X_train):,} samples  |  Test: {len(X_test):,} samples")

    t0 = time.time()
    model = RandomForestClassifier(
        n_estimators=150,
        max_depth=None,          # let trees grow fully
        min_samples_split=5,
        min_samples_leaf=2,
        max_features="sqrt",     # standard for classification
        class_weight="balanced", # handles class imbalance
        n_jobs=-1,
        random_state=42
    )
    model.fit(X_train, y_train)
    elapsed = time.time() - t0
    print(f"  Training complete in {elapsed:.1f}s")

    y_pred  = model.predict(X_test)
    y_score = model.predict_proba(X_test)[:, 1]

    metrics = print_metrics("Random Forest (Stability Classifier)", y_test, y_pred, y_score)

    # Cross-validation for robustness check
    print("\n  Running 5-fold cross-validation...")
    cv_scores = cross_val_score(model, X, y, cv=5, scoring="f1_weighted", n_jobs=-1)
    print(f"  CV F1 scores : {cv_scores.round(4)}")
    print(f"  CV Mean F1   : {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # Feature importance (top 10)
    importances = pd.Series(model.feature_importances_, index=X.columns)
    top10 = importances.nlargest(10)
    print(f"\n  Top 10 most important features for grid stability:")
    for feat, imp in top10.items():
        bar = "█" * int(imp * 50)
        print(f"    {feat:12s} {bar} {imp:.4f}")

    # Save model
    model_path = os.path.join(MODEL_DIR, "stability_classifier.pkl")
    joblib.dump(model, model_path)
    print(f"\n  ✅ Model saved: {model_path}")
    save_results("Random Forest — Stability Classifier", metrics, model_path)

    return model


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def train_all():
    """Run full training pipeline for both models."""
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Smart Grid Resilience — Model Training Pipeline            ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    # ── Model A: Anomaly Detection ────────────────────────────────────────
    X_msu, y_msu       = load_processed("msu_ornl")
    anomaly_model       = train_anomaly_detector(X_msu, y_msu)

    # ── Model B: Stability Classifier ─────────────────────────────────────
    X_stab, y_stab      = load_processed("stability")
    stability_model     = train_stability_classifier(X_stab, y_stab)

    # ── Final Summary ─────────────────────────────────────────────────────
    sep = "═" * 65
    print(f"\n{sep}")
    print("  TRAINING COMPLETE — MODELS READY")
    print(sep)
    print(f"  anomaly_detector.pkl     → Anomaly Detection Agent")
    print(f"  stability_classifier.pkl → Mitigation / Stability Agent")
    print(f"  All saved to: src/ml_model/models/")
    print(f"\n  Next step → run  src/ml_model/predict.py")
    print(sep)

    return anomaly_model, stability_model


if __name__ == "__main__":
    train_all()
