import os
from datetime import datetime
from urllib.parse import quote_plus

from flask import Flask, request, jsonify
from pymongo import MongoClient

app = Flask(__name__)


def build_mongo_uri():
    """Build the MongoDB URI.

    Priority:
      1. MONGODB_URI (full URI, used for local development)
      2. MONGO_USERNAME / MONGO_PASSWORD / MONGO_HOST / MONGO_PORT (used in Kubernetes,
         credentials are injected from a Secret). Credentials are URL-encoded so
         special characters in the password do not break the URI.
    """
    uri = os.environ.get("MONGODB_URI")
    if uri:
        return uri

    user = os.environ.get("MONGO_USERNAME")
    password = os.environ.get("MONGO_PASSWORD")
    host = os.environ.get("MONGO_HOST", "localhost")
    port = os.environ.get("MONGO_PORT", "27017")

    if user and password:
        return (
            f"mongodb://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/?authSource=admin"
        )
    return f"mongodb://{host}:{port}/"


# MongoClient connects lazily, so the app still starts if MongoDB is not ready yet.
client = MongoClient(build_mongo_uri(), serverSelectionTimeoutMS=5000)
db = client.flask_db
collection = db.data


@app.route("/")
def index():
    return f"Welcome to the Flask app! The current time is: {datetime.now()}"


@app.route("/data", methods=["GET", "POST"])
def data():
    if request.method == "POST":
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Request body must be valid JSON"}), 400
        collection.insert_one(payload)
        return jsonify({"status": "Data inserted"}), 201

    documents = list(collection.find({}, {"_id": 0}))
    return jsonify(documents), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
