import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc} from '../utils/html.js';
import {download} from '../api/client.js';
import {message} from '../hooks/action.js';

const rangeText=ranges=>(ranges||[]).map(([a,b])=>a===b?`LSN ${a}`:`LSN ${a}—${b}`).join('，')||'无';

function sourcesTable(sources){
  return table(['分段序号','来源','LSN范围','本次使用','摘要'],(sources||[]).map(s=>[s.seq,esc(s.source||'未标注'),`${s.start_lsn}—${s.end_lsn}`,`${s.used_from}—${s.used_to}`,`<code>${esc((s.digest||'').slice(0,12))}…</code>`]));
}

export function detailPage(job){
  if(!job)return '<section class="empty"><h2>还没有恢复记录</h2><p>先选择故障场景或日志位置并提交恢复任务，再逐条查看日志重做与撤销。</p><a href="#runs">进入恢复队列 →</a></section>';
  const r=job.result;
  const snap=job.snapshot||{};
  const segmented=snap.mode==='segmented';
  const missingAfter=segmented?(r?.missing_after_target??snap.missing_after_target??[]):[];
  return `<div class="section-title"><h2>${esc(job.label)} <small>#${job.id}</small></h2>${badge(job.status)}</div>
  ${r?.error?`<section class="verdict"><div><p>恢复强制拒绝</p><h2>拒绝恢复</h2></div><p>${esc(r.error)}</p></section>`:''}
  ${r&&!r.error?`<section class="verdict"><div><p>恢复账本核对</p><h2>${r.invariant_passed?'提交结果一致':'恢复失败'}</h2></div><p>检查点LSN ${r.checkpoint_lsn??'—'} · 目标位置 ${r.target_lsn??r.crash_after??'—'}<br>重做 ${r.redo_count??0} 次 / 撤销 ${r.undo_count??0} 次<br>${r.error?esc(r.error):'未提交事务 '+esc((r.losers||[]).join(', ')||'无')}</p></section>
  ${table(['账本键','检查点值','重做后','恢复后','独立提交结果'],Object.keys(r.recovered||{}).map(k=>[esc(k),r.durable_checkpoint[k],r.after_redo[k],r.recovered[k],r.oracle[k]]))}
  <h2>日志恢复轨迹</h2>${table(['LSN','事务','阶段','键','before → after'],(r.trace||[]).map(t=>[t.lsn,esc(t.tx),esc(t.phase),esc(t.key),t.phase==='undo_loser'||t.phase==='redo_abort_undo'?`${t.after} → ${t.before}`:`${t.before} → ${t.after}`]))}
  <p class="checksum">输入摘要 ${esc(r.input_sha256||'无')}<br>结果摘要 ${esc(r.result_sha256||'无')}</p>`:'<p class="notice">后台恢复计算中，请刷新状态。</p>'}
  <div class="section-title"><h2>来源与覆盖核对</h2><span>${segmented?'分段归档':'完整日志'}</span></div>
  <div class="verdict"><div><p>目标之前覆盖</p><h2>${esc(rangeText(r?.coverage_before_target??snap.coverage_before_target))}</h2></div><p>目标位置 ${job.target_lsn??snap.target_lsn??'—'}<br>目标之后缺段 ${missingAfter.length?esc(missingAfter.slice(0,12).join('、'))+(missingAfter.length>12?'…':''):'无'}<br>${missingAfter.length?'不影响本次恢复，仅提示后续段尚未到齐':'目标之后已无缺段'}</p></div>
  ${sourcesTable(r?.source_segments??snap.source_segments)}
  <div class="toolbar"><button id="export">导出日志与恢复证据</button><button class="secondary" id="refresh">刷新状态</button></div>
  <details><summary>查看冻结输入</summary><pre>${esc(JSON.stringify(job.snapshot,null,2))}</pre></details>`;
}
export function bindDetail(job,render){document.querySelector('#refresh')?.addEventListener('click',render);document.querySelector('#export')?.addEventListener('click',()=>download(job.id).catch(e=>message(e.message,true)));}
