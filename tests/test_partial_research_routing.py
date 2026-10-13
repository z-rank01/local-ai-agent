import unittest
from pathlib import Path
import yaml
from core.stock_bridge import _TOOL_ACTIONS


class PartialResearchRoutingTests(unittest.TestCase):
    def test_partial_stage_reaches_actual_bridge_action(self):
        action = _TOOL_ACTIONS['stock_research_resume']({
            'reference': 'a'*64, 'continuation_stage': 'partial_synthesis'})
        self.assertEqual(action, {'action': 'resume_research',
            'reference': {'type': 'task', 'token': 'a'*64},
            'continuation_stage': 'partial_synthesis'})

    def test_legacy_recovery_shape_is_unchanged(self):
        action = _TOOL_ACTIONS['stock_research_resume']({'reference': 'a'*64})
        self.assertNotIn('continuation_stage', action)

    def test_tool_exposes_optional_stage_without_new_entry(self):
        definition = yaml.safe_load((Path(__file__).resolve().parents[1] /
            'config/tools/stock_research_resume.yaml').read_text(encoding='utf-8'))
        self.assertEqual(definition['name'], 'stock_research_resume')
        self.assertEqual(definition['parameters']['required'], ['reference'])
        self.assertEqual(definition['parameters']['properties']['continuation_stage']['enum'],
                         ['partial_synthesis'])
