import glob
import os
import sys

import pandas as pd
import numpy as np
import networkx as nx
from flask import Flask, request, jsonify

from statsmodels.tsa.stattools import grangercausalitytests

from common import config
from common.utils import setup_logging

logger = setup_logging("dependency-engine")
app = Flask(__name__)


def load_all_data(ts_dir=None, system=None, scenario=None, min_rows=10):
    """
    Loads and concatenates all .ts files from ts_dir.

    Args:
        ts_dir:   directory containing .ts files (defaults to config.TS_DIR).
        system:   if set, only include rows for this system.
        scenario: if set, only include rows for this scenario.
        min_rows: skip files with fewer rows than this threshold.

    Returns:
        Combined DataFrame with a 'fail' column (1 = failure, 0 = pass).
    """
    if ts_dir is None:
        ts_dir = config.TS_DIR

    files = glob.glob(os.path.join(ts_dir, "*.ts"))
    if not files:
        logger.warning(f"No .ts files found in {ts_dir}")
        return pd.DataFrame()

    chunks = []
    for f in files:
        try:
            df = pd.read_csv(f, dtype={'system': str, 'name': str, 'verb': str})
            if len(df) < min_rows:
                continue
            if system:
                df = df[df['system'] == system]
            if scenario:
                df = df[df['scenario'] == scenario]
            df = df[(df['level'] == 'task') & (df['verb'] == 'executing')]
            if not df.empty:
                chunks.append(df)
        except Exception as e:
            logger.error(f"Failed to read {f}: {e}")

    if not chunks:
        logger.warning("No usable data after filtering.")
        return pd.DataFrame()

    combined = pd.concat(chunks, ignore_index=True)
    combined['fail'] = (combined['success'] == 0).astype(int)
    logger.info(
        f"Loaded {len(chunks)} files → {len(combined)} rows, "
        f"{combined['runid'].nunique()} runs, "
        f"{combined['name'].nunique()} unique tests"
    )
    return combined


def build_failure_matrix(df, min_runs=5):
    """
    Builds a run × test binary failure matrix.

    Rows   = runid  (one CI run / job)
    Cols   = test name
    Values = 1 (fail) / 0 (pass)

    Tests seen in fewer than min_runs are dropped to avoid
    spurious correlations.
    """
    # Two tests are considered co-executed only when they share the same
    # runid AND instance (same job slot / machine).
    df['_run_key'] = df['runid'].astype(str) + '|' + df['instance'].astype(str)

    matrix = df.pivot_table(
        index="_run_key",
        columns="name",
        values="fail",
        aggfunc='max',
        fill_value=0
    )

    # Drop sparse tests
    seen_counts = (matrix >= 0).sum(axis=0)
    matrix = matrix.loc[:, seen_counts >= min_runs]

    logger.info(
        f"Failure matrix: {matrix.shape[0]} run×instance pairs × {matrix.shape[1]} tests "
        f"(dropped {(seen_counts < min_runs).sum()} rare tests)"
    )
    return matrix

def compute_granger(matrix, max_lag=2, pval_threshold=0.05):
    """O(N²) — only call on small filtered matrices."""
    if grangercausalitytests is None:
        raise RuntimeError("statsmodels is not installed; Granger analysis is unavailable")

    tests = matrix.columns
    results = []

    for A in tests:
        for B in tests:
            if A == B:
                continue

            # Data format: [target, predictor]
            data = matrix[[B, A]].dropna()

            # Need enough samples
            if len(data) < 10:
                continue

            try:
                test_result = grangercausalitytests(
                    data,
                    maxlag=max_lag
                )

                # Take best (lowest) p-value across lags
                p_values = [
                    test_result[lag][0]["ssr_ftest"][1]
                    for lag in range(1, max_lag + 1)
                ]

                min_p = min(p_values)

                if min_p < pval_threshold:
                    results.append({
                        "cause": A,
                        "effect": B,
                        "p_value": min_p
                    })

            except Exception:
                continue

    return pd.DataFrame(results)

def compute_conditional_prob(matrix):
    tests = matrix.columns
    prob_matrix = pd.DataFrame(index=tests, columns=tests)

    for A in tests:
        A_fail = matrix[A] == 1
        P_A = A_fail.mean()

        if P_A == 0:
            continue

        for B in tests:
            B_fail = matrix[B] == 1

            # P(B fails | A fails)
            P_B_given_A = (B_fail & A_fail).sum() / A_fail.sum()

            prob_matrix.loc[A, B] = P_B_given_A

    return prob_matrix.astype(float)


def compute_conditional_prob_pass(matrix):
    """Compute P(B fails | A passes) for all test pairs."""
    tests = matrix.columns
    prob_matrix = pd.DataFrame(index=tests, columns=tests)

    for A in tests:
        A_pass = matrix[A] == 0
        n_pass = A_pass.sum()

        if n_pass == 0:
            continue

        for B in tests:
            B_fail = matrix[B] == 1

            # P(B fails | A passes)
            P_B_given_A_pass = (B_fail & A_pass).sum() / n_pass

            prob_matrix.loc[A, B] = P_B_given_A_pass

    return prob_matrix.astype(float)


def compute_lift(matrix, cond_prob):
    base_prob = matrix.mean()  # P(B fails)

    lift = cond_prob.copy()

    for A in cond_prob.index:
        for B in cond_prob.columns:
            if base_prob[B] > 0:
                lift.loc[A, B] = cond_prob.loc[A, B] / base_prob[B]
            else:
                lift.loc[A, B] = 0

    return lift


def build_graph(cond_prob, lift, prob_threshold=0.3, lift_threshold=1.5):
    G = nx.DiGraph()

    for A in cond_prob.index:
        for B in cond_prob.columns:
            if A == B:
                continue

            p = cond_prob.loc[A, B]
            l = lift.loc[A, B]

            if p >= prob_threshold and l >= lift_threshold:
                G.add_edge(A, B, weight=p, lift=l)

    return G


def rank_root_causes(G):
    """
    Root causes:
    - High outgoing edges
    - Low incoming edges
    """
    scores = {}

    for node in G.nodes:
        out_weight = sum([d["weight"] for _, _, d in G.out_edges(node, data=True)])
        in_weight = sum([d["weight"] for _, _, d in G.in_edges(node, data=True)])

        score = out_weight - in_weight
        scores[node] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked


def compute_toxicity(cond_prob):
    """
    How much a test causes others to fail
    """
    return cond_prob.sum(axis=1).sort_values(ascending=False)


def analyze(system=None, scenario=None,
            prob_threshold=0.3, lift_threshold=1.5,
            run_granger=False):
    df = load_all_data(ts_dir=config.TS_DIR, system=system, scenario=scenario)
    if df.empty:
        return {}

    matrix = build_failure_matrix(df)
    if matrix.empty:
        logger.warning("Failure matrix is empty after filtering.")
        return {}

    cond_prob = compute_conditional_prob(matrix)
    lift = compute_lift(matrix, cond_prob)

    G = build_graph(cond_prob, lift,
                    prob_threshold=prob_threshold,
                    lift_threshold=lift_threshold)

    cond_prob_pass = compute_conditional_prob_pass(matrix)
    lift_pass = compute_lift(matrix, cond_prob_pass)
    G_pass = build_graph(cond_prob_pass, lift_pass,
                         prob_threshold=prob_threshold,
                         lift_threshold=lift_threshold)

    ranking = rank_root_causes(G)
    toxicity = compute_toxicity(cond_prob)

    result = {
        "matrix": matrix,
        "cond_prob": cond_prob,
        "lift": lift,
        "graph": G,
        "graph_pass": G_pass,
        "ranking": ranking,
        "toxicity": toxicity
    }

    if run_granger:
        result["granger"] = compute_granger(matrix)

    return result


def _serialize(result):
    """Convert pandas/networkx objects to JSON-safe types."""
    ranking = [{"test": t, "score": round(s, 4)} for t, s in result.get("ranking", [])]
    toxicity = [
        {"test": t, "toxicity": round(float(v), 4)}
        for t, v in result.get("toxicity", {}).items()
    ]
    edges = [
        {
            "cause": u,
            "effect": v,
            "prob": round(d["weight"], 4),
            "lift": round(d["lift"], 4),
        }
        for u, v, d in result["graph"].edges(data=True)
    ] if result.get("graph") else []

    pass_edges = [
        {
            "cause": u,
            "effect": v,
            "prob": round(d["weight"], 4),
            "lift": round(d["lift"], 4),
        }
        for u, v, d in result["graph_pass"].edges(data=True)
    ] if result.get("graph_pass") else []

    payload = {
        "matrix_shape": list(result["matrix"].shape) if result.get("matrix") is not None else [],
        "ranking": ranking,
        "toxicity": toxicity,
        "edges": edges,
        "pass_edges": pass_edges,
    }

    granger_df = result.get("granger")
    if granger_df is not None:
        payload["granger"] = granger_df.to_dict(orient="records")

    return payload


@app.route('/internal/dependencies', methods=['GET'])
def internal_dependencies():
    system = request.args.get('system')
    scenario = request.args.get('scenario')
    run_granger = str(request.args.get('run_granger', 'false')).lower() in ('1', 'true', 'yes')

    try:
        # prob_threshold is the minimum P(B fails | A fails) for an edge to be included in the graph.
        prob_threshold = float(request.args.get('prob_threshold', 0.3))
        # lift_threshold is the minimum lift for an edge to be included in the graph.
        lift_threshold = float(request.args.get('lift_threshold', 1.5))
    except ValueError:
        return jsonify({"error": "prob_threshold and lift_threshold must be numbers"}), 400

    try:
        result = analyze(
            system=system,
            scenario=scenario,
            prob_threshold=prob_threshold,
            lift_threshold=lift_threshold,
            run_granger=run_granger,
        )
    except Exception as e:
        logger.error(f"Dependency analysis failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    if not result:
        return jsonify({"error": "No data found for the given filters"}), 404

    return jsonify(_serialize(result)), 200


if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.DEPENDENCY_PORT, threaded=True)