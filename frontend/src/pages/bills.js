import {heading} from '../components/common/header.js';import {table} from '../components/common/table.js';import {esc,fmt,time} from '../utils/format.js';import {BILL_STATUSES} from '../constants/enums.js';

const BILL_STATUS=BILL_STATUSES;
const billStatus=v=>`<span class="status ${esc(v)}">${esc(BILL_STATUS[v]||v)}</span>`;

function claimBlock(lineData){const claims=lineData.claims||[];if(claims.length)return `<div class="claims">${claims.map(c=>`<span class="claim-chip">#${c.source_id} ${esc(c.data.period)} · ${esc(c.data.department)} · ${esc(c.quantity)} ${esc(c.unit)} = ${fmt(Number(c.quantity_kwh))} kWh</span>`).join('')}</div>`;const report=lineData.report||{};const samples=report.candidate_samples||[];const reason=report.reason||(report.candidate_count?'该行存在多套候选组合，整单不唯一，未认领。':'尚未对账。');return `<p class="error">${esc(reason)}</p>${samples.length?`<details><summary>候选底稿组合样本（共 ${report.candidate_count} 套）</summary>${table(samples.map((s,i)=>({组合:i+1,底稿:s.map(d=>`#${d.source_id} ${d.quantity} ${d.unit}`).join('；'),合计kWh:s.reduce((sum,d)=>sum+Number(d.quantity_kwh),0)})))}</details>`:''}`}

function billDetail(bill,canEdit){if(!bill)return '<p class="empty">选择一张账单查看逐行追查结果。</p>';const result=bill.result||{};return `<section class="report"><div class="report-top"><h2>${esc(bill.name)}</h2>${billStatus(bill.status)}</div><p>第 ${bill.revision} 版 · 账本版本 ${bill.ledger_revision??'—'} · ${time(bill.solved||bill.created)} · 录入 ${esc(bill.created_by||'')}</p><p class="${bill.status==='reconciled'?'method':'warning'}">${esc(result.explanation||'尚未对账')}</p>${result.truncated?'<p class="warning">候选组合超出穷尽核验上限，结果按争议处理。</p>':''}${bill.lines.map(l=>`<div class="bill-line-card"><div class="bill-line-head"><b>第 ${l.position} 行</b><span>${esc(l.period)} · ${esc(l.department)}</span><span>账单 ${esc(l.quantity)} ${esc(l.unit)} = <b>${fmt(Number(l.target_kwh))} kWh</b></span><span>可用底稿 ${l.report?.available_count??0} 条 · 候选 ${l.report?.candidate_count??0} 套</span></div>${claimBlock(l)}</div>`).join('')}${canEdit&&bill.status!=='reconciled'?`<div class="report-actions"><button class="reconcile-bill" data-id="${bill.id}">整单对账</button></div>`:''}</section>`}

export function bills(c,d,selected,selectedBill){
 const canEdit=d.session?.role==='admin'||d.session?.role==='analyst';
 const bill=selectedBill;
 const rows=d.bills.map(b=>({编号:b.id,账单:b.name,状态:BILL_STATUS[b.status]||b.status,行数:b.line_count,认领:b.claim_count,版本:b.revision,说明:b.explanation||''}));
 return heading('BILL RECONCILIATION / 02','电费账单对账','按部门与月份合并底稿：账单电量统一为 kWh，各账单行须与互不重叠的电力底稿组合精确相等；仅整单唯一解才认领。')+
 `<details open><summary>录入电费账单（一行可合并多条底稿）</summary><form id="bill-form"><label>账单名称<input name="name" value="办公区十二月电费账单" required minlength="2"></label><div id="bill-lines">${billLineRow()}</div><div class="bill-line-actions"><button type="button" id="add-bill-line">增加账单行</button><button>保存为草稿</button></div></form></details>`+
 `<h2>账单列表</h2>${table(rows)}<div class="run-layout" style="margin-top:30px"><aside class="run-list">${d.bills.map(b=>`<button class="select-bill" data-id="${b.id}">${esc(b.name)} ${billStatus(b.status)}</button>`).join('')}</aside><div>${billDetail(bill,canEdit)}</div></div>`;
}

export function billLineRow(){return `<div class="policy-row bill-line"><label>月份<input name="period" value="2026-12" required></label><label>部门<input name="department" value="办公区" required></label><label>电量<input name="quantity" type="number" step="any" min="0" value="800" required></label><label>单位<select name="unit"><option>kWh</option><option>MWh</option></select></label><button type="button" class="remove-bill-line">删除行</button></div>`}

