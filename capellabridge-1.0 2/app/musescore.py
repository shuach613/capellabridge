"""Sandboxed-ish serial MuseScore command-line conversion and PDF rendering."""
from __future__ import annotations
import os
import subprocess
import shutil
from pathlib import Path

class ConversionFailure(Exception):pass


def run(*argv, timeout=150, cwd=None):
    env=os.environ.copy()
    env.update({'QT_QPA_PLATFORM':'offscreen','MU_QT_QPA_PLATFORM':'offscreen','HOME':'/tmp', 'XDG_RUNTIME_DIR':'/tmp/capellabridge-runtime'})
    Path('/tmp/capellabridge-runtime').mkdir(mode=0o700,exist_ok=True)
    try:
        cp=subprocess.run(list(map(str,argv)),env=env,cwd=cwd,capture_output=True,text=True,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailure(f'Timeout after {timeout} seconds') from exc
    if cp.returncode!=0:
        diagnostic=(cp.stderr or cp.stdout or 'Unknown error')[-1200:]
        raise ConversionFailure(f'MuseScore failed (exit {cp.returncode}): {diagnostic}')


def musescore_path():
    explicit=os.environ.get('MUSESCORE_BIN','')
    if explicit:return explicit
    for name in ('musescore3','mscore','musescore','MuseScore4'):
        found=shutil.which(name)
        if found:return found
    raise ConversionFailure('MuseScore CLI executable not installed. Build with supplied Dockerfile.')


def convert(source,target):
    run(musescore_path(),'-o',target,source)
    if not Path(target).is_file() or Path(target).stat().st_size<100:
        raise ConversionFailure('MuseScore did not create an output file')


def create_preview(pdf_path,png_path):
    if not shutil.which('pdftoppm'):
        raise ConversionFailure('pdftoppm not installed')
    stem=str(Path(png_path).with_suffix(''))
    run('pdftoppm','-f','1','-l','1','-singlefile','-png','-scale-to','1300',pdf_path,stem,timeout=60)
    if not Path(png_path).exists():
        raise ConversionFailure('PDF preview rendering failed')
