'use strict';
const $ = id => document.getElementById(id);
let csrf = '', courses = [], batch = null, configured = false, connected = false, working = false, polling = false;
function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; $('message').hidden = !text; }
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败，请重试。');
  return data;
}
function textCell(row, text, className='') { const cell = row.insertCell(); cell.textContent = text; cell.className = className; return cell; }
function invalidate() { batch = null; $('event-preview').hidden = true; updateImport(); }
function updateImport() {
  $('authorize').disabled = !configured || working;
  $('import').disabled = !batch || !connected || !$('calendar').value || working;
  $('import').textContent = batch ? `确认导入 ${batch.count} 条日程` : '确认导入';
  $('import-note').textContent = batch ? `${batch.count} 条日程，时区为北京时间。已导入的相同日程会自动跳过。` : '先上传课表并生成预览，然后连接飞书。';
}
function renderDraft(draft) {
  courses = draft.courses; $('course-panel').hidden = !courses.length;
  $('courses').replaceChildren(); $('warnings').replaceChildren();
  for (const warning of draft.warnings) { const p=document.createElement('p');p.textContent=warning;$('warnings').append(p); }
  $('warnings').hidden = !draft.warnings.length;
  $('ack-wrap').hidden = !draft.warnings.length; $('ack').checked = false;
  $('course-count').textContent = `${courses.length} 条课程安排`;
  $('select-all').checked = !courses.some(c=>c.conflict); $('select-all').indeterminate = courses.some(c=>c.conflict);
  for (const c of courses) {
    const row = $('courses').insertRow(); row.className = c.conflict ? 'conflict' : '';
    const check = document.createElement('input'); check.type='checkbox';check.value=c.id;check.checked=!c.conflict;check.setAttribute('aria-label',`选择 ${c.title} ${c.location}`);
    check.addEventListener('change',()=>{invalidate();const boxes=[...$('courses').querySelectorAll('input')];$('select-all').checked=boxes.every(x=>x.checked);$('select-all').indeterminate=boxes.some(x=>x.checked)&&!boxes.every(x=>x.checked);});
    row.insertCell().append(check);const title=textCell(row,c.title);const teacher=document.createElement('small');teacher.textContent=c.teacher;title.append(teacher);
    textCell(row,`周${'一二三四五六日'[c.weekday-1]} / ${c.sections} 节`);textCell(row,`${c.start}–${c.end}`,'time');textCell(row,c.week_text);textCell(row,c.location||'未提供');
  }
  invalidate();
}
async function loadFile(file) {
  if (!file) return;
  if (working) return message('正在导入，请等待完成。',true);
  if (file.size > 7.5*1024*1024) return message('文件过大，请选择小于 7.5 MB 的 HTML 文件。',true);
  invalidate();
  try {
    const buffer=await file.arrayBuffer();let html=new TextDecoder('utf-8',{fatal:false}).decode(buffer);
    if (/charset\s*=\s*["']?\s*(gbk|gb2312|gb18030)/i.test(html)) html=new TextDecoder('gb18030').decode(buffer);
    const draft=await api('/api/parse',{html});renderDraft(draft);$('filename').textContent=file.name;message(`已读取 ${draft.courses.length} 条课程安排，请核对周次和开学日期。`);
  } catch(e) { message(e.message,true); }
}
async function loadCalendars() {
  const data=await api('/api/calendars');$('calendar').replaceChildren(new Option('请选择目标日历',''));
  for(const c of data.calendars) $('calendar').add(new Option(`${c.summary || '未命名日历'}${c.type==='primary'?'（主日历）':''}`,c.calendar_id));
  if(!data.calendars.length) message('当前账号没有可写入的日历，请检查应用权限或日历共享权限。',true);
  $('calendar-wrap').hidden=false;updateImport();
}
function setWorking(value) {
  working=value;
  for(const id of ['file','config-file','semester','select-all','ack','preview','authorize','logout','save-config','calendar']) $(id).disabled=value;
  $('courses').querySelectorAll('input').forEach(x=>x.disabled=value); updateImport();
}
async function pollJob() {
  if(polling) return; polling=true;
  try {
    let job;
    do {
      const status=await api('/api/status');job=status.job;
      if(!job) break;
      $('progress-wrap').hidden=false;$('progress').max=job.total;$('progress').value=job.processed;
      $('progress-text').textContent=`${job.phase} · 已创建 ${job.created}，跳过 ${job.skipped}，待处理 ${job.total-job.processed}`;
      $('import-errors').hidden=!job.errors.length;$('import-errors').textContent=job.errors.join('\n');
      setWorking(job.running);
      if(job.running) await new Promise(resolve=>setTimeout(resolve,800));
    } while(job.running);
    if(job&&!job.errors.length) message(`已完成：新增 ${job.created} 条，跳过 ${job.skipped} 条。`);
  } catch(e) { message(`无法获取导入进度：${e.message} 请保持程序运行，刷新页面后查看。`,true); }
  finally {polling=false;}
}
async function start() {
  const status=await api('/api/status');csrf=status.csrf;connected=status.connected;configured=status.configured;
  $('app-id').value=status.app_id;$('callback').textContent=status.callback;$('config-panel').open=!status.configured;
  $('config-status').textContent=configured?`已保存企业配置（${status.app_id}）`:'尚未导入企业配置';
  $('connection').textContent=connected?'已连接飞书':'尚未连接';$('connection').className=connected?'status connected':'status';
  $('logout').hidden=!connected;$('authorize').textContent=connected?'重新授权 / 切换账号':'授权飞书账号';
  renderDraft(await api('/api/draft'));
  if(connected) await loadCalendars();
  if(status.job) {setWorking(status.job.running);pollJob();}
  if(new URLSearchParams(location.search).has('authorized')) {history.replaceState(null,'','/');message('飞书授权成功，请选择目标日历。');}
}
function handle(id, action) {$(id).addEventListener('click',async()=>{const button=$(id);button.disabled=true;try{await action();}catch(e){message(e.message,true);}finally{button.disabled=working;updateImport();}});}
$('file').addEventListener('change',event=>loadFile(event.target.files[0]));
for(const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('drag');});
for(const name of ['dragleave','drop']) $('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('drag');if(name==='drop') loadFile(event.dataTransfer.files[0]);});
$('semester').addEventListener('change',invalidate);$('ack').addEventListener('change',invalidate);$('calendar').addEventListener('change',updateImport);
$('select-all').addEventListener('change',()=>{$('courses').querySelectorAll('input').forEach(x=>x.checked=$('select-all').checked);invalidate();});
async function saveConfig(config) {
  await api('/api/config',config);
  const status=await api('/api/status');configured=status.configured;
  $('app-id').value=status.app_id;$('app-secret').value='';connected=false;
  $('config-status').textContent=`已保存企业配置（${status.app_id}）`;
  $('calendar-wrap').hidden=true;$('calendar').value='';$('logout').hidden=true;
  $('connection').textContent='尚未连接';$('connection').className='status';
  $('authorize').textContent='授权飞书账号';$('progress-wrap').hidden=true;
  invalidate();message('企业配置已保存，请点击“授权飞书账号”，用自己的账号登录。');
}
$('config-file').addEventListener('change',async event=>{
  const input=event.target,file=input.files[0];if(!file) return;
  input.disabled=true;
  try {
    if(working) throw new Error('正在导入课程，请等待完成后再更换企业配置。');
    if(file.size>64*1024) throw new Error('企业配置文件不能超过 64 KB，请向管理员确认文件。');
    let config;
    try{config=JSON.parse((await file.text()).replace(/^\uFEFF/,''));}
    catch{throw new Error('配置文件不是有效的 JSON，请选择管理员提供的 feishu-config.json。');}
    if(!config||typeof config!=='object'||Array.isArray(config)) throw new Error('配置文件应包含 app_id 和 app_secret，请向管理员领取正确文件。');
    await saveConfig({app_id:config.app_id,app_secret:config.app_secret});
  }catch(e){message(e.message,true);}
  finally{input.value='';input.disabled=working;updateImport();}
});
handle('save-config',()=>saveConfig({app_id:$('app-id').value,app_secret:$('app-secret').value}));
handle('authorize',async()=>{const data=await api('/api/auth',{});location.assign(data.url);});
handle('logout',async()=>{await api('/api/logout',{});connected=false;$('calendar-wrap').hidden=true;$('logout').hidden=true;$('connection').textContent='尚未连接';$('connection').className='status';message('已清除本机用户令牌。若需撤回应用权限，请在飞书内管理已授权应用。');});
handle('preview',async()=>{
  const selected=[...$('courses').querySelectorAll('input:checked')].map(x=>x.value);
  batch=await api('/api/preview',{selected,semester_start:$('semester').value,acknowledged:$('ack').checked});
  $('preview-title').textContent=`将生成 ${batch.count} 条日程`;$('date-range').textContent=`${batch.events[0].date} 至 ${batch.events.at(-1).date}，北京时间`;
  $('events').replaceChildren();for(const e of batch.events){const row=$('events').insertRow();textCell(row,e.date);textCell(row,e.title);textCell(row,`${e.start_iso.slice(11,16)}–${e.end_iso.slice(11,16)}`);textCell(row,e.location||'未提供');}
  $('event-preview').hidden=false;message('预览已生成，请核对后在下方确认导入。');updateImport();
});
handle('import',async()=>{await api('/api/import',{batch_id:batch.batch_id,calendar_id:$('calendar').value});setWorking(true);pollJob();});
start().catch(e=>message(e.message,true));
