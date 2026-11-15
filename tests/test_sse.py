import json
import time
import unittest
from app import app, broadcast_event

class TestServerSentEvents(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_sse_endpoint_stream(self):
        # Open GET request to /api/events/stream
        response = self.client.get("/api/events/stream")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        
        # Read the first event frame (the connected handshake)
        first_chunk = next(response.response).decode("utf-8")
        self.assertIn("event: connected", first_chunk)
        self.assertIn("data:", first_chunk)
        self.assertIn('"status": "connected"', first_chunk)

if __name__ == "__main__":
    unittest.main()

