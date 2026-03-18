# LSTM Test Success Predictor

This project uses a Long Short-Term Memory (LSTM) Neural Network to predict the success probability of system tests. It analyzes historical data including **Test Name**, **Verb**, **Level**, and **System** to identify high-risk scenarios before they happen.

---

## Environment Setup

It is highly recommended to use a virtual environment to manage dependencies and avoid system conflicts.

```bash
# Install the virtual environment package
sudo apt install python3.10-venv

# Create the environment
python3 -m venv .venv

# Activate the environment
source .venv/bin/activate

# For TensorFlow/Keras
pip install tensorflow pandas numpy matplotlib

# For PyTorch
pip install torch torchvision torchaudio pandas numpy

# More deps
pip install scikit-learn flask Flask-APScheduler gunicorn

```

An AI-powered pipeline for ingesting test results and predicting success rates using an LSTM model.

## Project Overview

This project consists of three core components:
1. **Ingestion API**: A Flask service (Port 5000) that receives and saves test result JSON files.
2. **Explorer API**: A Keras-backed service (Port 5001) providing predictive analysis and test rankings.
3. **Background Processor**: A worker job that periodically monitors new data and retrains the model.

## Structure

- `src/services/api/`: Web service implementations.
- `src/services/jobs/`: Background worker scripts.
- `src/common/`: Shared logic for prediction, training, and config.
- `deploy/`: Systemd service templates and deployment scripts.
- `data/results/`: Storage for incoming JSON test data.
- `data/ts/`: Processed Time-Series data ready for the LSTM.
- `data/processed/`: Raw ts moved here after successful training.
- `model/`: Storage for the trained `.keras` model and metadata.


## The Three-Stage Pipeline

To manage resources efficiently, the solution is divided into three specialized stages. This architecture ensures that "heavy" AI tasks don't block "light" data collection.

1. The Ingestion Stage (The Entry Point)
Service: test-predictor-ingestion
What it does: It acts as the "front door." When a client (like a CI/CD runner) finishes a test, it sends a JSON file to this API.
The Logic: It validates the JSON, gives it a unique name (using job_id and run_id), and drops it into a specific folder (data/results/).
Why it's separate: This service is very fast and uses almost no RAM. Even if the AI model is busy or broken, this service can still safely collect and store incoming data.

2. The Processing Stage (The Brain Update)
Service: test-predictor-processor
What it does: It is a background worker that "polls" (checks) the results folder every 180 seconds.
The Logic: If it finds new JSON files, it picks them up, transforms them into the format the LSTM model requires, and retrains/updates the model. Once finished, it archives the files so they aren't processed twice.
Why it's separate: Training a model is slow and resource-heavy (CPU/RAM). By running this as a background job, the APIs stay responsive while the "learning" happens in the back.

3. The Explorer Stage (The Insight)
Service: test-predictor-explorer
What it does: It provides real-time answers based on the latest trained model.
The Logic: It loads the .keras model into memory once (using Gunicorn --preload). When you query the API (e.g., "What's the risk of failure for this test?"), it runs a prediction (inference) and returns a success probability.
Why it's separate: Because it keeps the heavy LSTM model in RAM, it requires specific Gunicorn settings—like longer timeouts and higher memory limits—than the lightweight Ingestion API.
