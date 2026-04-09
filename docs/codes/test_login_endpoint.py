# codes/test_login_endpoint.py
# TODO: Sustituir por un test de integración real del login.
# Placeholder para el fragmento de código del capítulo de pruebas.

from rest_framework.test import APITestCase
from rest_framework import status
from users.factories import UserFactory


class TestLoginEndpoint(APITestCase):
    """Pruebas de integración del endpoint de inicio de sesión."""

    def setUp(self):
        self.user = UserFactory(
            email="test@uam.es",
            password="SecurePass123!",
        )
        self.url = "/api/v1/auth/login/"

    def test_login_success(self):
        """Login con credenciales válidas devuelve 200."""
        response = self.client.post(
            self.url,
            {
                "email": "test@uam.es",
                "password": "SecurePass123!",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
