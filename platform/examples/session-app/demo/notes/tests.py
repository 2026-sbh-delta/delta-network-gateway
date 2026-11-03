import os
from tempfile import TemporaryDirectory
from unittest.mock import patch
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase

from demo.notes.models import Note


class SessionContinuityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="test-user", password="test-only-password")

    def test_session_survives_a_new_app_process_and_environment_label(self):
        self.assertTrue(self.client.login(username="test-user", password="test-only-password"))
        self.assertEqual(self.client.post("/", {"text": "before moving"}).status_code, 302)
        self.assertEqual(Session.objects.count(), 1)
        replacement = Client()
        replacement.cookies = self.client.cookies
        with patch.dict(os.environ, {"APP_ENVIRONMENT": "gcp-target"}):
            response = replacement.get("/")
            self.assertContains(response, "before moving")
            self.assertContains(response, "gcp-target")
            self.assertEqual(replacement.post("/", {"text": "after moving"}).status_code, 302)
        self.assertEqual(Note.objects.count(), 2)

    def test_csrf_and_owner_boundary(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post("/", {"text": "untrusted"}).status_code, 403)
        other = get_user_model().objects.create_user(username="other", password="test-only-password")
        Note.objects.create(owner=other, text="other user's record")
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/"), "other user's record")

    def test_application_write_fence_rejects_new_notes(self):
        self.client.force_login(self.user)
        with TemporaryDirectory() as root:
            fence = Path(root) / "write-paused"
            fence.touch()
            with patch.dict(os.environ, {"APP_WRITE_FENCE": str(fence)}):
                self.assertEqual(self.client.post("/", {"text": "paused"}).status_code, 503)
        self.assertEqual(Note.objects.count(), 0)

    def test_logout_invalidates_shared_session(self):
        self.client.force_login(self.user)
        replacement = Client()
        replacement.cookies = self.client.cookies.copy()
        self.assertEqual(self.client.post("/logout/").status_code, 302)
        self.assertRedirects(replacement.get("/"), "/login/?next=/")
