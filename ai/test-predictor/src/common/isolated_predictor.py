import sys, os, pickle, numpy as np
import tensorflow as tf

from tensorflow.keras.models import load_model

# Force absolute isolation
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

def run(m_path, meta_path, name, verb, level, system, attempt, duration):
    try:
        with open(meta_path, 'rb') as f:
            encoders, _ = pickle.load(f)
        
        model = load_model(m_path, compile=False)
        
        # Mapping (Ensure this matches your training order)
        n_enc = encoders['name'].transform([name])[0]
        v_enc = encoders['verb'].transform([verb])[0]
        l_enc = encoders['level'].transform([level])[0]
        s_enc = encoders['system'].transform([system])[0]
        b_val = encoders['backend'].classes_[0]
        b_enc = encoders['backend'].transform([b_val])[0]

        features = np.array([float(duration), float(attempt), v_enc, l_enc, b_enc, s_enc, n_enc], dtype='float32')
        X_input = features.reshape(1, 1, 7)
        
        prediction = model.predict(X_input, verbose=0)
        print(float(prediction[0][0]))
    except Exception as e:
        print(f"ERROR:{e}")

if __name__ == "__main__":
    try:
        # Check if we actually have enough args
        if len(sys.argv) < 9:
            print(f"ERROR: Expected 8 args, got {len(sys.argv)-1}")
            sys.exit(1)
            
        run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], 
            sys.argv[5], sys.argv[6], sys.argv[7], sys.argv[8])
    except Exception as fatal:
        # Write directly to stderr so it shows up in your gunicorn logs
        sys.stderr.write(f"FATAL ERROR: {str(fatal)}\n")
        sys.exit(1)