"""OCR subprocess with a bounded lifetime; only confirmed output becomes an artifact."""
import asyncio
import os
import signal


async def convert(source, output, language, cancel):
    command = ['ocrmypdf', '--skip-text', '--output-type', 'pdf', '--optimize', '0',
               '--jobs', '1', '--tesseract-timeout', '180', '-l', language]
    if source.suffix.lower() != '.pdf':
        command += ['--image-dpi', '300']
    command += [str(source), str(output)]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    communication = asyncio.create_task(process.communicate())
    cancelled = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait([communication, cancelled], timeout=1800,
                                      return_when=asyncio.FIRST_COMPLETED)
        if communication not in done:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(communication), 10)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await communication
            if cancel.is_set():
                raise asyncio.CancelledError()
            raise TimeoutError('单文件 OCR 超过 30 分钟，进程已停止')
        log, _ = communication.result()
        if process.returncode:
            raise RuntimeError(f'OCR exit {process.returncode}: ' + log.decode('utf-8', errors='replace')[-2000:])
    finally:
        cancelled.cancel()
    # OCR sidecars omit pre-existing text. Extract every page of the completed PDF instead.
    import fitz
    with fitz.open(output) as document:
        text = '\n\n'.join(f'--- Page {i + 1} ---\n{page.get_text()}' for i, page in enumerate(document))
        pages = len(document)
    output.with_suffix('.txt').write_text(text, encoding='utf-8')
    return {'pages': pages, 'text_characters': len(text)}
