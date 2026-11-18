import {request} from './client.js';
export async function loadAll(){const [sources,periods,policies,runs,schedules,deliveries,audits,bills,reconciledBills]=await Promise.all(['sources','periods','policies','runs','schedules','deliveries','audits','bills','bills/reconciled'].map(k=>request('/'+k)));return {sources,periods,policies,runs,schedules,deliveries,audits,bills,reconciledBills}}
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
export const saveBill=(data,id)=>request('/bills'+(id?'/'+id:''),id?'PUT':'POST',data);
export const reconcileBill=id=>request('/bills/'+id+'/reconcile','POST',{});
export const loadBill=id=>request('/bills/'+id);
