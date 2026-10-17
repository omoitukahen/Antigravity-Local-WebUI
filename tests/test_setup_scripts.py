"""Exercise the distributed PowerShell entrypoint without installing or calling CLI."""
import shutil
import subprocess
import sys
from pathlib import Path


def run_setup(tmp_path,*arguments):
    source=Path(__file__).resolve().parents[1]/'Setup.ps1'
    script=tmp_path/'Setup.ps1';shutil.copyfile(source,script)
    return subprocess.run(['pwsh','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),*arguments],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)


def test_missing_explicit_python_does_not_fall_back(tmp_path):
    result=run_setup(tmp_path,'-PythonPath',str(tmp_path/'missing.exe'),'-CheckOnly')
    assert result.returncode!=0 and '-PythonPath' in result.stderr
    assert not (tmp_path/'.venv').exists()


def test_missing_explicit_cli_does_not_fall_back(tmp_path):
    result=run_setup(tmp_path,'-PythonPath',sys._base_executable,'-CliPath',str(tmp_path/'missing-agy.exe'),'-CheckOnly')
    assert result.returncode!=0 and '-CliPath' in result.stderr
    assert not (tmp_path/'webui.local.json').exists()


def test_check_only_accepts_real_python_without_writing_environment(tmp_path):
    result=run_setup(tmp_path,'-PythonPath',sys._base_executable,'-CheckOnly')
    assert result.returncode==0,result.stderr
    assert 'Prerequisites OK' in result.stdout
    assert not (tmp_path/'.venv').exists() and not (tmp_path/'data').exists()


def test_incomplete_virtual_environment_is_preserved(tmp_path):
    environment=tmp_path/'.venv';environment.mkdir()
    sentinel=environment/'user-note.txt';sentinel.write_bytes(b'preserve')
    result=run_setup(tmp_path,'-PythonPath',sys._base_executable)
    assert result.returncode!=0 and '.venv' in result.stderr
    assert sentinel.read_bytes()==b'preserve'
