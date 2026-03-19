import os
import tensorflow as tf
import numpy as np
import pickle
from flask import Flask, request, jsonify

from common import config
from common.config import setup_logging

logger = setup_logging("tp-predictor")

# Threading safety for VMs and to ensure isolation of TensorFlow operations
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
tf.config.threading.set_intra_op_parallelism_threads(1)
tf.config.threading.set_inter_op_parallelism_threads(1)

app = Flask(__name__)

# Global model load (Happens once at script start)
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)

logger.info("Loading model into memory...")
model = tf.keras.models.load_model(model_full_path, compile=False)
with open(metadata_full_path, 'rb') as f:
    encoders, _ = pickle.load(f)
logger.info("Predictor is READY.")

@app.route('/internal/predict', methods=['POST'])
def predict():
    data = request.json
    logger.info(f"Received prediction request: {data}")

    try:
        # Transformation logic (using the loaded 'encoders')
        n_enc = encoders['name'].transform([data['n']])[0]
        v_enc = encoders['verb'].transform([data['v']])[0]
        l_enc = encoders['level'].transform([data['l']])[0]
        s_enc = encoders['system'].transform([data['s']])[0]
        b_enc = encoders['backend'].transform([encoders['backend'].classes_[0]])[0]

        features = np.array([0.5, float(data['attempt']), v_enc, l_enc, b_enc, s_enc, n_enc], dtype='float32')
        X_input = features.reshape(1, 1, 7)
        
        prediction = model.predict(X_input, verbose=0)
        logger.info(f"Prediction result for {data}: {prediction[0][0]}")

        return jsonify({"probability": float(prediction[0][0])})
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
