"""Bounded, cancellable Linux process groups with incremental output."""
import asyncio
import codecs
import os
import signal
import time
from pathlib import Path

from sandbox import _SAFE_ENV


async def stream_shell(command, timeout=60, cwd='/workspace', argv=None):
    root = Path('/workspace').resolve()
    workdir = Path(cwd).resolve()
    if not workdir.is_relative_to(root) or not workdir.is_dir():
        raise ValueError('工作目录必须是 /workspace 内的实际目录')
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*(argv or ['/bin/sh', '-c', command]), cwd=str(workdir),
        env=_SAFE_ENV, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    queue = asyncio.Queue(maxsize=64)
    cap = 51200
    output = {'stdout':'', 'stderr':''}
    truncated = False

    async def read(pipe, name):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        while chunk := await pipe.read(2048):
            await queue.put((name, decoder.decode(chunk)))
        tail = decoder.decode(b'', final=True)
        if tail:
            await queue.put((name, tail))

    readers = [asyncio.create_task(read(proc.stdout,'stdout')), asyncio.create_task(read(proc.stderr,'stderr'))]
    timed_out = False
    try:
        yield {'event':'started', 'pid':proc.pid, 'shell':'/bin/sh', 'cwd':str(workdir)}
        while proc.returncode is None or any(not t.done() for t in readers) or not queue.empty():
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                timed_out = True
                break
            try:
                name, chunk = await asyncio.wait_for(queue.get(), min(0.5, remaining))
                room = cap - len(output[name])
                if len(chunk) > room:
                    truncated = True
                chunk = chunk[:max(0,room)]
                output[name] += chunk
                if chunk:
                    yield {'event':'output', 'stream':name, 'text':chunk, 'elapsed':time.monotonic()-start}
            except asyncio.TimeoutError:
                yield {'event':'progress', 'elapsed':time.monotonic()-start, 'pid':proc.pid}
        if timed_out:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if timed_out:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            async def drain(pipe):
                while await pipe.read(8192):
                    pass
            await asyncio.gather(drain(proc.stdout), drain(proc.stderr))
        await proc.wait()
        yield {'event':'result', 'result':{**output, 'exit_code':proc.returncode,
            'timed_out':timed_out, 'truncated':truncated, 'cwd':str(workdir), 'shell':'/bin/sh',
            'error':'命令超时，进程组已停止' if timed_out else None}}
    finally:
        # Also reap descendants whose parent exited while leaving inherited pipes open.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        async def discard(pipe):
            while await pipe.read(8192):
                pass
        await asyncio.gather(discard(proc.stdout), discard(proc.stderr))
        await proc.wait()


async def stream_python(code, timeout=30, args=None):
    import tempfile
    import sys
    from contextlib import aclosing
    fd, name = tempfile.mkstemp(suffix='.py', dir='/tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(code)
        async with aclosing(stream_shell('', timeout=timeout, argv=[sys.executable, '-u', name, *(args or [])])) as stream:
            async for item in stream:
                if item.get('event') == 'result':
                    item['result']['runtime'] = 'python'
                    item['result'].pop('shell', None)
                yield item
    finally:
        Path(name).unlink(missing_ok=True)
