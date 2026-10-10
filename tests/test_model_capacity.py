import unittest
from unittest.mock import patch
from core.model_capacity import context_capacity


class ModelCapacityTests(unittest.TestCase):
    def test_declared_capacity_is_not_replaced_by_generic_setting(self):
        with patch('core.model_capacity.config.CONTEXT_WINDOW', 32768):
            self.assertEqual(context_capacity({'context_length': 131072}), (131072, 'model_catalog'))

    def test_unknown_capacity_is_explicit_and_matches_runtime_fallback(self):
        with patch('core.model_capacity.config.CONTEXT_WINDOW', 262144):
            for spec in ({}, {'context_window': True}, {'context_length': '131072'}):
                self.assertEqual(context_capacity(spec), (32768, 'conservative_fallback'))
