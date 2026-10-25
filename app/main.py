"""CapellaBridge 1.0: private self-hosted CAP/CAPX to MuseScore web service."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote
from typing import Annotated
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from .converter import CapellaError, serialize_musicxml
from .musescore import ConversionFailure, convert, create_preview, musescore_path
from .engraving import prepare_print_ready
from .score_validation import audit_mscz, ScoreIntegrityError
from .semantic_audit import audit_musicxml_against_mscz, SemanticIntegrityError
from .source_audit import audit_capx_against_musicxml

VERSION='1.7.7'
BASE=Path(os.environ.get('CAPELLABRIDGE_DATA','/data')).resolve()
JOBS=BASE/'jobs'
STATIC=Path(__file__).parent/'static'
TTL=int(os.environ.get('CAPELLABRIDGE_TTL_HOURS','24'))*3600
MAX_FILE=int(os.environ.get('CAPELLABRIDGE_MAX_UPLOAD_MB','15'))*1024*1024
MAX_BATCH=max(1,min(30,int(os.environ.get('CAPELLABRIDGE_MAX_BATCH','30'))))
async def expire_loop():
    while True:
        await asyncio.to_thread(cleanup)
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(expire_loop())
    try:
        yield
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass


app=FastAPI(title='CapellaBridge',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
semaphore=asyncio.Semaphore(1)
background_tasks=set()


def valid_job_id(job_id):
    return bool(re.fullmatch(r'[0-9a-f]{32}', job_id))


def write_job_status(folder, status):
    """Persist status so polling remains reliable across request boundaries."""
    target=folder/'status.json'
    temporary=folder/'status.json.tmp'
    temporary.write_text(json.dumps(status, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, target)


def read_job_status(job_id):
    if not valid_job_id(job_id):
        raise HTTPException(404, 'Job not found')
    status=JOBS/job_id/'status.json'
    if not status.is_file():
        raise HTTPException(404, 'Job not found or expired')
    try:
        return json.loads(status.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(503, 'Job status is temporarily unavailable') from exc

def cleanup():
    JOBS.mkdir(parents=True,exist_ok=True)
    cutoff=time.time()-TTL
    for folder in JOBS.iterdir():
        if folder.is_dir() and folder.stat().st_mtime<cutoff:
            shutil.rmtree(folder,ignore_errors=True)


@app.get('/health')
def health():
    return {'status':'ok','version':VERSION,'engine_available':bool(shutil.which(os.environ.get('MUSESCORE_BIN','musescore3')))}


@app.get('/')
def index():
    return FileResponse(STATIC/'index.html',media_type='text/html',
                        headers={'Cache-Control':'no-store'})


@app.get('/static/{filename}')
def static(filename:str):
    if filename not in ('app.css','app.js','mark.svg'):raise HTTPException(404)
    return FileResponse(STATIC/filename,
                        media_type='image/svg+xml' if filename.endswith('.svg') else None,
                        headers={'Cache-Control':'no-store'})


@app.get('/api/info')
def info():
    return {'version':VERSION,'max_upload_mb':MAX_FILE//1024//1024,'max_batch':MAX_BATCH,
            'supported':{'capx':'Native CapXML 2.0 engine','cap':'Legacy MuseScore importer (best effort)'},
            'output':['mscz','musicxml','pdf','preview.png'],'retention_hours':TTL//3600,'engine_available':bool(shutil.which(os.environ.get('MUSESCORE_BIN','musescore3')))}


def safe_filename(filename):
    stem=Path(filename.replace('\\','/')).name
    stem=re.sub(r'[^\w .()\-]+','_',stem,flags=re.UNICODE).strip('. ')
    return (stem[:95] or 'score')


async def receive_to_path(upload:UploadFile, destination: Path):
    name=safe_filename(upload.filename or 'score.capx')
    ext=Path(name).suffix.lower()
    if ext not in ('.cap','.capx'):
        raise HTTPException(415,f'Unsupported file type: {ext or "none"}; upload .cap or .capx')
    size=0
    with destination.open('wb') as output:
        while True:
            block=await upload.read(1024*1024)
            if not block: break
            size+=len(block)
            if size>MAX_FILE:
                raise HTTPException(413,f'{name} exceeds {MAX_FILE//(1024*1024)} MB upload limit')
            output.write(block)
    if not size:
        raise HTTPException(400,f'{name} is empty')
    if ext=='.capx' and not zipfile.is_zipfile(destination):
        raise HTTPException(422,f'{name} is not a valid ZIP-based CAPX document')
    return name,ext


def pdf_page_count(path: Path) -> int | None:
    """Read printed page count from Poppler without extra Python packages."""
    try:
        result = subprocess.run(['pdfinfo', str(path)], capture_output=True,
                                text=True, timeout=12, check=True)
        match = re.search(r'^Pages:\s*(\d+)$', result.stdout, re.M)
        return int(match.group(1)) if match else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def save_validated_mscz(source_xml: Path, final_mscz: Path,
                        report: dict | None, warnings: list[str]) -> str:
    """Create a native score, returning ``verified`` or ``manual-review``.

    Validation remains fail-closed for certification, but not for delivery.
    If MuseScore produced a genuine MSCZ, keep it and expose every diagnostic
    instead of deleting the user's only editable result.
    """
    if report and report.get('unresolved_hidden_sounding_chords'):
        warnings.append(
            'Manual-review export: invisible pitched events remain ambiguous; '
            'verify playback before relying on this MSCZ.'
        )

    def validate_round_trip(path: Path) -> dict:
        # Production conversion is fail-closed: a tiny gap is not silently
        # padded unless a future source-aware ledger explicitly permits it.
        errors=[]
        try:
            warnings.extend(audit_mscz(path, repair=True, allow_repairs=False))
        except ScoreIntegrityError as exc:
            errors.append('Structural audit: ' + str(exc))
        try:
            semantic = audit_musicxml_against_mscz(source_xml, path)
        except SemanticIntegrityError as exc:
            errors.append('Semantic audit: ' + str(exc))
            semantic = {'status':'unverified','error':str(exc)[:500]}
        if report is not None:
            report['semantic_audit'] = semantic
        if semantic.get('ignored_grace_rests', 0):
            warnings.append(
                f"{semantic.get('ignored_grace_rests', 0)} invisible grace rests "
                'were omitted by MuseScore; they have no playback duration.'
            )
        if semantic.get('source_tail_padding', '0') != '0':
            warnings.append(
                'MuseScore added a silent trailing rounding rest of '
                f"{semantic.get('source_tail_padding', '0')}; no playback time was added."
            )
        if errors:
            raise SemanticIntegrityError(' | '.join(errors))
        return semantic

    convert(source_xml, final_mscz)
    prepare_print_ready(final_mscz, report)
    validation_errors=[]
    try:
        validate_round_trip(final_mscz)
    except (ScoreIntegrityError, SemanticIntegrityError) as exc:
        validation_errors.append(str(exc))
    canonical = final_mscz.with_name(final_mscz.stem + '-canonical.mscz')
    if not validation_errors:
        try:
            convert(final_mscz, canonical)
            prepare_print_ready(canonical, report)
            validate_round_trip(canonical)
            os.replace(canonical, final_mscz)
            audit_mscz(final_mscz, repair=False)
        except (ConversionFailure, ScoreIntegrityError,
                SemanticIntegrityError, zipfile.BadZipFile) as exc:
            validation_errors.append(
                'Canonical MuseScore round trip was not certified: ' + str(exc))
        finally:
            canonical.unlink(missing_ok=True)
    source_unverified = bool(
        report and report.get('source_audit', {}).get('status') == 'unverified')
    verification = ('manual-review' if validation_errors or source_unverified or
                    (report and report.get('unresolved_hidden_sounding_chords'))
                    else 'verified')
    if validation_errors:
        warnings.extend(
            'Manual-review export: ' + error[:500]
            for error in validation_errors
        )
    if report is not None:
        report['verification'] = verification
        report['verification_diagnostics'] = validation_errors
    return verification


def process_score(name,ext,raw,work,layout):
    stem=Path(name).stem
    prefix=safe_filename(stem)
    inp=work/name
    inp.write_bytes(raw)
    warnings=[]
    if ext=='.capx':
        xml,report=serialize_musicxml(raw,layout,filename=name)
        xml_path=work/(prefix+'.musicxml')
        xml_path.write_bytes(xml)
        warnings.extend(report.get('warnings',[]))
        try:
            report['source_audit']=audit_capx_against_musicxml(raw, xml)
        except SemanticIntegrityError as exc:
            report['source_audit']={'status':'unverified','error':str(exc)[:500]}
            warnings.append(
                'Manual-review export: CAPX-to-MusicXML audit could not certify '
                f'this score: {str(exc)[:400]}'
            )
    else:
        # Legacy binary CAP handled by the installed MuseScore engine.
        xml_path=work/(prefix+'.musicxml')
        convert(inp,xml_path)
        report={'title':stem,'source_format':'cap','parts':[],'measures':None,'warnings':[],'layout':'musescore-import'}
        warnings.append('Legacy .cap uses MuseScore’s import engine, not the CAPX native parser. Review layout and voices.')
    mscz=work/(prefix+'.mscz')
    verification=save_validated_mscz(
        xml_path, mscz, report if ext=='.capx' else None,
        warnings)
    pdf=work/(prefix+'.pdf')
    try:
        convert(mscz,pdf)
    except ConversionFailure as exc:
        warnings.append(f'PDF export failed, but the MuseScore file was retained: {exc}')
    # Restore a readable layout if an exact Capella system arrangement
    # expands into far more pages than the source's system count warrants.
    if ext=='.capx' and layout=='original' and pdf.is_file():
        source_systems=report.get('source_systems',0)
        pages=pdf_page_count(pdf)
        reasonable_pages=max(4,(source_systems+1)//2+1)
        if pages is not None and pages>reasonable_pages:
            xml,balanced_report=serialize_musicxml(raw,'balanced',filename=name)
            xml_path.write_bytes(xml)
            try:
                balanced_report['source_audit']=audit_capx_against_musicxml(raw, xml)
            except SemanticIntegrityError as exc:
                balanced_report['source_audit']={'status':'unverified','error':str(exc)[:500]}
                warnings.append(
                    'Manual-review export: CAPX-to-MusicXML audit could not certify '
                    f'the balanced fallback: {str(exc)[:400]}'
                )
            verification=save_validated_mscz(
                xml_path, mscz, balanced_report, warnings)
            try:
                convert(mscz,pdf)
            except ConversionFailure as exc:
                pdf.unlink(missing_ok=True)
                warnings.append(
                    f'Balanced PDF export failed, but the MuseScore file was retained: {exc}')
            report['layout']='balanced-fallback'
            warnings.append(
                f'Original system breaks would produce {pages} pages; '
                f'used a balanced print layout instead.'
            )
    png=work/(prefix+'-preview.png')
    if pdf.is_file():
        try:
            create_preview(pdf,png)
        except ConversionFailure as exc:
            warnings.append(f'Preview creation failed: {exc}')
    # Verify the resulting MSCZ ZIP contains a genuine MuseScore score.
    with zipfile.ZipFile(mscz) as archive:
        if not any(x.endswith('.mscx') for x in archive.namelist()):
            raise ConversionFailure('MuseScore produced an invalid MSCZ file')
    files={'mscz':mscz.name,'musicxml':xml_path.name}
    if pdf.is_file():files['pdf']=pdf.name
    if png.is_file():files['preview']=png.name
    report['verification']=verification
    report.setdefault('warning_categories',{})['verification']=[
        warning for warning in sorted(set(warnings))
        if warning.startswith('Manual-review export:')
    ]
    info={**report,'source_filename':name,'warnings':sorted(set(warnings)),
          'files':files}
    (work/(prefix+'-report.json')).write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    info['files']['report']=prefix+'-report.json'
    # Never retain the original uploaded file once conversion is done.
    inp.unlink(missing_ok=True)
    return info


def retain_partial_mscz(work: Path, name: str, error: Exception) -> dict | None:
    """Return a downloadable manual-review result after a late-stage error."""
    mscz = next(work.glob('*.mscz'), None)
    if mscz is None:
        return None
    try:
        with zipfile.ZipFile(mscz) as archive:
            if archive.testzip() is not None or not any(
                    member.endswith('.mscx') for member in archive.namelist()):
                return None
    except zipfile.BadZipFile:
        return None
    prefix=safe_filename(Path(name).stem)
    files={'mscz':mscz.name}
    for key,suffix in (('musicxml','.musicxml'),('pdf','.pdf'),
                       ('preview','-preview.png')):
        candidate=work/(prefix+suffix)
        if candidate.is_file():files[key]=candidate.name
    diagnostic=(
        'Manual-review export: a later conversion stage failed after MuseScore '
        f'created the editable score: {str(error)[:500]}')
    report={
        'source_filename':name,'title':Path(name).stem,'parts':[],
        'measures':None,'layout':'unknown','verification':'manual-review',
        'verification_diagnostics':[str(error)[:500]],'warnings':[diagnostic],
        'files':files,
    }
    report_path=work/(prefix+'-report.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),
                           encoding='utf-8')
    report['files']['report']=report_path.name
    return report


async def run_batch(job_id, incoming, layout):
    """Convert a batch outside the browser's long-running upload request."""
    folder=JOBS/job_id
    total=len(incoming)
    results=[]
    write_job_status(folder, {
        'job_id':job_id, 'state':'running', 'total':total,
        'completed':0, 'results':[], 'batch_url':None,
    })
    try:
        async with semaphore:
            for index,(name,ext,input_path) in enumerate(incoming):
                out=folder/f'{index+1:02d}'
                out.mkdir()
                try:
                    result=await asyncio.to_thread(
                        process_score, name, ext, input_path.read_bytes(),
                        out, layout
                    )
                    result['downloads']={
                        k:f'/api/files/{job_id}/{index+1:02d}/{quote(v)}'
                        for k,v in result['files'].items()
                    }
                    results.append({'ok':True,**result})
                except (CapellaError,ConversionFailure,ScoreIntegrityError,
                        ValueError,zipfile.BadZipFile) as exc:
                    partial=retain_partial_mscz(out,name,exc)
                    if partial:
                        partial['downloads']={
                            k:f'/api/files/{job_id}/{index+1:02d}/{quote(v)}'
                            for k,v in partial['files'].items()
                        }
                        results.append({'ok':True,**partial})
                    else:
                        shutil.rmtree(out,ignore_errors=True)
                        results.append({'ok':False,'source_filename':name,
                                        'stage':'conversion',
                                        'error':str(exc)[:500]})
                except Exception as exc:
                    partial=retain_partial_mscz(out,name,exc)
                    if partial:
                        partial['downloads']={
                            k:f'/api/files/{job_id}/{index+1:02d}/{quote(v)}'
                            for k,v in partial['files'].items()
                        }
                        results.append({'ok':True,**partial})
                    else:
                        shutil.rmtree(out,ignore_errors=True)
                        results.append({'ok':False,'source_filename':name,
                                        'stage':'internal',
                                        'error':'Internal conversion error. Check server logs.',
                                        'detail':str(exc)[:200]})
                finally:
                    input_path.unlink(missing_ok=True)
                write_job_status(folder, {
                    'job_id':job_id, 'state':'running', 'total':total,
                    'completed':index+1, 'results':results, 'batch_url':None,
                })
            shutil.rmtree(folder/'input',ignore_errors=True)
            if any(result['ok'] for result in results):
                batch=folder/'CapellaBridge-batch.zip'
                with zipfile.ZipFile(batch,'w',compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=5) as archive:
                    for index,result in enumerate(results):
                        if not result['ok']:
                            continue
                        out=folder/f'{index+1:02d}'
                        for file in out.iterdir():
                            archive.write(
                                file,
                                arcname=(
                                    f'{index+1:02d}_'
                                    f'{safe_filename(Path(result["source_filename"]).stem)}/'
                                    f'{file.name}'
                                ),
                            )
                batch_url=f'/api/files/{job_id}/CapellaBridge-batch.zip'
            else:
                batch_url=None
            write_job_status(folder, {
                'job_id':job_id, 'state':'completed', 'total':total,
                'completed':total, 'results':results, 'batch_url':batch_url,
            })
    except Exception as exc:
        for _,_,input_path in incoming:
            input_path.unlink(missing_ok=True)
        shutil.rmtree(folder/'input',ignore_errors=True)
        write_job_status(folder, {
            'job_id':job_id, 'state':'failed', 'total':total,
            'completed':len(results), 'results':results, 'batch_url':None,
            'error':f'Batch conversion failed: {str(exc)[:400]}',
        })


def retain_background_task(task):
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@app.post('/api/convert')
async def upload(files: Annotated[list[UploadFile],File(...)],layout: str='balanced',
                 background: bool = False):
    if layout not in ('balanced','compact','original'):
        raise HTTPException(400,'Unknown layout setting')
    if not 1<=len(files)<=MAX_BATCH:
        raise HTTPException(400,f'Upload 1-{MAX_BATCH} files at once')
    cleanup()
    job_id=uuid.uuid4().hex
    folder=JOBS/job_id
    folder.mkdir(parents=True,exist_ok=False)
    input_entries=[]
    try:
        for index,f in enumerate(files):
            input_folder=folder/'input'/f'{index+1:02d}'
            input_folder.mkdir(parents=True)
            input_path=input_folder/'source'
            name,ext=await receive_to_path(f,input_path)
            input_entries.append((name,ext,input_path))
            await f.close()
    except Exception:
        for f in files:
            await f.close()
        shutil.rmtree(folder,ignore_errors=True)
        raise
    write_job_status(folder, {
        'job_id':job_id, 'state':'queued', 'total':len(input_entries),
        'completed':0, 'results':[], 'batch_url':None,
    })
    if background:
        task=asyncio.create_task(run_batch(job_id,input_entries,layout))
        retain_background_task(task)
        return JSONResponse(status_code=202,content={
            'job_id':job_id, 'state':'queued', 'total':len(input_entries),
            'completed':0, 'results':[], 'batch_url':None,
            'status_url':f'/api/jobs/{job_id}',
        })
    await run_batch(job_id,input_entries,layout)
    status=read_job_status(job_id)
    if not any(result['ok'] for result in status['results']):
        shutil.rmtree(folder,ignore_errors=True)
        return {'job_id':None,'results':status['results'],'batch_url':None}
    return {k:status[k] for k in ('job_id','results','batch_url')}


@app.get('/api/jobs/{job_id}')
def job_status(job_id: str):
    return read_job_status(job_id)


@app.get('/api/files/{job_id}/{filename}')
def batch_download(job_id:str,filename:str):
    if not re.fullmatch(r'[0-9a-f]{32}',job_id) or filename!='CapellaBridge-batch.zip':raise HTTPException(404)
    return serve(JOBS/job_id/filename)


@app.get('/api/files/{job_id}/{directory}/{filename}')
def download(job_id:str,directory:str,filename:str):
    if not re.fullmatch(r'[0-9a-f]{32}',job_id) or not re.fullmatch(r'[0-9]{2}',directory):raise HTTPException(404)
    if filename!=Path(filename).name or '..' in filename:raise HTTPException(404)
    return serve(JOBS/job_id/directory/filename)


def serve(path):
    if not path.is_file():raise HTTPException(404,'File expired or not found')
    media='application/octet-stream'
    if path.suffix=='.png':media='image/png'
    if path.suffix=='.pdf':media='application/pdf'
    inline=path.suffix in ('.pdf','.png')
    return FileResponse(path,media_type=media,filename=None if inline else path.name,
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
