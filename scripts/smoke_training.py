"""Real two-step training -> model registration -> singing detection API smoke.
Uses synthetic audio in an isolated temporary workspace, never adds a test model
or synthetic annotations to the user's normal model library.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import time
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.config import Settings
from studio.adapters.media import FFmpegMedia

root = Path(__file__).resolve().parents[1]
output = root / "var/smoke"
output.mkdir(parents=True, exist_ok=True)
media = FFmpegMedia()
with TemporaryDirectory(prefix="training-api-", dir=output) as directory:
    work = Path(directory)
    config = Settings(root=root, data=work / "data", upstream=root / "upstream/AutoSlicer-v2", checkpoint=work / "absent.pt")
    with TestClient(create_app(config)) as client:
        headers = {"X-Studio-Token": client.get('/api/session').json()['token']}
        def post(path, **kwargs):
            response = client.post(path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        dataset = post('/api/training/datasets', json={"name": "合成流程验收", "target_name": "合成信号（不是真实主播）"})
        url = '/api/training/datasets/' + dataset['id']
        for index, split in enumerate(('train', 'validation')):
            source = work / f'{split}.wav'
            media.run(['-f', 'lavfi', '-i', f'sine=frequency={440+index*83}:sample_rate=16000', '-t', '8', str(source)])
            with source.open('rb') as stream:
                dataset = post(url + '/samples', data={"revision": dataset['revision']}, files={"file": (source.name, stream)})
            sample = dataset['samples'][-1]
            response = client.put(url + '/samples/' + sample['id'], headers=headers, json={
                "revision": dataset['revision'], "split": split, "reviewed": True,
                "regions": [{"start_ms": 0, "end_ms": 3200, "label": "target"}, {"start_ms": 5000, "end_ms": 7000, "label": "other"}]})
            response.raise_for_status()
            dataset = response.json()
        job = post(url + '/train', json={"revision": dataset['revision'], "settings": {
            "device": "auto", "steps": 2, "batch_size": 1, "window_s": 4, "val_every": 1, "warmup": 0}})
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            job = client.get('/api/training/jobs/' + job['id']).json()
            if job['status'] in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
                break
            time.sleep(.25)
        assert job['status'] == 'succeeded', job
        model = job['model']
        assert model['origin'] == 'trained' and model['classes'] == 1
        assert len(job['history']) == 2
        downloaded = client.get('/api/models/' + model['id'] + '/download')
        assert downloaded.status_code == 200 and len(downloaded.content) > 1_000_000
        with (work/'validation.wav').open('rb') as stream:
            project = post('/api/projects/upload', files={'file': ('validation.wav', stream)})
        project_url = '/api/projects/' + project['id']
        response = client.put(project_url, headers=headers, json={"revision": project['revision'], "clips": [],
                              "detector_model_id": model['id'], "detector_device": "auto"})
        response.raise_for_status()
        detect = post(project_url + '/detect')
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            detect = next(x for x in client.get('/api/jobs').json() if x['id'] == detect['id'])
            if detect['status'] in {'succeeded', 'failed'}:
                break
            time.sleep(.25)
        assert detect['status'] == 'succeeded', detect
        assert detect['model_id'] == model['id'] and detect['model_sha256'] == model['sha256']
        report = {'material': 'synthetic sine waves; not a singer-quality assessment', 'training_status': job['status'],
                  'steps': 2, 'validation_rows': len(job['history']), 'model_bytes': model['size_bytes'],
                  'model_sha256': model['sha256'], 'detection_status': detect['status'], 'actual_detection_device': detect['device'],
                  'checks': ['HTTP upload and labels', 'separate original training process', 'finite model validation',
                             'registered trained model', 'download', 'explicit model selection', 'real detection with new checkpoint']}
        (output/'training-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
