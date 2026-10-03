import unittest
from unittest.mock import patch

from flask import Flask

from services.api.trainer import trainer_bp


class TestTrainerApi(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(trainer_bp)
        self.client = self.app.test_client()

    @patch("services.api.trainer.requests.post")
    def test_train_forwards_to_cache_service(self, post):
        post.return_value.status_code = 200

        response = self.client.post("/train")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["message"], "Cache refresh triggered")
        post.assert_called_once_with(
            "http://127.0.0.1:5002/internal/train",
            timeout=2,
        )


if __name__ == "__main__":
    unittest.main()