import json
import time
from pathlib import Path
from studio.api import create_app
from studio.config import Settings
from studio.domain import Project, Clip, ExportSettings
from fastapi.testclient import TestClient


def test_export_reuses_bytes_but_keeps_each_users_snapshot_and_access(tmp_path):
    app = create_app(Settings(tmp_path, tmp_path/'data', tmp_path/'upstream', tmp_path/'none.pt'))
    with TestClient(app):
        studio = app.state.studio
        original = tmp_path/'source.wav'; original.write_bytes(b'fixture')
        first = Project(name='Alice', owner='alice', source_name='source.wav', source_file=str(original), duration_ms=10000, has_video=False,
                        clips=[Clip(start_ms=0,end_ms=3000,export_settings=ExportSettings(video=False))])
        second = first.model_copy(deep=True,update={'id':'b'*32,'name':'Bob','owner':'bob'})
        second.clips[0].id='c'*32
        for p in (first, second): studio.store.create(p)
        calls=[]
        def render(project, clip, output):
            calls.append(project.id);output.mkdir(parents=True,exist_ok=True)
            path=output/'clip.mp3';path.write_bytes(b'encoded-audio');return [path]
        studio.media.render=render
        for project in (first,second):
            j=studio.export(project.id,project.clips[0].id,owner=project.owner)
            deadline=time.monotonic()+5
            while studio.store.get_job(j.id).status in {'queued','running'} and time.monotonic()<deadline:time.sleep(.01)
            assert studio.store.get_job(j.id).status=='succeeded'
            snapshot=json.loads((studio.data/'jobs'/j.id/'project.snapshot.json').read_text())
            assert snapshot['owner']==project.owner and snapshot['id']==project.id
        assert calls==[first.id]


def test_shared_preview_cache_preserves_copy_references_and_active_work(tmp_path):
    app=create_app(Settings(tmp_path,tmp_path/'data',tmp_path/'upstream',tmp_path/'none.pt'))
    # Don't start maintenance while assembling this cache safety fixture.
    try:
        studio=app.state.studio
        original=tmp_path/'source.mp4';original.write_bytes(b'fixture')
        project=Project(name='copy',source_name='x',source_file=str(original),duration_ms=1000)
        studio.store.create(project)
        low=studio.preview_target(project);low.parent.mkdir(parents=True,exist_ok=True);low.write_bytes(b'preview')
        orphan=low.parent/('a'*64+'-low320-v1.mp4');orphan.write_bytes(b'unused')
        cache=app.state.cache
        assert cache.clean()['removed_files']==1
        assert low.exists() and not orphan.exists()
    finally:
        app.state.recorder.close();app.state.training.close();app.state.studio.runner.close();app.state.lease.close()
