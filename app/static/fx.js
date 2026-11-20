const fx={active:false,batch:null};
const box=document.getElementById('fx-error');
const $=id=>document.getElementById(id);
async function call(url,payload){
  if(fx.active)return null;fx.active=true;box.textContent='';
  for(const b of document.querySelectorAll('button'))b.disabled=true;
  try{
    const options={method:'POST',headers:{'Content-Type':'application/json'}};
    if(payload!==undefined)options.body=JSON.stringify(payload);
    const response=await fetch(url,options);
    const result=await response.json();
    if(!response.ok)throw Error(result.message||'请求失败');
    return result;
  }catch(e){box.textContent=e.message;return null}
  finally{fx.active=false;for(const b of document.querySelectorAll('button'))b.disabled=false;refreshButtons()}
}
function text(id,value){$(id).textContent=value===''||value==null?'—':value}
function render(batch){
  fx.batch=batch;
  $('fx-batch-badge').textContent=batch.action+' · '+batch.id.slice(0,8);
  $('fx-evidence').href='/api/fx/batches/'+batch.id+'/evidence.csv';
  $('fx-state-tag').textContent=batch.state;
  text('k-state',batch.state);text('k-hkd',batch.totals['应得_HKD']);text('k-locked',batch.totals['锁定_CNY']);
  text('k-paid',batch.totals['实付_CNY']);text('k-diff',batch.totals['差异_CNY']);
  text('k-bank',batch.totals['银行回款_CNY']);text('k-recon',batch.totals['对账差异_CNY']);
  text('fx-created',batch.created_at+' / UTC');text('fx-rule','规则 '+batch.rule_version);
  text('fx-pay-count',batch.payments.length+' 条');
  $('fx-payments').innerHTML=batch.payments.map(p=>'<tr>'+['客户','股份','每股分红_HKD','应得_HKD','锁定汇率','锁定_CNY','采用汇率','实付_CNY','差异_CNY','状态'].map(k=>'<td>'+(p[k]||'—')+'</td>').join('')+'</tr>').join('');
  $('fx-history').innerHTML=batch.history.map(h=>'<tr><td>'+h.at+'</td><td>'+h.event+'</td><td>'+h.detail+'</td></tr>').join('');
  refreshButtons();
}
function refreshButtons(){
  const state=fx.batch?fx.batch.state:null,has=!!fx.batch;
  $('fx-confirm').disabled=!has||state==='已对账'||state==='已发布';
  $('fx-bank-btn').disabled=!has||state!=='待对账';
  $('fx-publish').disabled=!has||state!=='已对账';
}
async function refreshList(){
  const list=await (await fetch('/api/fx/batches')).json();
  text('fx-batch-count',list.length+' 批');
  $('fx-batches').innerHTML=list.map(b=>'<tr data-id="'+b.id+'"><td>'+b.action+'</td><td>'+b.state+'</td><td>'+b['客户数']+'</td><td>'+(b['实付_CNY']||'—')+'</td></tr>').join('');
  for(const row of document.querySelectorAll('#fx-batches tr'))row.addEventListener('click',async()=>{
    const batch=await (await fetch('/api/fx/batches/'+row.dataset.id)).json();render(batch);
  });
}
$('fx-create').addEventListener('click',async()=>{
  let payload;try{payload=JSON.parse($('fx-source').value)}catch(e){box.textContent='宣告JSON格式无效';return}
  const batch=await call('/api/fx/batches',payload);
  if(batch){render(batch);refreshList();if(batch.reused)box.textContent='相同宣告已存在，复用原批次，未产生重复支付。'}
});
$('fx-confirm').addEventListener('click',async()=>{
  if(!fx.batch)return;
  const batch=await call('/api/fx/batches/'+fx.batch.id+'/rate',{rate:$('fx-rate').value});
  if(batch){render(batch);refreshList()}
});
$('fx-bank-btn').addEventListener('click',async()=>{
  if(!fx.batch)return;
  const batch=await call('/api/fx/batches/'+fx.batch.id+'/bank-return',{amount:$('fx-bank').value});
  if(batch){render(batch);refreshList()}
});
$('fx-publish').addEventListener('click',async()=>{
  if(!fx.batch)return;
  const batch=await call('/api/fx/batches/'+fx.batch.id+'/publish',{});
  if(batch){render(batch);refreshList()}
});
refreshList();
