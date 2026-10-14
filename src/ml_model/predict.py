"""
predict.py
────────────────────────────────────────────────────────────────────────────
Cloud Resilience Assessment Framework — Smart Powergrid Systems
Course  : BCSE355L — Cloud Architecture Design
Author  : Mohit Gupta (feature/Mohit)

PURPOSE
-------
This module implements the core inference logic for two agents:

  AnomalyDetectionAgent
    • Loads the trained Isolation Forest model
    • Accepts a single PMU reading OR a batch DataFrame
    • Returns: prediction label + anomaly score + confidence
    • This agent runs inside AWS Fargate containers, one per AZ

  StabilityAssessmentAgent
    • Loads the trained Random Forest classifier
    • Accepts a post-mitigation grid state reading
    • Returns: STABLE / UNSTABLE + probability
    • Used by the Mitigation Agent to confirm recovery

HOW THIS FITS IN THE ARCHITECTURE
    PMU sensor → Kinesis stream → [AnomalyDetectionAgent]
                                        ↓ ANOMALY detected
                                  [MitigationAgent] executes fix
                                        ↓ after fix
                                  [StabilityAssessmentAgent]
                                        ↓ STABLE confirmed
                                  [ZeroTrustAgent] de-escalates privileges
────────────────────────────────────────────────────────────────────────────
"""

import os
import time
import json
import numpy as np
import pandas as pd
import joblib
import warnings
from dataclasses import dataclass, asdict
from typing import Union

warnings.filterwarnings("ignore")

# ─── Path Configuration ────────────────────────────────────────────────────
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_DIR = os.path.join(ROOT_DIR, "src", "ml_model", "models")


# ═══════════════════════════════════════════════════════════════════════════
#  PREDICTION RESULT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AnomalyResult:
    """
    Structured result returned by AnomalyDetectionAgent.

    Fields
    ------
    label        : "NORMAL" or "ANOMALY"
    is_anomaly   : True if anomaly detected
    anomaly_score: float  — higher = more anomalous (range: ~0.0 – 1.0)
    confidence   : float  — confidence percentage  (0–100)
    latency_ms   : float  — inference time in milliseconds
    timestamp    : str    — ISO timestamp of detection
    agent_id     : str    — which agent instance made the decision
    """
    label        : str
    is_anomaly   : bool
    anomaly_score: float
    confidence   : float
    latency_ms   : float
    timestamp    : str
    agent_id     : str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StabilityResult:
    """Structured result returned by StabilityAssessmentAgent."""
    label           : str     # "STABLE" or "UNSTABLE"
    is_stable       : bool
    stable_prob     : float   # probability of STABLE (0–1)
    unstable_prob   : float   # probability of UNSTABLE (0–1)
    latency_ms      : float
    timestamp       : str
    agent_id        : str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
#  ANOMALY DETECTION AGENT
# ═══════════════════════════════════════════════════════════════════════════

class AnomalyDetectionAgent:
    """
    Localized Anomaly Detection Agent — runs per AWS Availability Zone.

    Wraps the trained Isolation Forest model with:
      - Model loading with validation
      - Single-sample and batch prediction
      - Anomaly scoring and confidence estimation
      - Structured JSON output for downstream agents

    Usage
    -----
    agent = AnomalyDetectionAgent(agent_id="agent-az1")
    result = agent.predict(pmu_reading_dict)
    print(result.to_json())
    """

    MODEL_FILE   = "anomaly_detector.pkl"
    SCALER_FILE  = "msu_scaler.pkl"
    IMPUTER_FILE = "msu_imputer.pkl"

    def __init__(self, agent_id: str = "anomaly-agent-01"):
        self.agent_id = agent_id
        self._model   = None
        self._scaler  = None
        self._imputer = None
        self._load_artifacts()
        print(f"[{self.agent_id}] Anomaly Detection Agent initialized ✅")

    def _load_artifacts(self):
        """Load model + preprocessor artifacts from disk."""
        model_path   = os.path.join(MODEL_DIR, self.MODEL_FILE)
        scaler_path  = os.path.join(MODEL_DIR, self.SCALER_FILE)
        imputer_path = os.path.join(MODEL_DIR, self.IMPUTER_FILE)

        for path, label in [(model_path,"model"), (scaler_path,"scaler"), (imputer_path,"imputer")]:
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Artifact not found: {path}\n"
                    "Run train.py first: python src/ml_model/train.py"
                )

        self._model   = joblib.load(model_path)
        self._scaler  = joblib.load(scaler_path)
        self._imputer = joblib.load(imputer_path)

    def _preprocess(self, data: Union[dict, pd.DataFrame]) -> np.ndarray:
        """Convert raw input to model-ready numpy array."""
        if isinstance(data, dict):
            df = pd.DataFrame([data])
        elif isinstance(data, pd.DataFrame):
            df = data.copy()
        else:
            raise TypeError(f"Expected dict or DataFrame, got {type(data)}")

        # Keep only features the model knows about
        expected_features = self._scaler.feature_names_in_
        for col in expected_features:
            if col not in df.columns:
                df[col] = np.nan   # fill unknown features with NaN → imputed

        df = df[expected_features]

        # Impute → scale
        X_imp    = self._imputer.transform(df)
        X_scaled = self._scaler.transform(X_imp)
        return X_scaled

    def predict(self, data: Union[dict, pd.DataFrame]) -> AnomalyResult:
        """
        Predict whether a PMU reading is NORMAL or ANOMALY.

        Parameters
        ----------
        data : dict or DataFrame
               Single PMU reading (dict) or batch of readings (DataFrame)

        Returns
        -------
        AnomalyResult (single) or list[AnomalyResult] (batch)
        """
        t_start   = time.time()
        X         = self._preprocess(data)
        raw_pred  = self._model.predict(X)          # +1 = normal, -1 = anomaly
        scores    = -self._model.score_samples(X)   # higher = more anomalous

        # Normalize score to 0–1 range for readability
        score_norm = float(np.clip(scores[0] / 0.8, 0.0, 1.0))
        is_anomaly = bool(raw_pred[0] == -1)
        label      = "ANOMALY" if is_anomaly else "NORMAL"

        # Confidence: distance from decision boundary
        # score < 0.3 = very normal, score > 0.6 = very anomalous
        if is_anomaly:
            confidence = float(np.clip(score_norm * 100, 50.0, 99.9))
        else:
            confidence = float(np.clip((1 - score_norm) * 100, 50.0, 99.9))

        latency_ms = (time.time() - t_start) * 1000

        return AnomalyResult(
            label        = label,
            is_anomaly   = is_anomaly,
            anomaly_score= round(score_norm, 4),
            confidence   = round(confidence, 2),
            latency_ms   = round(latency_ms, 3),
            timestamp    = pd.Timestamp.now().isoformat(),
            agent_id     = self.agent_id,
        )

    def predict_batch(self, df: pd.DataFrame) -> list[AnomalyResult]:
        """Run prediction on an entire DataFrame (batch mode)."""
        t_start   = time.time()
        X         = self._preprocess(df)
        raw_preds = self._model.predict(X)
        scores    = -self._model.score_samples(X)
        scores_norm = np.clip(scores / 0.8, 0.0, 1.0)

        results = []
        for i in range(len(raw_preds)):
            is_anomaly = bool(raw_preds[i] == -1)
            score_norm = float(scores_norm[i])
            confidence = float(np.clip(
                score_norm * 100 if is_anomaly else (1 - score_norm) * 100,
                50.0, 99.9
            ))
            results.append(AnomalyResult(
                label        = "ANOMALY" if is_anomaly else "NORMAL",
                is_anomaly   = is_anomaly,
                anomaly_score= round(score_norm, 4),
                confidence   = round(confidence, 2),
                latency_ms   = round((time.time() - t_start) * 1000 / len(raw_preds), 3),
                timestamp    = pd.Timestamp.now().isoformat(),
                agent_id     = self.agent_id,
            ))
        return results

    def status(self) -> dict:
        """Health-check endpoint — called by CloudWatch."""
        return {
            "agent_id"    : self.agent_id,
            "status"      : "healthy",
            "model"       : self.MODEL_FILE,
            "model_loaded": self._model is not None,
            "timestamp"   : pd.Timestamp.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  STABILITY ASSESSMENT AGENT
# ═══════════════════════════════════════════════════════════════════════════

class StabilityAssessmentAgent:
    """
    Stability Assessment Agent — used by the Mitigation Agent.

    After the Mitigation Agent executes a fix (reroute, scale, restart),
    this agent assesses whether the grid has returned to a stable state.

    If UNSTABLE is returned, the Mitigation Agent escalates to the next
    level of response (e.g., full substation isolation).
    """

    MODEL_FILE   = "stability_classifier.pkl"
    SCALER_FILE  = "stability_scaler.pkl"
    IMPUTER_FILE = "stability_imputer.pkl"

    def __init__(self, agent_id: str = "stability-agent-01"):
        self.agent_id = agent_id
        self._model   = None
        self._scaler  = None
        self._imputer = None
        self._load_artifacts()
        print(f"[{self.agent_id}] Stability Assessment Agent initialized ✅")

    def _load_artifacts(self):
        for fname, attr in [
            (self.MODEL_FILE,   "_model"),
            (self.SCALER_FILE,  "_scaler"),
            (self.IMPUTER_FILE, "_imputer"),
        ]:
            path = os.path.join(MODEL_DIR, fname)
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"Artifact not found: {path}\n"
                    "Run train.py first: python src/ml_model/train.py"
                )
            setattr(self, attr, joblib.load(path))

    def _preprocess(self, data: Union[dict, pd.DataFrame]) -> np.ndarray:
        if isinstance(data, dict):
            df = pd.DataFrame([data])
        elif isinstance(data, pd.DataFrame):
            df = data.copy()
        else:
            raise TypeError(f"Expected dict or DataFrame, got {type(data)}")

        expected_features = self._scaler.feature_names_in_
        for col in expected_features:
            if col not in df.columns:
                df[col] = np.nan

        df       = df[expected_features]
        X_imp    = self._imputer.transform(df)
        X_scaled = self._scaler.transform(X_imp)
        return X_scaled

    def predict(self, data: Union[dict, pd.DataFrame]) -> StabilityResult:
        """
        Assess grid stability after a mitigation action.

        Returns StabilityResult with STABLE/UNSTABLE label and probability.
        """
        t_start = time.time()
        X       = self._preprocess(data)
        pred    = self._model.predict(X)[0]
        proba   = self._model.predict_proba(X)[0]   # [p_stable, p_unstable]

        is_stable  = bool(pred == 0)
        latency_ms = (time.time() - t_start) * 1000

        return StabilityResult(
            label       = "STABLE" if is_stable else "UNSTABLE",
            is_stable   = is_stable,
            stable_prob = round(float(proba[0]), 4),
            unstable_prob= round(float(proba[1]), 4),
            latency_ms  = round(latency_ms, 3),
            timestamp   = pd.Timestamp.now().isoformat(),
            agent_id    = self.agent_id,
        )

    def status(self) -> dict:
        return {
            "agent_id"    : self.agent_id,
            "status"      : "healthy",
            "model"       : self.MODEL_FILE,
            "model_loaded": self._model is not None,
            "timestamp"   : pd.Timestamp.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO — End-to-End Agent Pipeline Test
# ═══════════════════════════════════════════════════════════════════════════

def run_demo():
    """
    Demonstrates the full agent prediction pipeline with sample data.
    Simulates: PMU reading → anomaly check → stability check
    """
    sep = "═" * 65
    print(f"\n{sep}")
    print("  AGENT PREDICTION PIPELINE — DEMO")
    print(sep)

    # Load processed test data for realistic samples
    proc_dir = os.path.join(ROOT_DIR, "dataset", "processed")

    # ── Agent 1: Anomaly Detection ─────────────────────────────────────────
    print("\n🤖 Initializing Anomaly Detection Agent...")
    anomaly_agent = AnomalyDetectionAgent(agent_id="anomaly-agent-az1")

    msu_path = os.path.join(proc_dir, "msu_ornl_processed.csv")
    msu_df   = pd.read_csv(msu_path)
    X_msu    = msu_df.drop(columns=["label"])
    y_msu    = msu_df["label"].values

    # Sample a NORMAL and an ANOMALY reading
    normal_idx  = np.where(y_msu == 0)[0][42]
    attack_idx  = np.where(y_msu == 1)[0][7]

    print("\n--- Sending NORMAL PMU reading ---")
    r1 = anomaly_agent.predict(X_msu.iloc[[normal_idx]])
    print(r1.to_json())

    print("\n--- Sending ATTACK PMU reading ---")
    r2 = anomaly_agent.predict(X_msu.iloc[[attack_idx]])
    print(r2.to_json())

    # ── Agent 2: Stability Assessment ──────────────────────────────────────
    print("\n🤖 Initializing Stability Assessment Agent...")
    stability_agent = StabilityAssessmentAgent(agent_id="stability-agent-az1")

    stab_path = os.path.join(proc_dir, "stability_processed.csv")
    stab_df   = pd.read_csv(stab_path)
    X_stab    = stab_df.drop(columns=["label"])
    y_stab    = stab_df["label"].values

    stable_idx   = np.where(y_stab == 0)[0][10]
    unstable_idx = np.where(y_stab == 1)[0][5]

    print("\n--- Grid state AFTER mitigation (should be stable) ---")
    s1 = stability_agent.predict(X_stab.iloc[[stable_idx]])
    print(s1.to_json())

    print("\n--- Grid state where mitigation FAILED (unstable) ---")
    s2 = stability_agent.predict(X_stab.iloc[[unstable_idx]])
    print(s2.to_json())

    # ── Batch Test ─────────────────────────────────────────────────────────
    print("\n--- Batch prediction on 100 samples ---")
    batch_results = anomaly_agent.predict_batch(X_msu.head(100))
    detected = sum(1 for r in batch_results if r.is_anomaly)
    print(f"  Batch of 100: {detected} anomalies detected, {100-detected} normal")

    # ── Health Check ───────────────────────────────────────────────────────
    print("\n--- Agent Health Checks (CloudWatch equivalent) ---")
    print(json.dumps(anomaly_agent.status(),    indent=2))
    print(json.dumps(stability_agent.status(), indent=2))

    print(f"\n{sep}")
    print("  ✅ All agents operational — pipeline verified")
    print(f"  Next step → run  src/backend/anomaly_agent.py (FastAPI)")
    print(sep)


if __name__ == "__main__":
    run_demo()
