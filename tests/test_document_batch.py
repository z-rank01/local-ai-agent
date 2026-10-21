"""Batch behavior: original preservation, partial failure, cancellation and restart."""
import asyncio
import importlib
from pathlib import Path
import tempfile
import unittest

Jobs = importlib.import_module('plugins.document-batch.runtime.jobs').Jobs


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ('one.pdf', 'two.png'):
            (self.root / name).write_bytes(b'unchanged-original')

    def resolve(self, path):
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise PermissionError('outside workspace')
        return target

    async def test_partial_outputs_idempotency_and_restart_do_not_reprocess(self):
        calls = []
        async def worker(source, output, language, cancel):
            calls.append(source.name)
            if source.name == 'two.png':
                raise RuntimeError('fixture: unreadable scan')
            output.write_bytes(b'fixture-searchable-pdf')
            output.with_suffix('.txt').write_text('fixture readable text', encoding='utf-8')
            return {'pages': 1, 'text_characters': 21}
        jobs = Jobs(self.root, self.resolve, worker)
        context = {'conversation_id': 'a', 'run_id': 'b'}
        first = jobs.submit('a' * 64, ['one.pdf', 'two.png'], 'eng', context)
        self.assertEqual(first['state'], 'QUEUED')
        await jobs.pending[first['id']][0]
        result = jobs.records.get(first['id'])
        self.assertEqual(jobs.records.get(first['id'][:16]), result)
        self.assertEqual(result['state'], 'PARTIAL')
        self.assertEqual([row['state'] for row in result['files']], ['SUCCEEDED', 'FAILED'])
        self.assertEqual((self.root / result['files'][0]['text']).read_text(), 'fixture readable text')
        for path in ('one.pdf', 'two.png'):
            self.assertEqual((self.root / path).read_bytes(), b'unchanged-original')
        restarted = Jobs(self.root, self.resolve, worker)
        self.assertEqual(restarted.submit('a' * 64, ['one.pdf', 'two.png'], 'eng', context), result)
        self.assertEqual(calls, ['one.pdf', 'two.png'])
        with self.assertRaises(ValueError): restarted.submit('a' * 64, ['one.pdf'], 'eng', context)
        with self.assertRaises(PermissionError): restarted.submit('b' * 64, ['../outside.pdf'], 'eng', context)
        collision = {**result, 'id': 'a' * 16 + 'b' * 48}
        restarted.records.save(collision)
        with self.assertRaisesRegex(ValueError, '不唯一'):
            restarted.records.get('a' * 16)
        self.assertEqual(restarted.records.get(first['id']), result)
        result['state'] = 'RUNNING'
        result['files'][1]['state'] = 'RUNNING'
        jobs.records.save(result)
        recovered = Jobs(self.root, self.resolve, worker).records.get(first['id'])
        self.assertEqual(recovered['state'], 'INTERRUPTED')
        self.assertEqual(recovered['files'][1]['state'], 'UNKNOWN')
        self.assertEqual(recovered['files'][0]['state'], 'SUCCEEDED')

    async def test_drain_cancels_active_and_queued_files_before_returning(self):
        entered = asyncio.Event()
        async def worker(source, output, language, cancel):
            entered.set()
            await cancel.wait()
            raise asyncio.CancelledError()
        jobs = Jobs(self.root, self.resolve, worker)
        jobs.submit('c' * 64, ['one.pdf', 'two.png'], 'eng', {'conversation_id': 'a', 'run_id': 'b'})
        await entered.wait()
        self.assertEqual(await jobs.drain(), {'drained': True})
        record = jobs.records.get('c' * 64)
        self.assertEqual(record['state'], 'CANCELLED')
        self.assertTrue(all(row['state'] == 'CANCELLED' for row in record['files']))
        with self.assertRaises(ValueError): jobs.submit('d' * 64, ['one.pdf'], 'eng', {})
