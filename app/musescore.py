"""Isolated MuseScore command-line conversion and PDF rendering.

The legacy Capella importer is used as a native-score importer.  CAP files
must therefore go directly to MSCZ; MusicXML and PDF are exports from that
same native score, never an intermediate conversion step.
"""
from __future__ import annotations
import os
import subprocess
import shutil
import tempfile
from pathlib import Path

class ConversionFailure(Exception):
    """A converter process failed without producing a trustworthy result."""

    def __init__(self, message, *, returncode=None, diagnostic=""):
        super().__init__(message)
        self.returncode = returncode
        self.diagnostic = diagnostic


def run(*argv, timeout=150, cwd=None):
    try:
        with tempfile.TemporaryDirectory(prefix='capellabridge-ms-', dir='/tmp') as root:
            runtime=Path(root)/'runtime'
            home=Path(root)/'home'
            runtime.mkdir(mode=0o700)
            home.mkdir(mode=0o700)
            env=os.environ.copy()
            env.update({
                'QT_QPA_PLATFORM':'offscreen',
                'MU_QT_QPA_PLATFORM':'offscreen',
                'HOME':str(home),
                'XDG_RUNTIME_DIR':str(runtime),
            })
            cp=subprocess.run(list(map(str,argv)),env=env,cwd=cwd,
                              capture_output=True,text=True,timeout=timeout,check=False)
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailure(f'Timeout after {timeout} seconds') from exc
    if cp.returncode!=0:
        diagnostic=(cp.stderr or cp.stdout or 'Unknown error')[-1200:]
        raise ConversionFailure(
            f'MuseScore failed (exit {cp.returncode}): {diagnostic}',
            returncode=cp.returncode,
            diagnostic=diagnostic,
        )


def musescore_path():
    explicit=os.environ.get('MUSESCORE_BIN','')
    if explicit:return explicit
    for name in ('musescore3','mscore','musescore','MuseScore4'):
        found=shutil.which(name)
        if found:return found
    raise ConversionFailure('MuseScore CLI executable not installed. Build with supplied Dockerfile.')


def legacy_cap_musescore_path():
    """Return the optional pinned importer used only for binary CAP files.

    MuseScore 3 has a known regression where CAP chord articulation values are
    parsed but their conversion cases are compiled out. MuseScore 2.3.2 keeps
    those objects. CAPX never calls this function and remains on the native
    CapellaBridge conversion path.
    """
    explicit=os.environ.get('MUSESCORE_LEGACY_CAP_BIN','')
    if not explicit:
        return None
    if not Path(explicit).is_file():
        raise ConversionFailure(
            f'Configured legacy CAP importer does not exist: {explicit}'
        )
    return explicit


def convert(source,target,timeout=150):
    """Convert one file with an isolated MuseScore runtime."""
    target_path=Path(target)
    run(musescore_path(),'-o',target_path,source,
        timeout=timeout,cwd=target_path.parent)
    if not target_path.is_file() or target_path.stat().st_size<100:
        raise ConversionFailure('MuseScore did not create an output file')


def import_cap(source, target):
    """Import binary CAP without exposing CAPX to the legacy engine.

    When the pinned MuseScore 2.3.2 importer is installed, it first decodes
    the CAP-specific articulations and draw objects into a native score. The
    regular MuseScore 3 engine then upgrades that native score to the final
    MSCZ consumed by all existing validation, engraving, and export steps.
    """
    if Path(target).suffix.lower() != '.mscz':
        raise ConversionFailure('Legacy CAP imports must target an MSCZ file')
    legacy=legacy_cap_musescore_path()
    if legacy is None:
        convert(source, target, timeout=300)
        return {'strategy':'native-cap-to-mscz','fallback':False}
    target_path=Path(target)
    with tempfile.TemporaryDirectory(
        prefix='.legacy-cap-import-', dir=target_path.parent
    ) as root:
        intermediate=Path(root)/(target_path.stem+'-ms2.mscz')
        def fallback(exc):
            target_path.unlink(missing_ok=True)
            convert(source, target_path, timeout=300)
            return {
                'strategy':'musescore3-cap-fallback',
                'fallback':True,
                'legacy_error':str(exc),
                'legacy_returncode':getattr(exc, 'returncode', None),
            }

        try:
            run(legacy,'-o',intermediate,source,
                timeout=300,cwd=intermediate.parent)
        except ConversionFailure as exc:
            # The pinned MuseScore 2 CAP reader is valuable because it keeps
            # legacy articulation objects that MuseScore 3 can omit. It is
            # nevertheless old native code and malformed drawing anchors can
            # terminate it with SIGSEGV (exit 139). A single importer crash
            # must not withhold the user's editable score or abort a batch.
            # MuseScore 3 is a deliberately degraded but isolated fallback;
            # callers surface the diagnostic and retain manual-review status.
            return fallback(exc)
        if not intermediate.is_file() or intermediate.stat().st_size < 100:
            return fallback(ConversionFailure(
                'Pinned legacy CAP importer did not create a usable MSCZ file'
            ))
        try:
            convert(intermediate,target_path,timeout=300)
        except ConversionFailure as exc:
            # A damaged MuseScore 2 intermediate should not prevent a direct
            # MuseScore 3 import from producing a separately reviewable score.
            return fallback(ConversionFailure(
                'Pinned legacy CAP intermediate could not be upgraded: '
                f'{exc}', returncode=exc.returncode,
                diagnostic=exc.diagnostic,
            ))
    return {
        'strategy':'musescore2-cap-to-musescore3-mscz',
        'fallback':False,
    }


def export_musicxml(source_mscz, target):
    """Export MusicXML from an already-created native MuseScore score."""
    convert(source_mscz, target, timeout=180)


def export_pdf(source_mscz, target):
    """Export PDF from an already-created native MuseScore score."""
    convert(source_mscz, target, timeout=180)


def create_preview(pdf_path,png_path):
    if not shutil.which('pdftoppm'):
        raise ConversionFailure('pdftoppm not installed')
    stem=str(Path(png_path).with_suffix(''))
    run('pdftoppm','-f','1','-l','1','-singlefile','-png','-scale-to','1300',pdf_path,stem,timeout=60)
    if not Path(png_path).exists():
        raise ConversionFailure('PDF preview rendering failed')
