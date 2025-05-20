from flask import Flask, request, jsonify, render_template
import requests
from flask_cors import CORS
import logging
import os
from dotenv import load_dotenv
load_dotenv()

# Authorized access tokens for MVP invite-only
AUTHORIZED_TOKENS = {
    "katya_token",
    "alex_token",
    "lena_token"
}

app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:3000", "http://127.0.0.1:5500"],
        "methods": ["POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API endpoints
FASTAPI_BASE = {
    "predict": os.environ.get("PREDICTION_API_URL", "http://localhost:8000/predict"),
    "accept": os.environ.get("ACCEPT_API_URL", "http://localhost:8001/accept_prescription"),
    "correct_single": os.environ.get("CORRECT_SINGLE_API_URL", "http://localhost:8001/correct"),
    "correct_combo": os.environ.get("CORRECT_COMBO_API_URL", "http://localhost:8001/api/correct_combo")
}

# Required fields for prediction
REQUIRED_FIELDS = {
    "Age": (float, (18, 120)),
    "Glucose": (float, (0, None)),
    "Systolic_BP": (int, (0, None)),
    "Diastolic_BP": (int, (0, None)),
    "BMI": (float, (10, 60)),
    "Pulse": (int, (30, 200)),
    "Creatinine_mg_dl": (float, (0.1, None)),
    "country": (str, None),
    "patient_id": (str, None),
    "Medication1_Name": (str, None),
    "Medication2_Name": (str, None),
    "Medication3_Name": (str, None)
}

@app.route("/", methods=["GET"])
def home():
    token = request.args.get("access")
    if token not in AUTHORIZED_TOKENS:
        return "Access Denied", 403
    return render_template("form.html")

def _validate_data(data, required_fields):
    """Validation for prediction data"""
    errors = []
    processed = {}
    for field, (field_type, valid_range) in required_fields.items():
        if field not in data:
            errors.append(f"Missing required field: {field}")
            continue
        try:
            value = field_type(data[field]) if field_type != str else data[field]
            if valid_range and isinstance(value, (int, float)):
                min_val, max_val = valid_range
                if min_val is not None and value < min_val:
                    errors.append(f"{field} must be ≥ {min_val}")
                if max_val is not None and value > max_val:
                    errors.append(f"{field} must be ≤ {max_val}")
            processed[field] = value
        except (ValueError, TypeError):
            errors.append(f"Invalid {field} value: {data[field]}")
    return processed, errors

def _proxy_post(url, payload):
    """Helper to proxy a POST request"""
    logger.info(f"Proxying to {url}: {payload}")
    response = requests.post(url, json=payload, timeout=10, headers={"Content-Type": "application/json"})
    response.raise_for_status()
    return response

@app.route("/api/predict", methods=["POST", "OPTIONS"])
def predict():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    data = request.get_json()
    logger.info(f"Raw request JSON: {data}")
    
    # Convert Glucose from mmol/L to mg/dL
    if data.get("Glucose_unit") == "mmol" and "Glucose" in data:
        try:
            data["Glucose"] = round(float(data["Glucose"]) * 18, 2)
        except ValueError:
            pass

    # Convert Creatinine from µmol/L to mg/dL
    if data.get("Creatinine_unit") == "umol" and "Creatinine_mg_dl" in data:
        try:
            data["Creatinine_mg_dl"] = round(float(data["Creatinine_mg_dl"]) / 88.4, 2)
        except ValueError:
            pass

    processed_data, errors = _validate_data(data, REQUIRED_FIELDS)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 422
    # Preserve optional field if provided in request JSON
    if "submitted_by_token" in data:
        processed_data["submitted_by_token"] = data["submitted_by_token"]

    try:
        response = _proxy_post(FASTAPI_BASE["predict"], processed_data)
        if response.status_code == 422:
            return jsonify({"error": "FAST API validation failed", "details": response.json().get("detail", "Unknown error")}), 422
        return jsonify(response.json()), 200
    except requests.exceptions.RequestException as e:
        logger.error(f"Prediction API Error: {str(e)}")
        return jsonify({"error": f"Backend error: {str(e)}"}), 500

@app.route("/api/accept", methods=["POST", "OPTIONS"])
def accept():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    data = request.get_json()
    required = ["patient_id", "accept_prescription"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 422

    try:
        response = _proxy_post(FASTAPI_BASE["accept"], data)
        return jsonify(response.json()), 200
    except requests.exceptions.RequestException as e:
        logger.error(f"Accept API Error: {str(e)}")
        return jsonify({"error": f"Backend error: {str(e)}"}), 500

@app.route("/api/correct", methods=["POST", "OPTIONS"])
def correct():
    if request.method == "OPTIONS":
        return _build_cors_preflight_response()
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    data = request.get_json()
    try:
        # Determine if it's a combo or single correction
        if "drugs" in data and isinstance(data["drugs"], list):
            endpoint = FASTAPI_BASE["correct_combo"]
            required = ["patient_id", "id", "drugs"]
        else:
            endpoint = FASTAPI_BASE["correct_single"]
            required = ["patient_id", "id", "accept_prescription"]

        missing = [f for f in required if f not in data]
        if missing:
            return jsonify({"error": "Missing required fields", "missing": missing}), 422

        response = _proxy_post(endpoint, data)
        return jsonify(response.json()), response.status_code
    except requests.exceptions.RequestException as e:
        logger.error(f"Correction API Error: {str(e)}")
        return jsonify({"error": f"Backend error: {str(e)}"}), 500

def _build_cors_preflight_response():
    response = jsonify({"message": "CORS preflight"})
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

if __name__ == "__main__":
    app.run(debug=True, port=5000)
