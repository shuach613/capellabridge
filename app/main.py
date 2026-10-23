"""CapellaBridge 1.0: private self-hosted CAP/CAPX to MuseScore web service."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import io
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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .converter import CapellaError, serialize_musicxml
from .musescore import ConversionFailure, convert, create_preview, musescore_path
from .engraving import prepare_print_ready
from .score_validation import audit_mscz, ScoreIntegrityError

VERSION='1.7.4'
BASE=Path(os.environ.get('CAPELLABRIDGE_DATA','/data')).resolve()
JOBS=BASE/'jobs'
STATIC=Path(__file__).parent/'static'
TTL=int(os.environ.get('CAPELLABRIDGE_TTL_HOURS','24'))*3600
MAX_FILE=int(os.environ.get('CAPELLABRIDGE_MAX_UPLOAD_MB','15'))*1024*1024
MAX_BATCH=max(1,min(30,int(os.environ.get('CAPELLABRIDGE_MAX_BATCH','12'))))
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
def index():return FileResponse(STATIC/'index.html',media_type='text/html')


@app.get('/static/{filename}')
def static(filename:str):
    if filename not in ('app.css','app.js','mark.svg'):raise HTTPException(404)
    return FileResponse(STATIC/filename,media_type='image/svg+xml' if filename.endswith('.svg') else None)


@app.get('/api/info')
def info():
    return {'version':VERSION,'max_upload_mb':MAX_FILE//1024//1024,'max_batch':MAX_BATCH,
            'supported':{'capx':'Native CapXML 2.0 engine','cap':'Legacy MuseScore importer (best effort)'},
            'output':['mscz','musicxml','pdf','preview.png'],'retention_hours':TTL//3600,'engine_available':bool(shutil.which(os.environ.get('MUSESCORE_BIN','musescore3')))}


def safe_filename(filename):
    stem=Path(filename.replace('\\','/')).name
    stem=re.sub(r'[^\w .()\-]+','_',stem,flags=re.UNICODE).strip('. ')
    return (stem[:95] or 'score')


async def receive(upload:UploadFile):
    name=safe_filename(upload.filename or 'score.capx')
    ext=Path(name).suffix.lower()
    if ext not in ('.cap','.capx'):
        raise HTTPException(415,f'Unsupported file type: {ext or "none"}; upload .cap or .capx')
    buffer=io.BytesIO()
    while True:
        block=await upload.read(1024*1024)
        if not block: break
        if buffer.tell()+len(block)>MAX_FILE:
            raise HTTPException(413,f'{name} exceeds {MAX_FILE//(1024*1024)} MB upload limit')
        buffer.write(block)
    raw=buffer.getvalue()
    if not raw:raise HTTPException(400,f'{name} is empty')
    if ext=='.capx' and not zipfile.is_zipfile(io.BytesIO(raw)):
        raise HTTPException(422,f'{name} is not a valid ZIP-based CAPX document')
    return name,ext,raw


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
                        report: dict | None, warnings: list[str]) -> None:
    """Round-trip native score via MuseScore and verify all timed measures.

    MuseScore 3 can emit a readable MSCZ ZIP containing slightly incomplete
    measures; MuseScore Studio 4 then flags those scores as corrupted. A
    successful PDF export alone cannot verify score integrity.
    """
    convert(source_xml, final_mscz)
    prepare_print_ready(final_mscz, report)
    warnings.extend(audit_mscz(final_mscz, repair=True))
    canonical = final_mscz.with_name(final_mscz.stem + '-canonical.mscz')
    try:
        convert(final_mscz, canonical)
        prepare_print_ready(canonical, report)
        warnings.extend(audit_mscz(canonical, repair=True))
        os.replace(canonical, final_mscz)
    finally:
        canonical.unlink(missing_ok=True)
    audit_mscz(final_mscz, repair=False)


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
    else:
        # Legacy binary CAP handled by the installed MuseScore engine.
        xml_path=work/(prefix+'.musicxml')
        convert(inp,xml_path)
        report={'title':stem,'source_format':'cap','parts':[],'measures':None,'warnings':[],'layout':'musescore-import'}
        warnings.append('Legacy .cap uses MuseScore’s import engine, not the CAPX native parser. Review layout and voices.')
    mscz=work/(prefix+'.mscz')
    save_validated_mscz(xml_path, mscz, report if ext=='.capx' else None, warnings)
    pdf=work/(prefix+'.pdf')
    convert(mscz,pdf)
    # Restore a readable layout if an exact Capella system arrangement
    # expands into far more pages than the source's system count warrants.
    if ext=='.capx' and layout=='original':
        source_systems=report.get('source_systems',0)
        pages=pdf_page_count(pdf)
        reasonable_pages=max(4,(source_systems+1)//2+1)
        if pages is not None and pages>reasonable_pages:
            xml,balanced_report=serialize_musicxml(raw,'balanced',filename=name)
            xml_path.write_bytes(xml)
            save_validated_mscz(xml_path, mscz, balanced_report, warnings)
            convert(mscz,pdf)
            report['layout']='balanced-fallback'
            warnings.append(
                f'Original system breaks would produce {pages} pages; '
                f'used a balanced print layout instead.'
            )
    png=work/(prefix+'-preview.png')
    create_preview(pdf,png)
    # Verify the resulting MSCZ ZIP contains a genuine MuseScore score.
    with zipfile.ZipFile(mscz) as archive:
        if not any(x.endswith('.mscx') for x in archive.namelist()):
            raise ConversionFailure('MuseScore produced an invalid MSCZ file')
    info={**report,'source_filename':name,'warnings':sorted(set(warnings)),'files':{
        'mscz':mscz.name,'musicxml':xml_path.name,'pdf':pdf.name,'preview':png.name}}
    (work/(prefix+'-report.json')).write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    info['files']['report']=prefix+'-report.json'
    # Never retain the original uploaded file once conversion is done.
    inp.unlink(missing_ok=True)
    return info


@app.post('/api/convert')
async def upload(files: Annotated[list[UploadFile],File(...)],layout: str='balanced'):
    if layout not in ('balanced','compact','original'):
        raise HTTPException(400,'Unknown layout setting')
    if not 1<=len(files)<=MAX_BATCH:
        raise HTTPException(400,f'Upload 1-{MAX_BATCH} files at once')
    incoming=[]
    for f in files:
        incoming.append(await receive(f))
        await f.close()
    async with semaphore:
        cleanup()
        job_id=uuid.uuid4().hex
        folder=JOBS/job_id
        folder.mkdir(parents=True,exist_ok=False)
        results=[]
        for index,(name,ext,raw) in enumerate(incoming):
            out=folder/f'{index+1:02d}'
            out.mkdir()
            try:
                result=await asyncio.to_thread(process_score,name,ext,raw,out,layout)
                result['downloads']={k:f'/api/files/{job_id}/{index+1:02d}/{quote(v)}' for k,v in result['files'].items()}
                results.append({'ok':True,**result})
            except (CapellaError,ConversionFailure,ScoreIntegrityError,ValueError,zipfile.BadZipFile) as exc:
                shutil.rmtree(out,ignore_errors=True)
                results.append({'ok':False,'source_filename':name,'error':str(exc)[:500]})
            except Exception:
                shutil.rmtree(out,ignore_errors=True)
                results.append({'ok':False,'source_filename':name,'error':'Internal conversion error. Check server logs.'})
        if not any(r['ok'] for r in results):
            shutil.rmtree(folder,ignore_errors=True)
            return {'job_id':None,'results':results,'batch_url':None}
        batch=folder/'CapellaBridge-batch.zip'
        with zipfile.ZipFile(batch,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as z:
            for index,rs in enumerate(results):
                if rs['ok']:
                    out=folder/f'{index+1:02d}'
                    for file in out.iterdir():
                        z.write(file,arcname=f'{index+1:02d}_{safe_filename(Path(rs["source_filename"]).stem)}/{file.name}')
        return {'job_id':job_id,'results':results,'batch_url':f'/api/files/{job_id}/CapellaBridge-batch.zip'}


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
