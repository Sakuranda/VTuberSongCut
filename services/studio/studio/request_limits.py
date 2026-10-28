"""Limits before multipart spooling and during media responses, per API process."""
import asyncio
import tempfile
import time
from urllib.parse import parse_qs
from pathlib import Path
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from .resources import integer, ResourceLimitError


class RequestLimits:
    def __init__(self, app, policy, data, server=False):
        self.app, self.policy, self.data, self.server = app, policy, Path(data), server
        self.uploads = 0
        self.max_uploads = integer("SONGCUT_UPLOAD_CONNECTIONS", 2, 1, 32)
        self.connections = 0
        self.high_connections = 0
        self.max_connections = integer('SONGCUT_MEDIA_CONNECTIONS', 12, 1, 1000)
        self.max_high = integer('SONGCUT_HD_CONNECTIONS', 2, 1, 100)
        self.bytes_per_second = integer('SONGCUT_MEDIA_MBPS', 10 if server else 0, 0, 10000) * 1000000 / 8
        self.next_send = 0.

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path']
        upload = scope['method'] in {'POST', 'PUT'} and (path.endswith('/upload') or path.endswith('/samples'))
        media = self.server and scope['method'] == 'GET' and (path.endswith('/media') or path.startswith('/api/artifacts/'))
        quality = parse_qs(scope.get('query_string', b'').decode('ascii', errors='replace')).get('quality', ['low'])[-1]
        high = media and (quality in {'high', 'source'} or (path.startswith('/api/artifacts/') and path.endswith('.mp4')))
        if upload and self.uploads >= self.max_uploads:
            return await JSONResponse({'detail': '上传通道正忙，请稍后重试'}, status_code=429,
                                      headers={'Retry-After': '10'})(scope, receive, send)
        if media and (self.connections >= self.max_connections or (high and self.high_connections >= self.max_high)):
            return await JSONResponse({'detail': '当前播放连接较多，请稍后重试或使用320p'}, status_code=429,
                                      headers={'Retry-After': '10'})(scope, receive, send)
        if upload:
            self.uploads += 1
        if media:
            self.connections += 1
            self.high_connections += int(high)
        total = 0
        rejected = None
        replaced = False

        async def bounded_receive():
            nonlocal total, rejected
            event = await receive()
            if upload and event['type'] == 'http.request':
                total += len(event.get('body', b''))
                try:
                    if total > self.policy.upload_bytes + 1024 * 1024:
                        raise ResourceLimitError('录播超过服务器上传限制', 413)
                    self.policy.check_disk(self.data)
                    self.policy.check_disk(Path(tempfile.gettempdir()))
                except ResourceLimitError as exc:
                    rejected = exc
                    # Starlette closes all partially spooled files on this exception.
                    raise MultiPartException(str(exc)) from exc
            return event

        async def bounded_send(event):
            nonlocal replaced
            if rejected:
                if not replaced:
                    replaced = True
                    await JSONResponse({'detail': str(rejected)}, status_code=rejected.status)(scope, receive, send)
                return
            if media and event['type'] == 'http.response.body' and self.bytes_per_second:
                count = len(event.get('body', b''))
                if count:
                    now = time.monotonic()
                    start = max(now, self.next_send)
                    self.next_send = start + count / self.bytes_per_second
                    if start > now:
                        await asyncio.sleep(start - now)
            await send(event)
        try:
            await self.app(scope, bounded_receive, bounded_send)
        finally:
            if upload:
                self.uploads -= 1
            if media:
                self.connections -= 1
                self.high_connections -= int(high)
