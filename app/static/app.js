'use strict';
const picker=document.getElementById('picker');const drop=document.getElementById('dropzone');const selected=document.getElementById('selected');const button=document.getElementById('convert');const queue=document.getElementById('queue-label');const results=document.getElementById('results');const resultsList=document.getElementById('result-list');const batch=document.getElementById('batch');const startOver=document.getElementById('start-over');let files=[];let max=12;
const reviewToggle=document.getElementById('allow-review');
const human=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':Math.round(n/1024)+' KB';
function putFile(list){for(const f of list){if(!/\.(capx|cap)$/i.test(f.name))continue;if(files.length<max&&!files.some(x=>x.name===f.name&&x.size===f.size))files.push(f);}paint();}
function paint(){selected.replaceChildren();for(const [index,f] of files.entries()){const line=document.createElement('div');line.className='selected-file';const a=document.createElement('span');a.textContent='♫  '+f.name+'  ·  '+human(f.size);const b=document.createElement('button');b.type='button';b.textContent='×';b.setAttribute('aria-label','Remove '+f.name);b.onclick=()=>{files.splice(index,1);paint()};line.append(a,b);selected.append(line);}queue.textContent=files.length?files.length+' FILE'+(files.length>1?'S':''):'0 FILES';button.disabled=!files.length;}
picker.addEventListener('change',()=>{putFile(picker.files);picker.value='';});for(const e of ['dragenter','dragover'])drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('dragging')});for(const e of ['dragleave','drop'])drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('dragging')});drop.addEventListener('drop',ev=>putFile(ev.dataTransfer.files));drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();picker.click();}});
document.querySelectorAll('input[name=layout]').forEach(x=>x.addEventListener('change',()=>document.querySelectorAll('.option').forEach(y=>y.classList.toggle('selected',y.querySelector('input').checked))));
const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el};
function link(label,url,other=false){const a=node('a','↓ '+label,'download'+(other?' other':''));a.href=url;a.download='';return a;}
function display(r){
  const card=node('article',undefined,'result-item');
  const heading=node('div',undefined,'result-title');
  heading.append(node('strong',r.source_filename),node('span',r.ok?`${r.measures??'—'} measures · ${(r.parts||[]).length} parts`:'Needs attention'));
  card.append(heading);
  if(!r.ok){card.append(node('p',r.error,'error'));return card;}
  const body=node('div',undefined,'result-body');
  const img=node('img');img.className='thumbnail';img.loading='lazy';img.src=r.downloads.preview;img.alt='First page score preview';
  body.append(img);
  const side=node('div');
  const links=node('div',undefined,'links');
  links.append(link('MuseScore',r.downloads.mscz),link('MusicXML',r.downloads.musicxml,true),link('PDF',r.downloads.pdf,true),link('Report',r.downloads.report,true));
  side.append(links);
  if(r.warnings?.length){
    const details=node('details',undefined,'review-details');
    const summary=node('summary',`${r.warnings.length} review note${r.warnings.length===1?'':'s'}`);
    const ul=node('ul',undefined,'warnings');
    for(const message of r.warnings)ul.append(node('li',message));
    details.append(summary,ul);side.append(details);
  }
  body.append(side);card.append(body);return card;
}
const progressPanel=document.getElementById('conversion-progress');
const progressLabel=document.getElementById('progress-label');
const progressFill=document.getElementById('progress-fill');
const progressValue=document.getElementById('progress-value');
const progressDetail=document.getElementById('progress-detail');
function progress(stage,value,detail){
  progressPanel.hidden=false;
  progressPanel.classList.toggle('working',stage==='converting');
  progressPanel.classList.toggle('failed',stage==='error');
  progressLabel.textContent=stage==='upload'?'Uploading scores…':stage==='converting'?'Converting music & preparing previews…':stage==='done'?'Conversion complete': 'Conversion failed';
  progressValue.textContent=stage==='upload'&&value!==null?value+'%':stage==='done'?'100%':'';
  progressFill.style.width=stage==='upload'&&value!==null?value+'%':stage==='done'?'100%':'42%';
  progressDetail.textContent=detail;
  const track=progressPanel.querySelector('[role=progressbar]');
  if(stage==='upload'&&value!==null){track.setAttribute('aria-valuenow',String(value));track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');}
  else{track.removeAttribute('aria-valuenow');track.removeAttribute('aria-valuemin');track.removeAttribute('aria-valuemax');}
}
function resetWorkspace(){
  files=[];picker.value='';paint();resultsList.replaceChildren();results.hidden=true;
  batch.hidden=true;batch.removeAttribute('href');startOver.hidden=true;
  progressPanel.hidden=true;progressPanel.classList.remove('working','failed');
  progressDetail.textContent='Processing your files locally.';progressFill.style.width='0';
  button.querySelector('span').textContent='Convert to MuseScore';
}
startOver.addEventListener('click',resetWorkspace);
document.getElementById('form').addEventListener('submit',async ev=>{
  ev.preventDefault();if(!files.length)return;
  button.disabled=true;button.querySelector('span').textContent='Converting…';
  results.hidden=false;resultsList.replaceChildren(node('p','Uploading your scores…'));
  batch.hidden=true;
  startOver.hidden=true;
  const data=new FormData();for(const f of files)data.append('files',f);
  const layout=document.querySelector('input[name=layout]:checked').value;
  const review=reviewToggle.checked?'&review=1':'';
  progress('upload',0,'Sending '+files.length+' score'+(files.length===1?'':'s')+' to your NAS.');
  try{
    const response=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();
      xhr.open('POST','/api/convert?layout='+encodeURIComponent(layout)+review);
      xhr.timeout=600000;
      xhr.upload.onprogress=event=>{
        if(event.lengthComputable)progress('upload',Math.min(100,Math.round(event.loaded/event.total*100)),'Uploading '+files.length+' score'+(files.length===1?'':'s')+'…');
      };
      xhr.upload.onload=()=>{
        progress('converting',null,'Upload complete. MuseScore is engraving and exporting the score. This stage can take longer for large files.');
        resultsList.replaceChildren(node('p','Creating MuseScore files and PDF previews…'));
      };
      xhr.onload=()=>resolve(xhr);
      xhr.onerror=()=>reject(new Error('Network connection lost during conversion.'));
      xhr.ontimeout=()=>reject(new Error('Conversion timed out. Try a smaller batch.'));
      xhr.send(data);
    });
    let json;
    try{json=JSON.parse(response.responseText)}catch{throw Error('Server returned an unexpected response');}
    if(response.status<200||response.status>=300)throw Error(typeof json.detail==='string'?json.detail:'Conversion request failed');
    resultsList.replaceChildren();
    for(const r of json.results)resultsList.append(display(r));
    if(json.batch_url){batch.href=json.batch_url;batch.hidden=false;batch.download='CapellaBridge-batch.zip';}
    startOver.hidden=false;
    progress('done',100,'Your converted files are ready below.');
    results.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(error){
    progress('error',null,String(error.message));
    resultsList.replaceChildren(node('p',String(error.message),'error'));
    startOver.hidden=false;
  }finally{
    button.querySelector('span').textContent='Convert to MuseScore';
    button.disabled=!files.length;
  }
});
fetch('/api/info').then(r=>r.json()).then(j=>{max=j.max_batch;document.getElementById('size-limit').textContent=j.max_upload_mb;document.getElementById('engine').textContent=j.engine_available?'MuseScore CLI ready':'Renderer unavailable';}).catch(()=>{document.getElementById('engine').textContent='Unavailable';});
