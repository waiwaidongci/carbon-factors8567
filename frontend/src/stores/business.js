import {loadAll,loadBill} from '../api/business.js';
export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],bills:[],reconciledBills:[]},selected:null,selectedBill:null,billDetail:null};
export async function reload(){store.data=await loadAll();const id=store.selectedBill??store.data.bills[0]?.id??null;store.selectedBill=id;store.billDetail=id?await loadBill(id):null}
export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
