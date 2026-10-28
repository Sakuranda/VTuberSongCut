"""Exercise deployment argument/credential handling without starting Docker."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]


def fixture(tmp_path):
    (tmp_path/'scripts').mkdir()
    shutil.copy2(ROOT/'scripts/deploy.sh',tmp_path/'scripts/deploy.sh')
    binaries=tmp_path/'bin';binaries.mkdir()
    docker=binaries/'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\ncase "$*" in *--configure-webhook*) [ "${FAIL_WEBHOOK:-0}" != 1 ] || exit 7 ;; esac\nexit 0\n')
    docker.chmod(0o755)
    env={**os.environ,'PATH':str(binaries)+os.pathsep+os.environ['PATH'],'DEPLOY_TEST_LOG':str(tmp_path/'commands')}
    for key in ('SONGCUT_DOMAIN','SONGCUT_HTTP_PORT'):env.pop(key,None)
    return env


def values(path):
    return dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)


@pytest.mark.skipif(os.name=='nt',reason='Linux/macOS shell deploy entry')
def test_recorder_one_command_bootstraps_once_and_preserves_credentials(tmp_path):
    env=fixture(tmp_path)
    command=['sh',str(tmp_path/'scripts/deploy.sh'),'--recorder']
    first=subprocess.run(command,env=env,capture_output=True,text=True)
    assert first.returncode==0,first.stderr
    config=values(tmp_path/'.env.server')
    assert len(config['SONGCUT_RECORDER_WEBHOOK_SECRET'])>=32
    assert (tmp_path/'.env.server').stat().st_mode & 0o777 == 0o600
    (tmp_path/'server-accounts.json').write_text('{}')
    second=subprocess.run(command,env=env,capture_output=True,text=True)
    assert second.returncode==0,second.stderr
    assert values(tmp_path/'.env.server')==config
    calls=(tmp_path/'commands').read_text()
    assert '-f deploy/compose.recorder.yaml' in calls
    assert '-f deploy/compose.accounts.yaml' in calls
    assert 'exec -T songcut python -m studio.recorder --configure-webhook' in calls
    assert config['SONGCUT_RECORDER_WEBHOOK_SECRET'] not in first.stdout+second.stdout


@pytest.mark.skipif(os.name=='nt',reason='Linux/macOS shell deploy entry')
def test_existing_recorder_credentials_survive_enabling(tmp_path):
    env=fixture(tmp_path)
    (tmp_path/'.env.server').write_text('SONGCUT_DOMAIN=\nSONGCUT_PUBLIC_ORIGIN=http://localhost:8765\nSONGCUT_RECORDER_ENABLED=0\nSONGCUT_RECORDER_USERNAME=existing\nSONGCUT_RECORDER_PASSWORD=existing-password-for-test\nSONGCUT_RECORDER_WEBHOOK_SECRET=existing-secret-for-test-1234567890')
    result=subprocess.run(['sh',str(tmp_path/'scripts/deploy.sh'),'--recorder'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    config=values(tmp_path/'.env.server')
    assert config['SONGCUT_RECORDER_USERNAME']=='existing'
    assert config['SONGCUT_RECORDER_PASSWORD']=='existing-password-for-test'
    assert config['SONGCUT_RECORDER_ENABLED']=='1'


@pytest.mark.skipif(os.name=='nt',reason='Linux/macOS shell deploy entry')
def test_webhook_bootstrap_failure_does_not_claim_success(tmp_path):
    env=fixture(tmp_path);env['FAIL_WEBHOOK']='1'
    result=subprocess.run(['sh',str(tmp_path/'scripts/deploy.sh'),'--recorder'],env=env,capture_output=True,text=True)
    assert result.returncode==7
    assert '拾歌已启动' not in result.stdout and '回调已配置' not in result.stdout
