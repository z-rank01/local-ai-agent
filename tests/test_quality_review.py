from copy import deepcopy
import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from scripts.quality_review import DIMENSIONS, HAZARDS, digest, blind_packet, summarize


class QualityReviewTests(unittest.TestCase):
    def test_packet_preserves_research_date_without_group_metadata(self):
        sample = self.manifest['samples'][0]
        result = next(r for r in self.results if r['sample'] == sample)
        case = {**self.case, 'as_of': '2026-10-12T00:00:00+08:00'}
        packet, binding = blind_packet(sample, result, case)
        self.assertEqual(packet['research_context'], {'as_of': case['as_of']})
        self.assertNotIn('arm', packet)
        self.assertNotIn('sample', packet)
        self.assertEqual(binding['packet_hash'], digest(packet))

    def setUp(self):
        self.rubric = {'dimensions': dict.fromkeys(DIMENSIONS, '0/1/2'),
                       'targets': {f'Q{i:02}': 'counterevidence' for i in range(1, 7)},
                       'gate': {'all_case_mean_noninferior': True,
                                'improved_target_dimensions_in_at_least_cases': 4,
                                'zero_tolerance': list(HAZARDS)}}
        self.case = {'material': {'question': '核对末尾反证', 'evidence': '原文否定此前合同'},
                     'input_hash': ''}
        self.case['input_hash'] = digest(self.case['material'])
        self.manifest = {'rubric_hash': digest(self.rubric),
                         'cases': [{'id': c, 'input_hash': self.case['input_hash']}
                                   for c in self.rubric['targets']], 'samples': []}
        self.results, self.bindings, self.reviews = [], [], []
        for case in self.rubric['targets']:
            for arm in ('original', 'quality'):
                for rep in (1, 2, 3):
                    sample = {'blind_id': digest([case, arm, rep])[:16],
                              'case_id': case, 'arm': arm, 'repetition': rep}
                    self.manifest['samples'].append(sample)
                    result = {'sample': sample, 'input_hash': self.case['input_hash'],
                              'output': {'run_status': 'COMPLETED', 'report': '合同撤销',
                                         'missing': ['仍须确认'], 'data': {
                                             'counter': '反证', 'model_execution': {'secret': 'metadata'},
                                             'reasoning_content': 'hidden'}}}
                    packet, binding = blind_packet(sample, result, self.case)
                    self.results.append(result); self.bindings.append(binding)
                    self.reviews.append({'blind_id': sample['blind_id'],
                        'packet_hash': digest(packet), 'rubric_hash': digest(self.rubric),
                        'scores': {d: 2 if arm == 'quality' else 1 for d in DIMENSIONS},
                        'hazards': dict.fromkeys(HAZARDS, False), 'boundary': False,
                        'rationale': '末尾原文撤销合同且回答保留条件', 'reviewer': 'offline-fixture'})

    def summary(self):
        return summarize(self.manifest, self.rubric, self.results, self.bindings, self.reviews)

    def test_full_gate_and_blind_packet(self):
        self.assertEqual(self.summary()['status'], 'PASS')
        before = deepcopy(self.results[0])
        packet, _ = blind_packet(self.manifest['samples'][0], before, self.case)
        self.assertNotIn('arm', packet)
        self.assertEqual(packet['answer']['data'], {'counter': '反证'})
        self.assertEqual(before, self.results[0])

    def test_missing_unknown_and_failed_never_pass(self):
        for status in ('UNKNOWN', 'FAILED', 'RUNNING'):
            self.results[0]['output']['run_status'] = status
            self.assertEqual(self.summary()['status'], 'INCOMPLETE')
        self.results.pop(0)
        self.assertEqual(self.summary()['result_states']['MISSING'], 1)

    def test_zero_tolerance(self):
        self.reviews[0]['hazards']['wrong_security'] = True
        self.assertEqual(self.summary()['status'], 'FAIL')

    def test_four_improvements_and_every_case_noninferior(self):
        for review in self.reviews:
            review['scores'] = dict.fromkeys(DIMENSIONS, 1)
        self.assertEqual(self.summary()['status'], 'FAIL')
        for sample, review in zip(self.manifest['samples'], self.reviews):
            if sample['arm'] == 'quality' and sample['case_id'] <= 'Q04':
                review['scores']['counterevidence'] = 2
        self.assertEqual(self.summary()['status'], 'PASS')
        for sample, review in zip(self.manifest['samples'], self.reviews):
            if sample['arm'] == 'quality' and sample['case_id'] == 'Q06':
                review['scores']['source_support'] = 0
        self.assertEqual(self.summary()['status'], 'FAIL')

    def test_boundary_requires_named_human(self):
        self.reviews[0]['boundary'] = True
        self.assertEqual(self.summary()['status'], 'INCOMPLETE')
        self.reviews[0]['human_review'] = {'confirmed': True, 'reviewer': 'human-fixture'}
        self.assertEqual(self.summary()['status'], 'PASS')

    def test_tampering_and_duplicates_rejected(self):
        self.results[0]['output']['report'] = 'changed'
        with self.assertRaises(ValueError): self.summary()
        self.setUp()
        self.reviews.append(self.reviews[0])
        with self.assertRaises(ValueError): self.summary()

    def test_missing_review_and_invalid_scores(self):
        self.reviews.pop()
        self.assertEqual(self.summary()['status'], 'INCOMPLETE')
        self.setUp()
        self.reviews[0]['scores']['source_support'] = True
        with self.assertRaises(ValueError): self.summary()

    def test_reduced_manifest_or_weakened_gate_rejected(self):
        self.manifest['samples'].pop()
        with self.assertRaises(ValueError): self.summary()
        self.setUp()
        self.rubric['gate']['improved_target_dimensions_in_at_least_cases'] = 3
        self.manifest['rubric_hash'] = digest(self.rubric)
        with self.assertRaises(ValueError): self.summary()

    def test_file_pack_summary_and_no_overwrite(self):
        from scripts.quality_review import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); batch = root/'batch'; batch.mkdir()
            def write(path, value): path.write_text(json.dumps(value), encoding='utf-8')
            write(batch/'manifest.json', self.manifest); write(batch/'rubric.json', self.rubric)
            for case in self.rubric['targets']: write(batch/(case+'.json'), self.case)
            for result in self.results:
                write(batch/('result-'+result['sample']['blind_id']+'.json'), result)
            packed = root/'packed'
            with patch('sys.argv', ['review', 'pack', str(batch), str(packed)]): main()
            self.assertFalse((packed/'reviewer'/'manifest.json').exists())
            self.assertFalse((packed/'reviewer'/'bindings.json').exists())
            reviews = root/'reviews'; reviews.mkdir()
            for review in self.reviews: write(reviews/(review['blind_id']+'.json'), review)
            args = ['review', 'summarize', str(batch), str(root/'summary'),
                    '--pack', str(packed), '--reviews', str(reviews)]
            with patch('sys.argv', args): main()
            self.assertEqual(json.loads((root/'summary/summary.json').read_text())['status'], 'PASS')
            with patch('sys.argv', args), self.assertRaises(FileExistsError): main()


if __name__ == '__main__': unittest.main()
