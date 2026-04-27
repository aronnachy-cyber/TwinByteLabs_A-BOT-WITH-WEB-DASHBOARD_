import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "SENTINEL-BOT-Main"))

from wsgi import app  # noqa: F401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
