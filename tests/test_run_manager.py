import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import HTTPException

from bff.schemas import UIStreamEvent
from core.conversation_store import ConversationStore
from core.run_manager import RunManager


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conversations = ConversationStore(Path(self.tmp.name)/'conversations.db')
        self.manager = RunManager(self.conversations)
        self.addAsyncCleanup(self.manager.close)
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.calls = 0

    def start(self, factory, request='one', workspace='default'):
        return self.manager.start(workspace_id=workspace, conversation_id=None,
            request_id=request, kind='chat', payload={'message': 'test'}, factory=factory)

    async def producer(self):
        self.calls += 1
        yield UIStreamEvent(event='session.started')
        self.entered.set()
        await self.release.wait()
        yield UIStreamEvent(event='assistant.delta', data={'text':'done'})
        yield UIStreamEvent(event='session.completed')

    async def test_observer_disconnect_does_not_cancel_and_reconnect_replays_once(self):
        run = self.start(self.producer)
        observer = self.manager.subscribe(run)
        first = await anext(observer)
        await observer.aclose()
        self.assertEqual(self.manager.store.get(run)['status'], 'RUNNING')
        self.release.set()
        events = [e async for e in self.manager.subscribe(run, first['seq'])]
        self.assertEqual([e['event'] for e in events], ['assistant.delta','session.completed','run.updated'])
        self.assertEqual(len({e['seq'] for e in events}), len(events))
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.manager.store.get(run)['status'], 'SUCCEEDED')

    async def test_duplicate_request_subscribes_existing_run(self):
        run = self.start(self.producer)
        self.assertEqual(self.start(self.producer), run)
        with self.assertRaisesRegex(Exception, '另一份输入'):
            self.start(self.producer, workspace='other')
        self.release.set()
        _ = [e async for e in self.manager.subscribe(run)]
        self.assertEqual(self.calls, 1)

    async def test_error_then_session_completed_is_not_success(self):
        async def failure():
            yield UIStreamEvent(event='error', data={'message':'no report'})
            yield UIStreamEvent(event='session.completed')
        run = self.start(failure)
        _ = [e async for e in self.manager.subscribe(run)]
        self.assertEqual(self.manager.store.get(run)['status'], 'FAILED')
        self.assertEqual(self.manager.store.get(run)['error'], 'no report')

    async def test_terminal_report_projection_does_not_show_stale_in_progress(self):
        async def done():
            yield UIStreamEvent(event='session.completed')
        run = self.start(done)
        self.manager.store.update(run, checkpoint={'research':{'record':{'status':'IN_PROGRESS'}}})
        _ = [e async for e in self.manager.subscribe(run)]
        self.assertEqual(self.manager.public(self.manager.store.get(run))['research_status'], 'DELIVERY_INCOMPLETE')
        checkpoint = self.manager.store.get(run)['checkpoint']
        checkpoint['published'] = {'path':'/workspace/report.html','title':'Actual published report','sha256':'a'*64}
        self.manager.store.update(run, checkpoint=checkpoint)
        row = self.manager.public(self.manager.store.get(run))
        self.assertEqual(row['research_status'], 'PUBLISHED')
        self.assertEqual(row['resume_blocker'], '')

    async def test_known_terminal_without_checkpoint_is_not_labelled_unknown(self):
        run = self.start(self.producer)
        await self.entered.wait()
        control = self.manager.live[run]
        control.begin_step('模型请求')
        control.end_step(confirmed=True)
        public = self.manager.public(self.manager.store.get(run))
        self.assertFalse(public['resumable'])
        self.assertIn('步骤已结束', public['resume_blocker'])
        control.end_step(confirmed=False)
        control.end_step(confirmed=True)
        self.assertIn('未确认', self.manager.public(self.manager.store.get(run))['resume_blocker'])
        self.release.set()

    async def test_immediate_stop_before_producer_started(self):
        run = self.start(self.producer)
        await self.manager.action(run, 'stop')
        self.assertEqual(self.manager.store.get(run)['status'], 'STOPPED')
        self.assertEqual(self.calls, 0)
        self.assertNotIn(run, self.manager.live)

    async def test_unconfirmed_process_cleanup_keeps_run_owned_until_explicit_retry(self):
        parked=asyncio.Event(); ready=False
        async def confirm():
            if not ready: raise RuntimeError('not stopped')
        async def producer():
            yield UIStreamEvent(event='session.started')
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                parked.set()
                await self.manager.live[run].wait_for_cleanup(confirm)
                raise
        run=self.start(producer)
        await asyncio.sleep(0)
        first=asyncio.create_task(self.manager.action(run,'stop'))
        await parked.wait(); await asyncio.sleep(0)
        self.assertIn(run,self.manager.live)
        self.assertEqual(self.manager.store.get(run)['status'],'STOPPING')
        with self.assertRaises(HTTPException): await self.manager.action(run,'pause')
        with self.assertRaises(HTTPException): await self.manager.action(run,'resume')
        with self.assertRaises(HTTPException): await self.manager.action(run,'stop')
        ready=True
        await self.manager.action(run,'stop'); await first
        self.assertEqual(self.manager.store.get(run)['status'],'STOPPED')

    async def test_pause_waits_boundary_and_resume_keeps_same_execution(self):
        async def producer():
            yield UIStreamEvent(event='session.started')
            await self.release.wait()
            await self.manager.live[run].boundary({'safe':True,'step':1})
            self.entered.set()
            yield UIStreamEvent(event='session.completed')
        run = self.start(producer)
        await asyncio.sleep(0)
        await self.manager.action(run, 'pause')
        self.assertEqual(self.manager.store.get(run)['status'], 'PAUSING')
        self.release.set()
        await asyncio.sleep(0.01)
        self.assertEqual(self.manager.store.get(run)['status'], 'PAUSED')
        self.assertFalse(self.entered.is_set())
        await self.manager.action(run, 'resume')
        _ = [e async for e in self.manager.subscribe(run)]
        self.assertTrue(self.entered.is_set())

    async def test_restart_marks_orphan_interrupted_without_automatic_execution(self):
        self.manager.store.create('old',workspace_id='default',conversation_id=None,
            request_id='old',kind='chat',payload={})
        reloaded = RunManager(self.conversations)
        self.assertEqual(reloaded.store.get('old')['status'], 'INTERRUPTED')
        self.assertEqual(reloaded.live, {})

    async def test_journal_references_large_result_and_does_not_store_reasoning(self):
        conv = self.conversations.create_conversation(title='test')
        message = self.conversations.add_message(conv.id,role='tool',tool_name='web_fetch',
            tool_result={'content':'source'*1000},content='fetched')
        async def producer():
            yield UIStreamEvent(event='session.started',conversation_id=conv.id)
            yield UIStreamEvent(event='reasoning.delta',data={'text':'private-thinking'})
            yield UIStreamEvent(event='tool.completed',message_id=message.id,
                data={'name':'web_fetch','result':{'content':'source'*1000}})
        run = self.start(producer)
        events = [e async for e in self.manager.subscribe(run)]
        stored = self.manager.store.events(run)
        self.assertNotIn('private-thinking', json.dumps(stored))
        self.assertNotIn('sourcesource', json.dumps(stored))
        completed = next(e for e in events if e['event']=='tool.completed')
        self.assertEqual(completed['data']['result']['content'], 'source'*1000)

    async def test_api_snapshots_events_and_origin_protected_control(self):
        from bff.app import app
        service = SimpleNamespace(runs=self.manager, control_run=self.manager.action,
                                  run_summary=self.manager.public)
        run = self.start(self.producer)
        await self.entered.wait()
        with patch('bff.runs.get_chat_service', return_value=service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://127.0.0.1:9510') as client:
                snapshot = await client.get('/api/runs/'+run)
                self.assertEqual(snapshot.json()['status'], 'RUNNING')
                self.assertNotIn('payload',snapshot.json())
                denied = await client.post('/api/runs/'+run+'/control', json={'action':'stop'})
                self.assertEqual(denied.status_code,403)
                stopped = await client.post('/api/runs/'+run+'/control', json={'action':'stop'},
                    headers={'Origin':'http://127.0.0.1:9510'})
                self.assertEqual(stopped.json()['status'],'STOPPED')
                replay = await client.get('/api/runs/'+run+'/events?after_seq=0')
                rows = [json.loads(line) for line in replay.text.splitlines()]
                self.assertEqual(rows[-1]['data']['run']['status'],'STOPPED')
                self.assertEqual((await client.get('/api/runs/missing')).status_code,404)
