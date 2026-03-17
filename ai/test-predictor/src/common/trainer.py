import os
import pickle
import numpy as np
import pandas as pd

from config import setup_logging

# Silence TensorFlow logs BEFORE importing it
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

from sklearn.preprocessing import LabelEncoder, MinMaxScaler
from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
from tensorflow.keras.models import load_model, Sequential
from tensorflow.keras.preprocessing.sequence import pad_sequences

logger = setup_logging("tp-trainer")

def get_metadata(metadata_path):
    if os.path.exists(metadata_path):
        with open(metadata_path, 'rb') as f:
            return pickle.load(f)
    return {}, MinMaxScaler()

def save_metadata(metadata_path, encoders, scaler):
    with open(metadata_path, 'wb') as f:
        pickle.dump((encoders, scaler), f)

def preprocess_dataframe(df, encoders, scaler):
    cat_cols = ['verb', 'level', 'backend', 'system', 'name']
    for col in cat_cols:
        if col in df.columns:
            if col not in encoders:
                encoders[col] = LabelEncoder()
                df[col] = encoders[col].fit_transform(df[col].astype(str))
            else:
                # Handle unseen labels by mapping to a known string or catching error
                df[col] = encoders[col].fit_transform(df[col].astype(str))
    
    df[['duration_ms']] = scaler.fit_transform(df[['duration_ms']])

    logger.info(f"Preprocessed dataframe with {len(df)} rows and columns: {df.columns.tolist()}")
    return df

def prepare_sequences(df):
    sequences, targets = [], []
    for _, group in df.groupby('instance'):
        features = group[['duration_ms', 'attempt', 'verb', 'level', 'backend', 'system', 'name']].values
        target = group['success'].iloc[-1] 
        sequences.append(features)
        targets.append(target)
    
    X = pad_sequences(sequences, padding='post', dtype='float32')

    logger.info(f"Prepared {len(X)} sequences with max length {X.shape[1]} and feature size {X.shape[2]}")
    return X, np.array(targets)

def build_or_load_model(model_path, input_shape):
    logger.info(f"Building or loading model from {model_path} with input shape {input_shape}")
    if os.path.exists(model_path):
        model = load_model(model_path, compile=False)
    else:
        model = Sequential([
            Input(shape=input_shape),
            LSTM(64),
            Dropout(0.2),
            Dense(32, activation='relu'),
            Dense(1, activation='sigmoid')
        ])
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    logger.info("Model compiled successfully")  
    return model
