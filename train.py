import argparse
import json
import os
import random
import sys
import time

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split

from features import extract, to_list, FEATURE_NAMES


_TLDS = (
    "com", "net", "org", "ru", "info", "biz", "name", "su",
    "me", "io", "co", "us", "de", "eu", "fr", "nl",
)

def ensure_tld(domain: str) -> str:
    return domain if "." in domain else f"{domain}.{random.choice(_TLDS)}"


def is_valid(domain: str) -> bool:
    label = domain.split(".")[0] if "." in domain else domain
    if not label or len(label) < 4:
        return False
    if label.startswith("-") or label.endswith("-"):
        return False
    if label.count("-") > len(label) // 3:
        return False
    return True


def load_tsv(path: str) -> list[str]:
    if not os.path.exists(path):
        print(f"[skip] not found: {path}")
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = line.strip().split("\t")[0].strip().lower()
            if d and not d.startswith("#") and is_valid(d):
                out.append(ensure_tld(d))
    print(f"[data] {os.path.basename(path)}: {len(out):,}")
    return out


def load_txt(path: str) -> list[str]:
    if not os.path.exists(path):
        print(f"[skip] not found: {path}")
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = line.strip().lower()
            if d and not d.startswith("#") and is_valid(d):
                out.append(ensure_tld(d))
    print(f"[data] {os.path.basename(path)}: {len(out):,}")
    return out


def load_jsonl(path: str) -> tuple[list[str], list[str]]:
    if not os.path.exists(path):
        print(f"[skip] not found: {path}")
        return [], []
    legit, dga = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("domain", "").strip().lower()
            t = row.get("threat", "").lower()
            if not d or not is_valid(d):
                continue
            d = ensure_tld(d)
            if t == "dga":
                dga.append(d)
            elif t in ("benign", "legit", "clean"):
                legit.append(d)
    print(f"[data] {os.path.basename(path)}: DGA={len(dga):,}  legit={len(legit):,}")
    return legit, dga


def load_legit_csv(path: str, limit: int) -> list[str]:
    if not os.path.exists(path):
        print(f"[skip] не найден: {path}")
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        first = f.readline().strip()
        parts = first.split(",")
        
        # Cisco: "1,google.com"
        # Majestic: "GlobalRank,TldRank,Domain,..."
        if parts[0].isdigit():
            d = parts[1].strip().lower() if len(parts) >= 2 else ""
            if d and is_valid(d):
                out.append(d)

        for line in f:
            if limit and len(out) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if parts[0].isdigit() and len(parts) >= 2:
                d = parts[1].strip().lower() # Cisco
            elif len(parts) >= 3:
                d = parts[2].strip().lower() # Majestic
            else:
                continue
            if d and is_valid(d):
                out.append(d)

    print(f"[data] {os.path.basename(path)} (limit={limit:,}): {len(out):,}")
    return out


def build_dataset(
    legit: list[str],
    dga: list[str],
    legit_n: int,
) -> tuple[pd.DataFrame, np.ndarray]:

    legit = list(dict.fromkeys(legit))
    dga   = list(dict.fromkeys(dga))

    ratio = len(dga) / len(legit) if legit else float("inf")
    print(f"\n[feat] Total: DGA={len(dga):,}  legit={len(legit):,}  ratio={ratio:.2f}")
    print(f"[feat] Features: {len(FEATURE_NAMES)}")
    print("[feat] Extracting features")

    all_domains = legit + dga
    labels = [0] * len(legit) + [1] * len(dga)

    t0 = time.perf_counter()
    rows = [to_list(extract(d)) for d in all_domains]
    elapsed = time.perf_counter() - t0
    print(f"[feat] {len(all_domains):,} domains in {elapsed:.2f}s "
          f"({elapsed / len(all_domains) * 1e6:.1f} µs/domain)")

    X = pd.DataFrame(rows, columns=list(FEATURE_NAMES))
    y = np.array(labels, dtype=np.int8)
    return X, y


def train(X: pd.DataFrame, y: np.ndarray) -> lgb.LGBMClassifier:
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )

    params = {
        "objective":         "binary",
        "metric":            "auc",
        "n_estimators":      500,
        "learning_rate":     0.05,
        "num_leaves":        63,
        "min_child_samples": 20,
        "feature_fraction":  0.8,
        "bagging_fraction":  0.8,
        "bagging_freq":      5,
        "verbose":           -1,
        "n_jobs":            -1,
    }

    print("\n[train] Training LightGBM")
    t0 = time.perf_counter()
    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(50),
        ],
    )
    elapsed = time.perf_counter() - t0
    print(f"[train] Done in {elapsed:.1f}s  "
          f"best_iteration={model.best_iteration_}")

    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    auc    = roc_auc_score(y_test, y_prob)
    print(f"\n[eval] AUC = {auc:.4f}")
    print(classification_report(y_test, y_pred, target_names=["legit", "DGA"]))

    imp = sorted(
        zip(FEATURE_NAMES, model.feature_importances_),
        key=lambda x: -x[1],
    )
    max_score = max(v for _, v in imp) or 1
    print("[feat] Feature importance:")
    for name, score in imp:
        bar = "█" * int(score / max_score * 20)
        print(f"  {name:25s} {bar} {score}")

    X_np = X_test.to_numpy(dtype=np.float32)
    _ = model.predict_proba(X_np)
    t0 = time.perf_counter()
    _ = model.predict_proba(X_np)
    elapsed = time.perf_counter() - t0
    print(f"\n[perf] Inference: {elapsed / len(X_test) * 1e6:.2f} µs/domain "
          f"({len(X_test):,} domains)")

    return model


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # DGA
    parser.add_argument("--dga",         default="data/vendor_iocs.tsv",
                        help="TSV with DGA domains")
    parser.add_argument("--extra",       default=None,
                        help="JSONL {domain, threat}")
    parser.add_argument("--extra-dga",   default=None,
                        help="false negatives")

    # Legit
    parser.add_argument("--legit",       default=None,
                        help="CSV rank,domain (Cisco/Majestic)")
    parser.add_argument("--extra-legit", default=None,
                        help="false pasitve")
    parser.add_argument("--legit-n",     type=int, default=150_000,
                        help="Legit domain limit from CSV")

    parser.add_argument("--out",         default="model.pkl")
    parser.add_argument("--seed",        type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    all_dga: list[str]   = []
    all_legit: list[str] = []

    if args.extra is None:
        args.extra = "data/dga-training-data-example.json"

    print("\n[sources] DGA:")
    all_dga += load_tsv(args.dga)

    if args.extra:
        legit_j, dga_j = load_jsonl(args.extra)
        all_dga   += dga_j
        all_legit += legit_j

    if args.extra_dga:
        all_dga += load_txt(args.extra_dga)

    print("\n[sources] Legit:")
    if args.legit:
        all_legit += load_legit_csv(args.legit, args.legit_n)
    if args.extra_legit:
        all_legit += load_txt(args.extra_legit)

    if not all_dga:
        print("[error] No DGA domains found", file=sys.stderr); sys.exit(1)
    if not all_legit:
        print("[error] No legit domains found", file=sys.stderr); sys.exit(1)

    print(f"\n[data] Before deduplication: "
          f"DGA={len(all_dga):,}  legit={len(all_legit):,}")

    X, y = build_dataset(all_legit, all_dga, legit_n=args.legit_n)
    model = train(X, y)

    joblib.dump(model, args.out, compress=3)
    size_kb = os.path.getsize(args.out) / 1024
    print(f"\n[ok] Model: {args.out}  ({size_kb:.0f} KB)")
    print(f"     Features ({len(FEATURE_NAMES)}): {', '.join(FEATURE_NAMES)}")
    print(f"\n[ok] Feature names in model: {model.feature_name_[:3]}...")


if __name__ == "__main__":
    main()
