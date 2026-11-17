import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {recoveriesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';

const ui={logSetId:null};
const rangeText=ranges=>(ranges||[]).map(([a,b])=>a===b?`LSN ${a}`:`LSN ${a}—${b}`).join('，')||'无';

function targetHint(logs){
  if(!logs)return '';
  const a=logs.archive||{};
  const firstGap=(a.missing||[])[0];
  const maxTarget=firstGap?firstGap-1:(a.continuous_prefix??0);
  return `<div class="verdict"><div><p>该账本连续前缀</p><h2>LSN 0—${a.continuous_prefix??0}</h2></div><p>覆盖 ${esc(rangeText(a.ranges))}<br>${firstGap?`目标位置需 ≤ ${maxTarget}，否则会因LSN ${firstGap}缺段被拒`:'目标位置不超过最高已归档LSN即可恢复'}<br>目标之后缺段不影响本次演练</p></div>`;
}

function jobRows(runs){
  return runs.map(r=>[r.id,esc(r.label),r.target_lsn??'—',badge(r.status),r.result?.error?'拒绝：'+esc(r.result.error):r.result?.invariant_passed?'与提交事务一致':r.result?'需检查':'待计算',`<a href="#detail/${r.id}">查看恢复轨迹 →</a>`]);
}

export function runsPage(){
  const segmented=store.logs.filter(l=>l.segmented);
  const chosen=store.logs.find(l=>l.id===ui.logSetId)||segmented[0]||store.logs[0];
  const canWrite=session.role!=='viewer'&&store.logs.length>0;
  return `<section class="split compact"><div><h2>发起一次恢复演练</h2><p>按选定日志位置恢复：排队时冻结所选段与目标，之后补传的分段不会改变已排队任务。目标之前必须连续，跨段开启的未提交写入全部撤回。</p>${store.logs.length?targetHint(chosen):'<p class="readonly">还没有日志集，请先在事务日志集中新建或上传分段。</p>'}</div>
  ${canWrite?`<div>
    <form id="run-form"><label>恢复方式<select name="mode"><option value="segment">按日志位置（当前账本）</option><option value="case">按故障场景</option></select></label>
    <div id="segment-target"><label>日志集<select name="log_set_id">${store.logs.map(l=>`<option value="${l.id}" ${l.id===chosen?.id?'selected':''}>${esc(l.name)} · ${l.segmented?'分段':'完整'}</option>`).join('')}</select></label><label>目标日志位置 (LSN)<input name="target_lsn" type="number" min="0" max="200" value="${chosen?.archive?.continuous_prefix??0}" required></label></div>
    <div id="case-target" hidden><label>故障场景<select name="crash_case_id">${store.cases.map(c=>`<option value="${c.id}">${esc(c.name)} · 崩溃点 ${c.crash_after}</option>`).join('')}</select></label></div>
    <label>记录标签<input name="label" value="晚班恢复核对" required></label><button type="submit">冻结所选段与目标 →</button></form>
  </div>`:'<p class="readonly">观察员可查看下方恢复结果。</p>'}</section>
  <div class="section-title"><h2>恢复队列</h2><button class="secondary" id="refresh">刷新状态</button></div>
  ${table(['编号','恢复记录','目标LSN','状态','恢复核对','证据'],jobRows(store.runs))}`;
}

export function bindRuns(render){
  document.querySelector('#refresh').onclick=render;
  const mode=document.querySelector('[name=mode]');
  const syncMode=()=>{
    const seg=mode?.value!=='case';
    const segmentBox=document.querySelector('#segment-target'),caseBox=document.querySelector('#case-target');
    if(segmentBox){segmentBox.hidden=!seg;segmentBox.querySelectorAll('input,select').forEach(el=>{el.disabled=!seg;});}
    if(caseBox){caseBox.hidden=seg;caseBox.querySelectorAll('input,select').forEach(el=>{el.disabled=seg;});}
  };
  mode?.addEventListener('change',syncMode);
  syncMode();
  document.querySelector('[name=log_set_id]')?.addEventListener('change',e=>{ui.logSetId=Number(e.target.value);render();});
  action(document.querySelector('#run-form'),async f=>{
    const d=formObject(f);
    const body={label:d.label};
    if(d.mode!=='case'){body.log_set_id=Number(d.log_set_id);body.target_lsn=Number(d.target_lsn);}
    else body.crash_case_id=Number(d.crash_case_id);
    const job=await recoveriesAPI.create(body);location.hash='#detail/'+job.id;
  });
}
