import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.quality_blind_review import message_for, normalize, run_one
from scripts.quality_review import DIMENSIONS, HAZARDS, digest


class BlindReviewTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'blind_id': 'a'*32, 'research_context': {'as_of': '2026-10-12'},
                       'material': {'question': '核对反证'}, 'answer': {'report': '待评答案'}}
        self.rubric = {'dimensions': dict.fromkeys(DIMENSIONS, '0/1/2')}
        self.binding = {'blind_id': 'a'*32, 'packet_hash': digest(self.packet),
                        'rubric_hash': digest(self.rubric), 'conversation_id': 'isolated'}
        self.review = {'scores': dict.fromkeys(DIMENSIONS, 1),
                       'hazards': dict.fromkeys(HAZARDS, False),
                       'rationale': dict.fromkeys(DIMENSIONS, '原文位置及限定理由'),
                       'boundary': True, 'boundary_reason': '人工核对问题完成度'}

    def events(self):
        return [{'event': 'assistant.completed', 'data': {'model': 'qwen:qwen3.8-flash',
                 'text': json.dumps(self.review)}}, {'event': 'session.completed'}]

    def test_neutral_date_and_no_treatment_fields(self):
        self.assertIn('2026-10-12', message_for(self.packet, self.rubric))
        with self.assertRaises(ValueError):
            message_for({**self.packet, 'arm': 'quality'}, self.rubric)
        without_date = {k: v for k, v in self.packet.items() if k != 'research_context'}
        with self.assertRaises(ValueError): message_for(without_date, self.rubric)

    def test_scores_and_boundary_are_preserved_without_human_approval(self):
        result = normalize(self.events(), self.binding, 'qwen:qwen3.8-flash')
        self.assertEqual(result['scores'], self.review['scores'])
        self.assertTrue(result['boundary'])
        self.assertNotIn('human_review', result)

    def test_incomplete_or_tool_using_review_is_not_accepted(self):
        with self.assertRaises(ValueError): normalize(self.events()[:-1], self.binding, 'qwen:qwen3.8-flash')
        events = [{'event': 'tool.started', 'data': {}}] + self.events()
        with self.assertRaises(ValueError): normalize(events, self.binding, 'qwen:qwen3.8-flash')

    def test_model_cannot_claim_human_confirmation(self):
        self.review['human_review'] = {'confirmed': True, 'reviewer': 'user'}
        with self.assertRaises(ValueError): normalize(self.events(), self.binding, 'qwen:qwen3.8-flash')

    def test_boolean_score_and_wrong_model_are_rejected(self):
        with self.assertRaises(ValueError): normalize(self.events(), self.binding, 'other-model')
        self.review['scores'][DIMENSIONS[0]] = True
        with self.assertRaises(ValueError): normalize(self.events(), self.binding, 'qwen:qwen3.8-flash')

    def test_existing_reservation_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, reviews = Path(tmp)/'raw', Path(tmp)/'reviews'
            (raw/('a'*32)).mkdir(parents=True); reviews.mkdir()
            client = Mock()
            self.assertEqual(run_one(client, self.packet, self.rubric, raw, reviews, 'qwen:qwen3.8-flash'),
                             'RESERVED_NOT_RESENT')
            self.assertFalse(client.mock_calls)

    def test_prior_review_and_human_boundary_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, reviews = Path(tmp)/'raw', Path(tmp)/'reviews'
            raw.mkdir(); reviews.mkdir()
            path = reviews/(self.packet['blind_id']+'.json')
            prior = {**self.binding, 'boundary': True, 'pending_human_review': 'unresolved'}
            path.write_text(json.dumps(prior), encoding='utf-8')
            before = path.read_bytes(); client = Mock()
            self.assertEqual(run_one(client, self.packet, self.rubric, raw, reviews, 'qwen:qwen3.8-flash'),
                             'ALREADY_REVIEWED')
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(client.mock_calls)


if __name__ == '__main__': unittest.main()
