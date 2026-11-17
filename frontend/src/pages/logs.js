import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {logSetsAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';

const ui={selectedId:null};
const rangeText=ranges=>(ranges||[]).map(([a,b])=>a===b?`LSN ${a}`:`LSN ${a}—${b}`).join('，')||'无';
const logMode=s=>s.segmented?'分段归档':'完整日志';

function segmentRows(logs){
  if(!logs.segmented)return [['—','legacy-complete',`1—${logs.records.length||0}`,logs.records.length,'完整日志']];
  return (logs.segments||[]).map(s=>[s.seq,esc(s.source||'未标注'),`${s.start_lsn}—${s.end_lsn}`,s.record_count,`<code>${esc((s.digest||'').slice(0,12))}…</code>`]);
}

function selectedLog(){
  return store.logs.find(l=>l.id===ui.selectedId)||store.logs[0];
}

function archivePanel(){
  const logs=selectedLog();
  if(!logs)return '<p class="readonly">还没有日志集。可新建分段归档，或录入完整日志。</p>';
  const archive=logs.archive||{};
  const mergedRows=(archive.log||[]).map(row=>[row.lsn,esc(row.record.op),esc(row.record.tx||'—'),esc(row.record.key||'—'),row.record.value??'—']);
  return `<div class="section-title"><h2>${esc(logs.name)} <small>#${logs.id} · ${logMode(logs)}</small></h2><label class="inline">选择日志集<select id="log-select">${store.logs.map(l=>`<option value="${l.id}" ${l.id===logs.id?'selected':''}>${esc(l.name)}</option>`).join('')}</select></label></div>
  <div class="verdict"><div><p>连续前缀</p><h2>LSN 0—${archive.continuous_prefix??0}</h2></div><p>已归档 ${archive.covered_count??0} 条 / 最高 LSN ${archive.max_lsn??0}<br>覆盖 ${esc(rangeText(archive.ranges))}<br>${archive.missing?.length?`缺口 ${esc(archive.missing.slice(0,12).join('、'))}${archive.missing.length>12?'…':''}`:'无缺口'}</p></div>
  <div class="section-title"><h2>分段来源</h2><span>相同重传自动合并，重叠但内容不同则整批拒绝</span></div>
  ${table(['分段序号','来源','LSN范围','条数','摘要'],segmentRows(logs))}
  <details open><summary>合并后的日志（按 LSN，可据此选择目标位置）</summary>${table(['LSN','操作','事务','键','值'],mergedRows)}</details>`;
}

export function logsPage(){
  const sample=store.logs.find(l=>!l.segmented)||store.logs[0];
  const canWrite=session.role!=='viewer';
  return `<div class="section-title"><h2>事务日志集</h2><span>最多200条日志 / 30个键 · 分段归档可乱序补传</span></div>
  ${table(['日志集','初始账本','模式','覆盖范围','记录数','说明'],store.logs.map(s=>[esc(s.name),esc(JSON.stringify(s.initial)),logMode(s),esc(rangeText(s.archive?.ranges)),s.archive?.covered_count??(s.records||[]).length,esc(s.description)]))}
  ${archivePanel()}
  <section class="split"><div><h2>分段归档规则</h2><p>每段声明分段序号与起始LSN（1起）。乱序到达可合并，完全相同重传只保留一份；同一 LSN 内容不同则整批拒绝，不覆盖已归档内容。</p><p>目标位置之前必须连续；目标之后缺段不影响本次恢复。跨段开启的未提交事务在恢复时全部按LSN倒序撤回。</p><p class="big-note">先凑齐目标之前，再谈恢复之后。</p><details><summary>查看示例日志</summary><pre>${esc(JSON.stringify(sample?.archive?.log?.map(x=>x.record)||sample?.records||[],null,2))}</pre></details></div>
  ${canWrite?`<div>
    <h2>新建分段归档账本</h2>
    <form id="archive-form"><label>日志集名<input name="name" value="shift-20261117-分段" required></label><label>说明<input name="description" value="晚班账本按段回传"></label><label>初始账本 JSON<textarea name="initial" rows="2" required>${esc(JSON.stringify(sample?.initial||{sample_A:100,sample_B:50}))}</textarea></label><div class="fields"><label>来源批次<input name="source" value="shift-a" required></label><label>分段序号<input name="seq" type="number" min="0" value="1" required></label><label>起始LSN<input name="start_lsn" type="number" min="1" max="200" value="1" required></label></div><label>该段日志 JSON 数组<textarea name="records" rows="6" required>${esc(JSON.stringify((sample?.records||[]).slice(0,5),null,2))}</textarea></label><button type="submit">新建并接收第一段</button></form>
    <h2>向已有账本追加分段</h2>
    ${store.logs.some(l=>l.segmented)?`<form id="segment-form"><label>目标分段账本<select name="log_set_id">${store.logs.filter(l=>l.segmented).map(l=>`<option value="${l.id}">${esc(l.name)} · LSN ${l.archive?.continuous_prefix??0}连续</option>`).join('')}</select></label><div class="fields"><label>来源批次<input name="source" value="shift-b" required></label><label>分段序号<input name="seq" type="number" min="0" value="2" required></label><label>起始LSN<input name="start_lsn" type="number" min="1" max="200" value="6" required></label></div><label>该段日志 JSON 数组<textarea name="records" rows="6" required>${esc(JSON.stringify((sample?.records||[]).slice(5),null,2))}</textarea></label><button type="submit">合并到归档</button></form>`:'<p class="readonly">还没有分段归档账本；先用上方表单新建，或选择已有分段账本。</p>'}
    <h2>录入完整日志（原演练）</h2>
    <form id="log-form"><label>日志集名<input name="name" value="shift-20261117" required></label><label>说明<input name="description" value="晚班采样账本故障复核"></label><label>初始账本 JSON<textarea name="initial" rows="2" required>${esc(JSON.stringify(sample?.initial||{sample_A:100,sample_B:50}))}</textarea></label><label>日志 JSON 数组<textarea name="records" rows="6" required>${esc(JSON.stringify(sample?.records||[],null,2))}</textarea></label><button type="submit">校验并保存完整日志集</button></form>
  </div>`:'<p class="readonly">观察员仅查看，上传分段与新建账本需要操作员或管理员。</p>'}</section>`;
}

function segmentPayload(d){
  return {source:d.source,segments:[{seq:Number(d.seq),start_lsn:Number(d.start_lsn),records:JSON.parse(d.records)}]};
}

export function bindLogs(render){
  document.querySelector('#log-select')?.addEventListener('change',e=>{ui.selectedId=Number(e.target.value);render();});
  action(document.querySelector('#log-form'),async f=>{const d=formObject(f);d.initial=JSON.parse(d.initial);d.records=JSON.parse(d.records);await logSetsAPI.create(d);await render();});
  action(document.querySelector('#archive-form'),async f=>{const d=formObject(f);const body={name:d.name,description:d.description,initial:JSON.parse(d.initial),...segmentPayload(d)};const out=await logSetsAPI.createArchive(body);ui.selectedId=out.id;await render();});
  action(document.querySelector('#segment-form'),async f=>{const d=formObject(f);const out=await logSetsAPI.appendSegments(Number(d.log_set_id),segmentPayload(d));ui.selectedId=out.id;
    const added=out.batch?.added_lsns?.length??0,dup=out.batch?.duplicate_lsns?.length??0;
    await render();return `已合并：新增 ${added} 条，相同重传 ${dup} 条`;});
}
