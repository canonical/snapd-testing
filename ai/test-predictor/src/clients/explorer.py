#!/usr/bin/env python3

import argparse
import os
import pickle
from secrets import choice
import sys
import numpy as np

from common import config
from common.config import setup_logging
from common.predictor import predict_success

logger = setup_logging("tp-explorer-client")

# Silence TensorFlow noise
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0' 
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

from tensorflow.keras.models import load_model

def get_selection(prompt, options):
    """Displays options as a numbered list and returns the selected string."""
    print(f"\nSelect {prompt}:")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    
    while True:
        try:
            choice = int(input(f"Enter number (1-{len(options)}): "))
            if 1 <= choice <= len(options):
                return options[choice - 1]
        except ValueError:
            pass
        print(f"Invalid input. Please enter 1-{len(options)}.")

def print_progress(current, total, bar_length=40):
    """Simple terminal progress bar."""
    percent = float(current) * 100 / total
    arrow = '-' * int(percent / 100 * bar_length - 1) + '>'
    spaces = ' ' * (bar_length - len(arrow))
    sys.stdout.write(f'\rProgress: [{arrow}{spaces}] {percent:.1f}% ({current}/{total})')
    sys.stdout.flush()

def main():
    parser = argparse.ArgumentParser(description="Advanced Model Explorer")
    parser.add_argument("--modeldir", help="Directory with model and metadata", default=config.MODEL_DIR)
    args = parser.parse_args()

    model_path = os.path.join(args.modeldir, config.MODEL_NAME)
    metadata_path = os.path.join(args.modeldir, config.METADATA_NAME)

    if not os.path.exists(model_path):
        print(f"[!] Model not found: {model_path}")
        return

    model = load_model(model_path)
    with open(metadata_path, 'rb') as f:
        encoders, scaler = pickle.load(f)

    # Available options
    names = list(encoders['name'].classes_)
    verbs = list(encoders['verb'].classes_)
    levels = list(encoders['level'].classes_)
    systems = list(encoders['system'].classes_)

    while True:
        print("\n" + "="*50)
        print("      AI TEST SUCCESS EXPLORER")
        print("="*50)
        print("1. Predict Specific Scenario")
        print("2. Rank Tests (High Risk)")
        print("3. Worst System for Test")
        print("4. Worst Test by Attempt")
        print("q. Quit")
        
        choice = input("\nChoice: ").lower()
        if choice == 'q': break

        try:
            if choice == '1':
                n = get_selection("Name", names)
                v = get_selection("Verb", verbs)
                l = get_selection("Level", levels)
                s = get_selection("System", systems)
                
                prob = predict_success(model, encoders, n, v, l, s)
                print(f"\nRESULT: {prob:.2%} Success Probability for {n}")

            elif choice == '2':
                v = get_selection("Verb", verbs)
                l = get_selection("Level", levels)
                s = get_selection("System", systems)
                
                total_names = len(names)
                print(f"\n[*] Analyzing {total_names} tests...")
                results = []
                for i, n in enumerate(names, 1):
                    # Show progress every 5 tests to save CPU
                    if i % 5 == 0 or i == total_names:
                        print_progress(i, total_names)

                    p = predict_success(model, encoders, n, v, l, s)
                    if p is not None:
                        results.append((n, p))
                
                if not results:
                    print("[!] No results found. Check if the model was trained with these categories.")
                else:
                    # Explicitly sort by the probability (the second item in the tuple)
                    results.sort(key=lambda x: x[1]) 
                    
                    print("\nTOP 10 HIGH-RISK NAMES (Lowest Success Probability):")
                    print("-" * 60)
                    for name, p in results[:10]:
                        # color-like indicator for very low success
                        status = "!! CRITICAL" if p < 0.2 else "!! HIGH RISK"
                        print(f"{status} | {p:.2%} Success | {name}")

            elif choice == '3':
                n = get_selection("Name", names)
                v = get_selection("Verb", verbs)
                l = get_selection("Level", levels)
                
                results = []
                for s in systems:
                    p = predict_success(model, encoders, n, v, l, s)
                    if p is not None: results.append((s, p))
                
                results.sort(key=lambda x: x[1])
                print(f"\nWorst Systems for {n}:")
                for s_name, p in results:
                    print(f"{p:.2%} Success | {s_name}")

            if choice == '4':
                v = get_selection("Verb", verbs)
                l = get_selection("Level", levels)
                s = get_selection("System", systems)
                
                # Ask for the specific attempt to analyze
                target_attempt = int(input("\nWhich attempt number to analyze? (e.g., 2): ") or 2)

                print(f"\n[*] Finding tests with highest failure risk at Attempt {target_attempt}...")
                final_report = []

                for n in names:
                    # Predict probability for the specific target attempt
                    prob = predict_success(model, encoders, n, v, l, s, attempt=target_attempt)
                    
                    if prob is not None:
                        final_report.append({
                            'name': n, 
                            'prob': prob
                        })

                # Sort by lowest probability (the ones most likely to fail)
                final_report.sort(key=lambda x: x['prob'])

                print(f"\nTOP 10 WORST TESTS AT ATTEMPT {target_attempt}:")
                print("-" * 70)
                for item in final_report[:10]:
                    status = "CRITICAL" if item['prob'] < 0.3 else "HIGH RISK"
                    print(f"{status} | {item['prob']:.2%} Success | {item['name']}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[!] Error: {e}")

if __name__ == "__main__":
    main()
