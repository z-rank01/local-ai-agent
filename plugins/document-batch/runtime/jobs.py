"""Sequential batch execution with per-file receipts, cancellation and restart truth."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from .records import Records, digest
from .ocr import convert


class Jobs:
    def __init__(self, root, resolve, worker=convert):
        self.root, self.resolve, self.worker = Path(root), resolve, worker
        self.records = Records(self.resolve('.plugin-state/document-batch'))
        self.records.recover()
        self.pending = {}
        self.serial = asyncio.Lock()
        self.accepting = True

    def submit(self, identity, paths, language, context):
        self.records.path(identity)
        if self.records.path(identity).exists():
            existing = self.records.get(identity)
            if existing['request'] != {'paths': paths, 'language': language, 'context': context}:
                raise ValueError('任务 ID 已用于其他请求')
            return existing
        if not self.accepting:
            raise ValueError('服务正在收尾，不接收新任务')
        if language not in ('eng', 'chi_sim', 'eng+chi_sim'):
            raise ValueError('仅支持英文、简体中文或中英混合')
        if not isinstance(paths, list) or not 1 <= len(paths) <= 50:
            raise ValueError('每批需要 1 至 50 个明确文件')
        files, seen = [], set()
        for supplied in paths:
            path = self.resolve(supplied)
            if path in seen or path.suffix.lower() not in ('.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff'):
                raise ValueError('文件重复或格式不支持')
            if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError('文件不存在或大于 100 MiB')
            seen.add(path)
            files.append({'source': supplied, 'source_sha256': digest(path), 'state': 'PENDING'})
        record = {'id': identity, 'state': 'QUEUED', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'request': {'paths': paths, 'language': language, 'context': context}, 'files': files}
        self.records.save(record)
        cancel = asyncio.Event()
        task = asyncio.create_task(self.execute(record, cancel))
        self.pending[identity] = (task, cancel)
        task.add_done_callback(lambda _: self.pending.pop(identity, None))
        return record

    async def execute(self, record, cancel):
        async with self.serial:
            record['state'] = 'RUNNING'
            self.records.save(record)
            try:
                output = self.resolve('reports/document-batch/' + record['id'])
                output.mkdir(parents=True, exist_ok=False)
                for index, item in enumerate(record['files']):
                    if cancel.is_set():
                        item['state'] = 'CANCELLED'
                        continue
                    item['state'] = 'RUNNING'
                    self.records.save(record)
                    try:
                        source = self.resolve(item['source'])
                        if digest(source) != item['source_sha256']:
                            raise ValueError('原文件在提交后发生变化')
                        target = output / f'{index + 1:03d}.pdf'
                        receipt = await self.worker(source, target, record['request']['language'], cancel)
                        if digest(source) != item['source_sha256']:
                            raise ValueError('原文件在处理期间发生变化，输出未认证')
                        item.update(state='SUCCEEDED', pdf=str(target.relative_to(self.root)),
                            text=str(target.with_suffix('.txt').relative_to(self.root)),
                            pdf_sha256=digest(target), text_sha256=digest(target.with_suffix('.txt')), **receipt)
                    except asyncio.CancelledError:
                        item['state'] = 'CANCELLED'
                        cancel.set()
                    except Exception as exc:
                        item.update(state='FAILED', error=str(exc))
                    self.records.save(record)
                states = {item['state'] for item in record['files']}
                record['state'] = ('CANCELLED' if cancel.is_set() else 'SUCCEEDED' if states == {'SUCCEEDED'}
                                   else 'PARTIAL' if 'SUCCEEDED' in states else 'FAILED')
            except Exception as exc:
                record.update(state='FAILED', error=str(exc))
            finally:
                self.records.save(record)

    async def cancel(self, identity):
        identity = self.records.resolve(identity)
        current = self.pending.get(identity)
        if current:
            current[1].set()
            await asyncio.shield(current[0])
        return self.records.get(identity)

    async def drain(self):
        self.accepting = False
        current = list(self.pending.values())
        for _, cancel in current:
            cancel.set()
        await asyncio.gather(*(asyncio.shield(task) for task, _ in current))
        return {'drained': True}
