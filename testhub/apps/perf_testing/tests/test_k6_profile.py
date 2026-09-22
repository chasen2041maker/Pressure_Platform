"""The local k6 profile must not advertise unsupported data-factory functions."""
import os
from types import SimpleNamespace
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.pressure_settings')


class LocalK6ProfileTests(unittest.TestCase):
    def test_variable_function_catalog_is_an_explicit_empty_list(self):
        import django
        django.setup()
        from django.urls import resolve, Resolver404
        from rest_framework.test import APIRequestFactory, force_authenticate
        try:
            route = resolve('/api/data-factory/variable_functions/', urlconf='backend.pressure_urls')
        except Resolver404:
            self.fail('The local profile must return an empty catalog instead of 404')
        request = APIRequestFactory().get('/api/data-factory/variable_functions/')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = route.func(request, **route.kwargs)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])


if __name__ == '__main__':
    unittest.main()
