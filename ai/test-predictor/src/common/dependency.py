import glob
import os
import threading
import time

import pandas as pd
import numpy as np
import networkx as nx
from statsmodels.tsa.stattools import grangercausalitytests

from common import config
from common.cache import DependencyMatrixCache
from common.utils import setup_logging

logger = setup_logging("dependency-manager")


class DependencyManager:
    """
    Manages all dependency analysis computation:
    loading data, building matrices and graphs, ranking root causes,
    computing toxicity, and optionally running Granger causality.
    Results are cached in-memory and on disk via DependencyMatrixCache.
    """

    def __init__(self, ts_dir=None):
        self.ts_dir = ts_dir or config.TS_DIR
        self.cache = DependencyMatrixCache()
        self.cache.load()
        self._build_lock = threading.Lock()
        self._build_status = {
            "running": False,
            "mode": None,
            "system": None,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "processed": 0,
            "cached": 0,
            "no_data": 0,
            "failed": 0,
            "errors": [],
        }

    def _list_available_system_scenario_pairs(self):
        """Return unique (system, scenario) pairs available in task/executing rows."""
        files = glob.glob(os.path.join(self.ts_dir, "*.ts"))
        pairs = set()
        for f in files:
            try:
                df = pd.read_csv(f, dtype={'system': str, 'scenario': str, 'level': str, 'verb': str})
                if len(df) < 10:
                    continue
                df = df[(df['level'] == 'task') & (df['verb'] == 'executing')]
                if df.empty:
                    continue
                for _, row in df[['system', 'scenario']].dropna(subset=['system']).drop_duplicates().iterrows():
                    system = str(row.get('system') or '').strip()
                    scenario = str(row.get('scenario') or '').strip() or None
                    if system:
                        pairs.add((system, scenario))
            except Exception as e:
                logger.error("Failed to enumerate system/scenario pairs from %s: %s", f, e)
        return sorted(pairs)

    def _run_cache_build(self, system=None):
        pairs = self._list_available_system_scenario_pairs()
        if system:
            pairs = [pair for pair in pairs if pair[0] == system]

        with self._build_lock:
            self._build_status.update({
                "running": True,
                "mode": "system" if system else "all",
                "system": system,
                "started_at": time.time(),
                "finished_at": None,
                "total": len(pairs),
                "processed": 0,
                "cached": 0,
                "no_data": 0,
                "failed": 0,
                "errors": [],
            })

        for system, scenario in pairs:
            try:
                result = self.analyze(system=system, scenario=scenario, use_cache=False)
                with self._build_lock:
                    self._build_status["processed"] += 1
                    if result:
                        self._build_status["cached"] += 1
                    else:
                        self._build_status["no_data"] += 1
            except Exception as e:
                with self._build_lock:
                    self._build_status["processed"] += 1
                    self._build_status["failed"] += 1
                    self._build_status["errors"].append({
                        "system": system,
                        "scenario": scenario,
                        "error": str(e),
                    })

        with self._build_lock:
            self._build_status["running"] = False
            self._build_status["finished_at"] = time.time()

    def trigger_cache_build_all(self):
        """Start asynchronous cache build for all system/scenario pairs."""
        with self._build_lock:
            if self._build_status.get("running"):
                return {"started": False, "message": "Cache build already running"}
        threading.Thread(target=self._run_cache_build, daemon=True).start()
        return {"started": True, "message": "Cache build started"}

    def trigger_cache_build_system(self, system):
        """Start asynchronous cache build for a specific system across scenarios."""
        if not system:
            return {"started": False, "message": "missing required param: system"}

        available_pairs = self._list_available_system_scenario_pairs()
        if not any(pair[0] == system for pair in available_pairs):
            return {"started": False, "message": f"system not found: {system}"}

        with self._build_lock:
            if self._build_status.get("running"):
                return {"started": False, "message": "Cache build already running"}

        threading.Thread(target=self._run_cache_build, kwargs={"system": system}, daemon=True).start()
        return {"started": True, "message": f"Cache build started for system: {system}"}

    def get_cache_build_status(self):
        """Return current cache build status and cache snapshot stats."""
        with self._build_lock:
            status = dict(self._build_status)
        status["cache"] = self.cache.stats()
        return status

    def load_all_data(self, system=None, scenario=None, min_rows=10):
        files = glob.glob(os.path.join(self.ts_dir, "*.ts"))
        if not files:
            logger.warning(f"No .ts files found in {self.ts_dir}")
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
                    df['_source_file'] = os.path.basename(f)
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

    def build_failure_matrix(self, df, min_runs=5):
        df['_run_key'] = df['runid'].astype(str) + '|' + df['instance'].astype(str)
        matrix = df.pivot_table(
            index="_run_key",
            columns="name",
            values="fail",
            aggfunc='max',
            # Do NOT fill missing with 0: a test absent from a run is NaN,
            # not a pass. fill_value=0 would inflate PASS counts.
        )
        # Count only rows where the test was actually executed (not NaN)
        seen_counts = matrix.notna().sum(axis=0)
        matrix = matrix.loc[:, seen_counts >= min_runs]
        logger.info(
            f"Failure matrix: {matrix.shape[0]} run×instance pairs × {matrix.shape[1]} tests "
            f"(dropped {(seen_counts < min_runs).sum()} rare tests)"
        )
        return matrix

    def compute_conditional_prob(self, matrix):
        """Compute P(B fails | A fails) for all test pairs."""
        tests = matrix.columns
        prob_matrix = pd.DataFrame(index=tests, columns=tests)
        for A in tests:
            A_fail = matrix[A] == 1
            if A_fail.mean() == 0:
                continue
            for B in tests:
                B_fail = matrix[B] == 1
                prob_matrix.loc[A, B] = (B_fail & A_fail).sum() / A_fail.sum()
        return prob_matrix.astype(float)

    def compute_conditional_prob_pass(self, matrix):
        """Compute P(B fails | A passes) for all test pairs."""
        tests = matrix.columns
        prob_matrix = pd.DataFrame(index=tests, columns=tests)
        tests_with_passes = 0
        for A in tests:
            A_pass = matrix[A] == 0
            n_pass = A_pass.sum()
            if n_pass == 0:
                continue
            tests_with_passes += 1
            for B in tests:
                B_fail = matrix[B] == 1
                prob_matrix.loc[A, B] = (B_fail & A_pass).sum() / n_pass
        logger.info(f"Pass-edges: {tests_with_passes} tests have at least 1 pass out of {len(tests)} total tests")
        return prob_matrix.astype(float)

    def compute_lift(self, matrix, cond_prob):
        """Lift = P(B fails | A fails) / P(B fails)."""
        base_prob = matrix.mean()
        lift = cond_prob.copy()
        for A in cond_prob.index:
            for B in cond_prob.columns:
                lift.loc[A, B] = cond_prob.loc[A, B] / base_prob[B] if base_prob[B] > 0 else 0
        return lift

    def build_graph(self, cond_prob, lift, prob_threshold=0.3, lift_threshold=1.5):
        """Build a directed dependency graph filtered by probability and lift thresholds.
           cond_prob: P(B fails | A fails) matrix
           lift: lift matrix where lift[A, B] = P(B fails | A fails) / P(B fails)
           prob_threshold: minimum P(B fails | A fails) to consider A→B a dependency
           lift_threshold: minimum lift to consider A→B a dependency 
                           (how much A failing increases the chance of B failing compared to baseline)
        """
        G = nx.DiGraph()
        candidates = 0
        passed_prob = 0
        passed_lift = 0
        passed_both = 0
        for A in cond_prob.index:
            for B in cond_prob.columns:
                if A == B:
                    continue
                p = cond_prob.loc[A, B]
                l = lift.loc[A, B]
                # Skip NaN values
                if pd.isna(p) or pd.isna(l):
                    continue
                candidates += 1
                if p >= prob_threshold:
                    passed_prob += 1
                if l >= lift_threshold:
                    passed_lift += 1
                if p >= prob_threshold and l >= lift_threshold:
                    passed_both += 1
                    G.add_edge(A, B, weight=p, lift=l)
        logger.debug(f"build_graph: {candidates} candidates, {passed_prob} passed prob, {passed_lift} passed lift, {passed_both} edges created")
        return G

    def rank_root_causes(self, graph):
        """Score = sum of outgoing edge weights − sum of incoming edge weights."""
        scores = {}
        for node in graph.nodes:
            out_weight = sum(d["weight"] for _, _, d in graph.out_edges(node, data=True))
            in_weight  = sum(d["weight"] for _, _, d in graph.in_edges(node, data=True))
            scores[node] = out_weight - in_weight
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def compute_toxicity(self, cond_prob):
        """Sum of P(B fails | A fails) across all B — how broadly A propagates failures."""
        return cond_prob.sum(axis=1).sort_values(ascending=False)

    # --------------------------------------------------------------- granger

    def reduce_matrix_for_granger(self, matrix, cond_prob, graph, graph_pass, max_tests=80):
        """Reduce matrix to at most max_tests columns before running Granger."""
        if max_tests is None or max_tests <= 0 or matrix.shape[1] <= max_tests:
            return matrix

        selected = set(graph.nodes()) | set(graph_pass.nodes())

        impact = cond_prob.fillna(0).sum(axis=1) + cond_prob.fillna(0).sum(axis=0)
        for name in impact.sort_values(ascending=False).index:
            if len(selected) >= max_tests:
                break
            selected.add(name)

        if len(selected) < max_tests:
            for name in matrix.mean(axis=0).sort_values(ascending=False).index:
                if len(selected) >= max_tests:
                    break
                selected.add(name)

        cols = [c for c in matrix.columns if c in selected][:max_tests]
        reduced = matrix[cols]
        logger.info("Granger matrix reduced from %s to %s tests", matrix.shape[1], reduced.shape[1])
        return reduced

    def compute_granger(self, matrix, max_lag=2, pval_threshold=0.05):
        """Compute Granger causality for all test pairs (O(N²))."""
        tests = matrix.columns
        results = []
        for A in tests:
            for B in tests:
                if A == B:
                    continue
                data = matrix[[B, A]].dropna()
                if len(data) < 10:
                    continue
                try:
                    test_result = grangercausalitytests(data, maxlag=max_lag)
                    p_values = [
                        test_result[lag][0]["ssr_ftest"][1]
                        for lag in range(1, max_lag + 1)
                    ]
                    min_p = min(p_values)
                    if min_p < pval_threshold:
                        results.append({"cause": A, "effect": B, "p_value": min_p})
                except Exception:
                    continue
        return pd.DataFrame(results)

    def analyze(self, system=None, scenario=None,
                prob_threshold=0.3, lift_threshold=1.5,
                run_granger=False, granger_max_tests=80,
                use_cache=True):
        """
        Run full dependency analysis.  Results are cached keyed by (system, scenario).
        Cache is loaded from disk at startup and saved to disk after each recompute.
        """
        if use_cache:
            cached = self.cache.get(system, scenario)
            if cached is not None:
                return cached

        df = self.load_all_data(system=system, scenario=scenario)
        if df.empty:
            return {}

        # The failure matrix is the core data structure for all subsequent analysis.
        # It captures which tests failed in which runs, and is used to compute conditional
        # probabilities, lift, and Granger causality. We apply a minimum runs filter
        # to ensure we have enough data for reliable statistics. The rest of the analysis
        # depends on having a well-constructed failure matrix, so if it's empty after
        # filtering, we return an empty result to avoid errors downstream.
        matrix = self.build_failure_matrix(df)
        if matrix.empty:
            logger.warning("Failure matrix is empty after filtering.")
            return {}

        # Compute conditional probabilities and lift, then build the dependency graph based
        # on the specified thresholds. The graph captures which tests are likely to cause
        # others to fail, and is the basis for ranking root causes and computing toxicity.
        cond_prob      = self.compute_conditional_prob(matrix)
        lift           = self.compute_lift(matrix, cond_prob)
        logger.info(f"Fail-fail graph construction:")
        graph              = self.build_graph(cond_prob, lift, prob_threshold, lift_threshold)

        # Also compute the "pass" version of the dependency graph, which captures tests
        # that tend to fail when another test passes. This can reveal different patterns
        # of interaction — e.g. resource contention or environment interference — that
        # the failure graph alone would miss.
        cond_prob_pass = self.compute_conditional_prob_pass(matrix)
        lift_pass      = self.compute_lift(matrix, cond_prob_pass)
        logger.info(f"Pass-fail graph construction:")
        graph_pass         = self.build_graph(cond_prob_pass, lift_pass, prob_threshold, lift_threshold)

        # Rank root causes based on their influence in the failure graph, and compute
        # toxicity scores based on how many other tests they cause to fail.
        ranking  = self.rank_root_causes(graph)
        toxicity = self.compute_toxicity(cond_prob)

        result = {
            "matrix":    matrix,
            "cond_prob": cond_prob,
            "lift":      lift,
            "graph":     graph,
            "graph_pass": graph_pass,
            "ranking":   ranking,
            "toxicity":  toxicity,
            "source_file_count": int(df['_source_file'].nunique()) if '_source_file' in df.columns else 0,
            "raw_task_rows": int(len(df)),
        }

        # Optionally run Granger causality analysis on a reduced set of tests to identify
        # potential causal relationships. This provides deeper insights into the temporal
        # dynamics of test failures but is computationally intensive (O(N²)), so the matrix
        # is first reduced to a manageable size before running.
        if run_granger:
            granger_matrix = self.reduce_matrix_for_granger(
                matrix, cond_prob, graph, graph_pass, max_tests=granger_max_tests
            )
            if granger_matrix.shape[1] >= 2:
                result["granger"] = self.compute_granger(granger_matrix)
            else:
                result["granger"] = pd.DataFrame()
            result["granger_meta"] = {
                "tests_total": int(matrix.shape[1]),
                "tests_used":  int(granger_matrix.shape[1]),
                "max_tests":   int(granger_max_tests),
            }

        self.cache.set(system, scenario, result)
        return result

    def get_pass_probabilities_given_fail(self, test_name, system=None, scenario=None,
                                          use_cache=True, include_self=False):
        """
        Compute P(B passes | A fails) for all tests B, where A is ``test_name``.

        The cohort is the same run+machine key used by the dependency matrix
        (``runid|instance``), so probabilities are evaluated only over tests that
        were executed together in the same run_id / machine rows.
        """
        if not test_name:
            raise ValueError("test_name is required")

        result = self.analyze(system=system, scenario=scenario, use_cache=use_cache)
        if not result:
            return {}

        matrix = result.get("matrix")
        if matrix is None or matrix.empty:
            return {}

        if test_name not in matrix.columns:
            raise KeyError(test_name)

        a_series = matrix[test_name]
        a_present = a_series.notna()
        a_fail_all = a_present & (a_series == 1)
        conditioned_rows = int(a_fail_all.sum())

        probabilities = []
        for other_test in matrix.columns:
            if not include_self and other_test == test_name:
                continue

            b_series = matrix[other_test]
            # Restrict to runs where BOTH tests were actually executed
            both_present = a_present & b_series.notna()
            a_fail = both_present & (a_series == 1)
            a_pass = both_present & (a_series == 0)
            b_pass = both_present & (b_series == 0)
            pass_count = int((a_fail & b_pass).sum())
            both_pass_count = int((a_pass & b_pass).sum())
            denom = int(a_fail.sum())
            pass_probability = float(pass_count / denom) if denom > 0 else 0.0
            probabilities.append(
                {
                    "test": other_test,
                    "pass_probability": pass_probability,
                    "pass_count": pass_count,
                    "both_pass_count": both_pass_count,
                }
            )

        probabilities.sort(key=lambda item: item["pass_probability"], reverse=True)

        return {
            "conditioning_test": test_name,
            "condition": "FAIL",
            "run_machine_rows": int(matrix.shape[0]),
            "source_files_evaluated": (
                int(result["source_file_count"])
                if result.get("source_file_count") is not None else None
            ),
            "raw_task_rows": int(result.get("raw_task_rows", 0)),
            "rows_where_condition_holds": conditioned_rows,
            "total_tests": int(matrix.shape[1]),
            "probabilities": probabilities,
        }

    def get_fail_probabilities_given_fail(self, test_name, system=None, scenario=None,
                                          use_cache=True, include_self=False):
        """
        Compute P(B fails | A fails) for all tests B, where A is ``test_name``.
        """
        if not test_name:
            raise ValueError("test_name is required")

        result = self.analyze(system=system, scenario=scenario, use_cache=use_cache)
        if not result:
            return {}

        matrix = result.get("matrix")
        if matrix is None or matrix.empty:
            return {}

        if test_name not in matrix.columns:
            raise KeyError(test_name)

        a_series = matrix[test_name]
        a_present = a_series.notna()
        conditioned_rows = int((a_present & (a_series == 1)).sum())

        probabilities = []
        for other_test in matrix.columns:
            if not include_self and other_test == test_name:
                continue

            b_series = matrix[other_test]
            # Restrict to runs where BOTH tests were actually executed
            both_present = a_present & b_series.notna()
            a_fail = both_present & (a_series == 1)
            b_fail = both_present & (b_series == 1)
            b_pass = both_present & (b_series == 0)
            fail_count = int((a_fail & b_fail).sum())
            fail_pass_count = int((a_fail & b_pass).sum())
            denom = int(a_fail.sum())
            fail_probability = float(fail_count / denom) if denom > 0 else 0.0
            probabilities.append(
                {
                    "test": other_test,
                    "fail_probability": fail_probability,
                    "fail_count": fail_count,
                    "fail_pass_count": fail_pass_count,
                }
            )

        probabilities.sort(key=lambda item: item["fail_probability"], reverse=True)

        return {
            "conditioning_test": test_name,
            "condition": "FAIL",
            "run_machine_rows": int(matrix.shape[0]),
            "source_files_evaluated": (
                int(result["source_file_count"])
                if result.get("source_file_count") is not None else None
            ),
            "raw_task_rows": int(result.get("raw_task_rows", 0)),
            "rows_where_condition_holds": conditioned_rows,
            "total_tests": int(matrix.shape[1]),
            "probabilities": probabilities,
        }
