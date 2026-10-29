"""Public login/status entry; workspace access remains gated by session middleware."""
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .authentication import AuthenticationError


def auth_router(service):
    router = APIRouter()
    def failure(exc):
        headers = {'Cache-Control': 'no-store'}
        if exc.retry_after: headers['Retry-After'] = str(exc.retry_after)
        return JSONResponse({'detail': str(exc)}, status_code=exc.status, headers=headers)

    @router.get('/api/auth/status')
    def status(request: Request):
        try:
            service.require_same_origin(request)
            return JSONResponse(service.status(request), headers={'Cache-Control': 'no-store'})
        except AuthenticationError as exc:
            return failure(exc)

    @router.post('/api/auth/login')
    async def login(request: Request):
        try:
            service.require_same_origin(request)
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                raise AuthenticationError('请使用登录表单', 415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192: raise AuthenticationError('登录请求过大', 413)
            try: value = json.loads(body)
            except (ValueError, UnicodeError): raise AuthenticationError('登录信息格式无效', 422)
            if not isinstance(value, dict) or set(value) != {'username', 'password', 'code'}:
                raise AuthenticationError('请填写账号、密码和六位验证码', 422)
            token, identity = await run_in_threadpool(service.login, value['username'], value['password'], value['code'], request)
            response = JSONResponse({'server': True, 'mode': 'totp', 'authenticated': True, 'user': {'name': identity.name, 'role': identity.role}}, headers={'Cache-Control': 'no-store'})
            service.set_cookie(response, token)
            return response
        except AuthenticationError as exc: return failure(exc)

    @router.post('/api/auth/logout')
    def logout(request: Request):
        try:
            service.logout(request)
            response = JSONResponse({'authenticated': False}, headers={'Cache-Control': 'no-store'})
            service.clear_cookie(response)
            return response
        except AuthenticationError as exc: return failure(exc)
    return router
