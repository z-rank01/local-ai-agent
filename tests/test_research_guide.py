import unittest
from scripts.build_research_guide import scoped_calls


class ResearchGuideTests(unittest.TestCase):
    def test_revisions_and_linked_continuations_share_only_their_actual_calls(self):
        calls = [{'at': str(i)} for i in range(7)]
        original, scope = scoped_calls(calls, [{'start':'0','end':'2'}])
        revision, _ = scoped_calls(calls, [{'start':'3','end':'4'}, {'start':'5','end':'6'}])
        self.assertEqual(original, calls[:3])
        self.assertEqual(revision, calls[3:])
        self.assertIn('关联运行', scope)
        historical, scope = scoped_calls(calls, [])
        self.assertEqual(historical, calls)
        self.assertIn('不作为单份研究', scope)
