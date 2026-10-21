"""Frozen specialist requests and ambiguous transport outcomes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from plugins.finance.generation import generate, validate_public
from core.public_address import public_url
from core.providers import CallBudget
from pathlib import Path
import tempfile
import sqlite3
import json
from contextlib import closing


PARAMS = dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536)


class DirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_independent_model_request_cannot_execute(self):
        runtime=SimpleNamespace(models=SimpleNamespace())
        with self.assertRaises(HTTPException) as result:
            await generate(runtime,dict(provider='qwen',model='qwen3.8-flash',generation=PARAMS,
                prompt='公开研究',execution_hash='a'*64,material={}))
        self.assertEqual(result.exception.status_code,409)

    def test_existing_ledger_backed_up_before_incremental_table(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=CallBudget(Path(directory)/'model-calls.sqlite',limit=1)
            ledger.reserve('legacy')
            ledger.begin_research('selected','a'*64,'b'*64,{})
            backup=Path(ledger.path).with_name('model-calls.before-research.sqlite')
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(db.execute('SELECT model FROM calls').fetchall(),[('legacy',)])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='research_receipts'").fetchone())

    def test_failed_backup_does_not_complete_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=CallBudget(Path(directory)/'model-calls.sqlite',limit=1)
            ledger.reserve('legacy')
            with patch('pathlib.Path.replace',side_effect=PermissionError('backup unavailable')):
                with self.assertRaises(PermissionError):ledger.begin_research('selected','a'*64,'b'*64,{})
            backup=Path(ledger.path).with_name('model-calls.before-research.sqlite')
            self.assertFalse(backup.exists())
            ledger.begin_research('selected','a'*64,'b'*64,{})
            self.assertTrue(backup.exists())


class PublicAddressTests(unittest.TestCase):
    def test_public_company_cash_is_not_account_cash(self):
        from copy import deepcopy
        financial = {'symbol': '688312', 'snapshot_id': 'a'*64,
                     'latest': {'ytd': {'cash': 12.5}}, 'periods': [{'ytd': {'cash': 10}}]}
        packet = {'material': {'instrument': {'symbol': '688312'},
            'evidence': {'financial': financial}, 'metrics': {'financial': {'ytd': {'cash': 12.5}}},
            'peers': [{'symbol': '688312', 'financial': deepcopy(financial)}]}}
        validate_public(packet)
        for location in ('root', 'nested', 'wrong_symbol', 'text', 'holdings'):
            changed = deepcopy(packet)
            if location == 'root': changed['material']['cash'] = 999
            elif location == 'nested': changed['material']['evidence']['cash'] = 999
            elif location == 'wrong_symbol': changed['material']['instrument']['symbol'] = '000001'
            elif location == 'text': changed['material']['evidence']['financial']['latest']['ytd']['cash'] = 'private text'
            else: changed['material']['evidence']['financial']['latest']['ytd']['holdings'] = []
            with self.subTest(location=location), self.assertRaises(HTTPException):
                validate_public(changed)

    def test_local_and_credential_urls_rejected(self):
        for url in ['https://127.0.0.1/x','https://[::1]/x','https://localhost/x',
                    'https://host.internal/x','https://u:p@example.com/x','http://example.com/x',
                    'https://example.com:9510/x','https://10.0.0.1/x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        self.assertEqual(public_url('https://example.com/news').hostname, 'example.com')
