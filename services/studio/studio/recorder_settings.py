"""Whitelisted BREC 2.20 settings. Never return its raw configuration or Cookie.

Verified against the v2.20.0 Web/Models/Config.gen.cs, ConfigController.cs,
Core/Config/V3/Config.gen.cs, RecordTaskBase.ParseAllowedQn and
StandardRecordTask.StatsRule_StatsUpdated in the official recorder source.
"""
import hashlib
import hmac
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from .recorder import RecorderUnavailable
from .store import ConflictError


class RecorderPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    recording_quality: str = Field(min_length=1, max_length=160)
    split_mode: Literal['disabled', 'time', 'size']
    split_value: int = Field(ge=0, le=2**32 - 1)
    split_by_title: bool
    record_danmaku: bool
    record_danmaku_super_chat: bool
    record_danmaku_gift: bool
    record_danmaku_guard: bool
    save_cover: bool
    retry_seconds: float = Field(ge=1, le=120)
    check_interval_seconds: int = Field(ge=30, le=3600)

    @field_validator('recording_quality')
    @classmethod
    def quality(cls, value):
        tokens = re.split(r'[,，、\s]+', value.strip().lower())
        allowed = {80, 150, 250, 400, 401, 10000, 20000, 30000}
        if not 1 <= len(tokens) <= 8:
            raise ValueError('画质优先级需包含1到8项')
        normalized = []
        for token in tokens:
            match = re.fullmatch(r'(avc|hevc)?([0-9]{1,5})', token)
            if not match or int(match[2]) not in allowed:
                raise ValueError('画质优先级包含不支持的选项')
            item = (match[1] or 'avc') + str(int(match[2]))
            if item not in normalized:
                normalized.append(item)
        return ','.join(normalized)

    @model_validator(mode='after')
    def ranges(self):
        if self.split_mode == 'time' and not 1 <= self.split_value <= 1440:
            raise ValueError('按时间分段需为1到1440分钟')
        if self.split_mode == 'size' and not 16 <= self.split_value <= 65536:
            raise ValueError('按大小分段需为16到65536MiB')
        if not math.isfinite(self.retry_seconds) or abs(self.retry_seconds * 1000 - round(self.retry_seconds * 1000)) > 1e-6:
            raise ValueError('重试间隔最多支持毫秒精度')
        return self


class RecorderSettingsService:
    # Store/application paths, API hosts, scripts, credentials and callbacks are
    # deliberately absent. They must never pass through a generic config proxy.
    FIELDS = {
        'recording_quality': ('RecordingQuality', str),
        'split_mode': ('CuttingMode', int),
        'split_value': ('CuttingNumber', int),
        'split_by_title': ('CuttingByTitle', bool),
        'record_danmaku': ('RecordDanmaku', bool),
        'record_danmaku_super_chat': ('RecordDanmakuSuperChat', bool),
        'record_danmaku_gift': ('RecordDanmakuGift', bool),
        'record_danmaku_guard': ('RecordDanmakuGuard', bool),
        'save_cover': ('SaveStreamCover', bool),
        'retry_seconds': ('TimingStreamRetry', int),
        'check_interval_seconds': ('TimingCheckInterval', int),
    }
    MODES = ('disabled', 'time', 'size')
    QUALITY_OPTIONS = [
        {'value': 'avc10000,hevc10000', 'label': '原画优先（H.264优先）'},
        {'value': 'avc10000', 'label': '仅H.264原画'},
        {'value': 'avc10000,avc400,avc250,avc150,avc80', 'label': 'H.264优先，允许降低画质'},
        {'value': 'hevc10000,avc10000', 'label': '原画优先（HEVC优先）'},
        {'value': 'avc400,avc250,avc150,avc80', 'label': 'H.264蓝光优先，允许降低画质'},
    ]

    def __init__(self, client):
        self.client = client

    def _snapshot(self):
        current = self.client.request('GET', '/api/config/global')
        defaults = self.client.request('GET', '/api/config/default')
        if not isinstance(current, dict) or not isinstance(defaults, dict):
            raise RecorderUnavailable('录播姬设置格式不兼容，请检查适配版本')
        effective, selected = {}, {}
        for key, (upstream, expected_type) in {**self.FIELDS, '_cookie': ('Cookie', str)}.items():
            name = 'optional' + upstream
            value = current.get(name)
            if not isinstance(value, dict) or type(value.get('hasValue')) is not bool:
                raise RecorderUnavailable('录播姬设置字段不兼容，请检查适配版本')
            default_key = upstream[0].lower() + upstream[1:]
            # A missing field is not an empty value: incomplete upstream responses
            # must never be converted into a valid snapshot that can be saved.
            source, source_key = (value, 'value') if value['hasValue'] else (defaults, default_key)
            if source_key not in source:
                raise RecorderUnavailable('录播姬设置字段不完整，请重新读取或检查适配版本')
            raw = source[source_key]
            if raw is None and key == '_cookie':
                raw = ''
            if type(raw) is not expected_type:
                raise RecorderUnavailable('录播姬设置类型不兼容，请检查适配版本')
            if expected_type is int and not 0 <= raw <= 2**32 - 1:
                raise RecorderUnavailable('录播姬设置数值异常，请检查适配版本')
            effective[key] = raw
            # Include default/effective values to reject an obsolete editing snapshot.
            selected[name] = {'override': value, 'effective': raw}
        if effective['split_mode'] not in range(3):
            raise RecorderUnavailable('录播姬分段模式不兼容，请检查适配版本')
        revision = hmac.new(self.client.config.secret.encode(),
                            b'recorder-settings-v1\0' + json.dumps(selected, sort_keys=True, ensure_ascii=False).encode(),
                            hashlib.sha256).hexdigest()
        return effective, revision

    def _public(self, effective, revision):
        values = {key: effective[key] for key in self.FIELDS}
        values['split_mode'] = self.MODES[effective['split_mode']]
        values['retry_seconds'] = effective['retry_seconds'] / 1000
        return {
            'available': True, 'message': '录播姬设置已读取', 'adapter_version': '2.20.0',
            'revision': revision, 'settings': values,
            'cookie_configured': bool(effective['_cookie'].strip()),
            'quality_options': self.QUALITY_OPTIONS,
            'notice': '这些是全局录制设置；房间单独设置会优先。画质控制原始录制，网页仍默认320p。改动由录播姬在相应步骤读取，部分设置下次录制生效。',
        }

    def read(self):
        try:
            with self.client.lock:
                effective, revision = self._snapshot()
                return self._public(effective, revision)
        except RecorderUnavailable as exc:
            # No invented defaults: the UI must not offer an editable form here.
            return {'available': False, 'message': str(exc), 'adapter_version': '2.20.0'}

    @staticmethod
    def _validate(body):
        if not isinstance(body, dict) or set(body) - {'revision', 'settings', 'cookie', 'clear_cookie'}:
            raise ValueError('录制设置请求格式无效')
        revision = body.get('revision')
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
            raise ValueError('请先重新读取录制设置')
        try:
            preferences = RecorderPreferences.model_validate(body.get('settings'))
        except ValidationError as exc:
            # Avoid echoing user input (including misplaced secrets) in 422 bodies.
            raise ValueError('录制设置无效：请检查画质、分段范围、重试间隔和开播检查间隔') from exc
        clear = body.get('clear_cookie', False)
        if type(clear) is not bool:
            raise ValueError('清除Cookie选项无效')
        cookie = body.get('cookie')
        if cookie is not None and not isinstance(cookie, str):
            raise ValueError('Cookie格式无效')
        if cookie is not None:
            cookie = cookie.strip()
            if cookie.lower().startswith('cookie:'):
                cookie = cookie[7:].strip()
            if len(cookie) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in cookie) or (cookie and '=' not in cookie):
                raise ValueError('Cookie格式无效，请粘贴Cookie内容，勿包含换行')
        if clear and cookie:
            raise ValueError('替换Cookie与清除Cookie不能同时操作')
        return preferences, revision, ('' if clear else cookie or None)

    def update(self, body):
        preferences, expected, cookie = self._validate(body)
        with self.client.lock:
            before, revision = self._snapshot()
            if not hmac.compare_digest(revision, expected):
                raise ConflictError('录播设置已更新，请重新读取后再保存')
            values = preferences.model_dump()
            values['split_mode'] = self.MODES.index(values['split_mode'])
            values['retry_seconds'] = round(values['retry_seconds'] * 1000)
            patch = {'optional' + self.FIELDS[key][0]: {'hasValue': True, 'value': value}
                     for key, value in values.items() if before[key] != value}
            if cookie is not None and cookie != before['_cookie']:
                patch['optionalCookie'] = {'hasValue': True, 'value': cookie}
            if not patch:
                return self._public(before, revision)
            self.client.request('POST', '/api/config/global', patch)
            effective, revision = self._snapshot()
            if any(effective[key] != value for key, value in values.items()) or (cookie is not None and not hmac.compare_digest(effective['_cookie'].encode(), cookie.encode())):
                raise ConflictError('录播姬未保持本次设置，请重新读取确认；不会自动重复提交')
            return self._public(effective, revision)
