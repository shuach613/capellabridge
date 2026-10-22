"""CapellaBridge 1.0: private self-hosted CAP/CAPX to MuseScore web service."""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
import threading
from contextlib import asynccontextmanager
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Annotated
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .converter import CapellaError, serialize_musicxml
from .musescore import ConversionFailure, convert, create_preview, musescore_path

VERSION='1.0.1'
BASE=Path(os.environ.get('CAPELLABRIDGE_DATA','/data')).resolve()
JOBS=BASE/'jobs'
STATIC=Path(__file__).parent/'static'
PASSWORD=os.environ.get('CAPELLABRIDGE_PASSWORD','') # Optional CI / backwards-compat override
USERNAME=os.environ.get('CAPELLABRIDGE_USER','admin')
IDENTITY=BASE/'admin.json'
LOG=logging.getLogger('uvicorn.error')
_identity_lock=threading.Lock()

def _derive(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,310_000).hex()

def _save_identity(username: str, password: str):
    BASE.mkdir(parents=True,exist_ok=True)
    salt=secrets.token_bytes(32)
    state={'username':username,'salt':salt.hex(),'password_hash':_derive(password,salt)}
    temp=BASE/('admin-'+secrets.token_hex(8)+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as fp:
            json.dump(state,fp)
        os.chmod(temp,0o600)
        os.replace(temp,IDENTITY)
    finally:
        temp.unlink(missing_ok=True)

def initialize_identity():
    if PASSWORD or IDENTITY.is_file():return
    with _identity_lock:
        if IDENTITY.is_file():return
        initial=secrets.token_urlsafe(20)
        _save_identity('admin',initial)
        LOG.warning('=====================================================')
        LOG.warning('CAPELLABRIDGE FIRST START: admin login generated')
        LOG.warning('Username: admin')
        LOG.warning('Password: %s',initial)
        LOG.warning('Save this password or change it in the web UI.')
        LOG.warning('Find it using: docker logs capellabridge')
        LOG.warning('=====================================================')

def _valid_credentials(user: str, password: str) -> bool:
    if PASSWORD:
        return secrets.compare_digest(user,USERNAME) and secrets.compare_digest(password,PASSWORD)
    try:
        state=json.loads(IDENTITY.read_text(encoding='utf-8'))
        derived=_derive(password,bytes.fromhex(state['salt']))
        return hmac.compare_digest(user,state['username']) and hmac.compare_digest(derived,state['password_hash'])
    except (OSError,KeyError,ValueError,TypeError,json.JSONDecodeError):
        return False

TTL=int(os.environ.get('CAPELLABRIDGE_TTL_HOURS','24'))*3600
MAX_FILE=int(os.environ.get('CAPELLABRIDGE_MAX_UPLOAD_MB','15'))*1024*1024
MAX_BATCH=max(1,min(30,int(os.environ.get('CAPELLABRIDGE_MAX_BATCH','12'))))
async def expire_loop():
    while True:
        await asyncio.to_thread(cleanup)
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app):
    initialize_identity()
    task=asyncio.create_task(expire_loop())
    try:
        yield
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass


app=FastAPI(title='CapellaBridge',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
security=HTTPBasic(auto_error=False)
semaphore=asyncio.Semaphore(1)


def auth(cred: Annotated[HTTPBasicCredentials|None,Depends(security)]):
    if not cred or not _valid_credentials(cred.username,cred.password):
        raise HTTPException(status_code=401,detail='Authentication required',headers={'WWW-Authenticate':'Basic realm="CapellaBridge"'})
    return cred.username

class PasswordChange(BaseModel):
    current_password: str
    new_password: str

@app.post('/api/account/password',dependencies=[Depends(auth)])
def change_password(body:PasswordChange, username:Annotated[str,Depends(auth)]):
    if PASSWORD:
        raise HTTPException(409,'Credentials are controlled by container environment')
    if not _valid_credentials(username,body.current_password):
        raise HTTPException(403,'Current password is incorrect')
    if len(body.new_password)<12 or len(body.new_password)>256:
        raise HTTPException(422,'Password must be between 12 and 256 characters')
    with _identity_lock:
        _save_identity(username,body.new_password)
    return {'ok':True}


def cleanup():
    JOBS.mkdir(parents=True,exist_ok=True)
    cutoff=time.time()-TTL
    for folder in JOBS.iterdir():
        if folder.is_dir() and folder.stat().st_mtime<cutoff:
            shutil.rmtree(folder,ignore_errors=True)


@app.get('/health')
def health():
    return {'status':'ok','version':VERSION,'engine_available':bool(shutil.which(os.environ.get('MUSESCORE_BIN','musescore3'))),'authentication_configured':bool(PASSWORD or IDENTITY.exists())}


@app.get('/',dependencies=[Depends(auth)])
def index():return FileResponse(STATIC/'index.html',media_type='text/html')


@app.get('/static/{filename}',dependencies=[Depends(auth)])
def static(filename:str):
    if filename not in ('app.css','app.js','mark.svg'):raise HTTPException(404)
    return FileResponse(STATIC/filename,media_type='image/svg+xml' if filename.endswith('.svg') else None)


@app.get('/api/info',dependencies=[Depends(auth)])
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


def process_score(name,ext,raw,work,layout):
    stem=Path(name).stem
    prefix=safe_filename(stem)
    inp=work/name
    inp.write_bytes(raw)
    warnings=[]
    if ext=='.capx':
        xml,report=serialize_musicxml(raw,layout)
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
    convert(xml_path,mscz)
    pdf=work/(prefix+'.pdf')
    convert(mscz,pdf)
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


@app.post('/api/convert',dependencies=[Depends(auth)])
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
                result['downloads']={k:f'/api/files/{job_id}/{index+1:02d}/{v}' for k,v in result['files'].items()}
                results.append({'ok':True,**result})
            except (CapellaError,ConversionFailure,ValueError,zipfile.BadZipFile) as exc:
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


@app.get('/api/files/{job_id}/{filename}',dependencies=[Depends(auth)])
def batch_download(job_id:str,filename:str):
    if not re.fullmatch(r'[0-9a-f]{32}',job_id) or filename!='CapellaBridge-batch.zip':raise HTTPException(404)
    return serve(JOBS/job_id/filename)


@app.get('/api/files/{job_id}/{directory}/{filename}',dependencies=[Depends(auth)])
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
