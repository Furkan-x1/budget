const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const state = {page:'dashboard', month:'', accounts:[], meta:null, quotes:{}, tradeType:'buy', debts:[], entriesById:{}, occurrencesByKey:{}};
const localDate = () => {const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
const toTurkishDate = iso => iso ? iso.split('-').reverse().join('.') : '';
function toISODate(value){
  const raw=String(value||'').trim();
  if(!raw)return '';
  let day,month,year;
  if(/^\d{4}-\d{2}-\d{2}$/.test(raw)){[year,month,day]=raw.split('-').map(Number);}
  else {
    const match=raw.match(/^(\d{1,2})[.\\/ -](\d{1,2})[.\\/ -](\d{4})$/);
    if(!match)throw new Error('Tarihi GG.AA.YYYY biçiminde gir. Örnek: 08.11.2026');
    day=Number(match[1]);month=Number(match[2]);year=Number(match[3]);
  }
  const test=new Date(Date.UTC(year,month-1,day));
  if(test.getUTCFullYear()!==year||test.getUTCMonth()!==month-1||test.getUTCDate()!==day)throw new Error('Geçerli bir tarih gir. Biçim: GG.AA.YYYY');
  return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
}
function getDateISO(id, optional=false){
  const el=$(`#${id}`), raw=el?.value||'';
  if(optional&&!raw.trim())return null;
  try{return toISODate(raw)}catch(err){toast(err.message);el?.focus();return null}
}
function bindDateInputs(root=document){
  $('[data-date-input]',root).forEach(el=>{
    if(el.dataset.dateBound)return;el.dataset.dateBound='1';
    el.addEventListener('input',()=>{
      const digits=el.value.replace(/\\D/g,'').slice(0,8);
      let formatted=digits;
      if(digits.length>4)formatted=digits.slice(0,2)+'.'+digits.slice(2,4)+'.'+digits.slice(4);
      else if(digits.length>2)formatted=digits.slice(0,2)+'.'+digits.slice(2);
      el.value=formatted;
    });
  });
}
state.month=localDate().slice(0,7);
const fmtTRY = value => new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY',maximumFractionDigits:2}).format(Number(value||0));
const fmtNum = (value, digits=6) => new Intl.NumberFormat('tr-TR',{maximumFractionDigits:digits}).format(Number(value||0));
const fmtAsset = (value, code) => {
  const n = Number(value||0);
  if (code === 'TRY') return fmtTRY(n);
  if (['USD','EUR','GBP','CHF','JPY','CAD','AUD','CNY','AED','SAR','RUB','KWD'].includes(code)) return new Intl.NumberFormat('tr-TR',{style:'currency',currency:code,maximumFractionDigits:2}).format(n);
  const unit = ({GOLD_GRAM:'gr',GOLD_CEYREK:'adet',GOLD_HALF:'adet',GOLD_FULL:'adet',GOLD_CUMHURIYET:'adet',SILVER_GRAM:'gr'})[code] || code;
  return `${fmtNum(n)} ${unit}`;
};
const moneySign = type => type==='income' ? '+' : type==='expense' ? '−' : '↔';
const iconFor = type => type==='income' ? '↗' : type==='expense' ? '↘' : '⇄';
const esc = v => String(v??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3000)}
async function api(url, opts={}){
  const response=await fetch(url,{headers:{'Content-Type':'application/json',...(opts.headers||{})},...opts});
  const text=await response.text();let data={};try{data=text?JSON.parse(text):{}}catch{data={detail:text}}
  if(!response.ok) throw new Error(data.detail||`İstek başarısız (${response.status})`);
  return data;
}
function showPage(page){
  state.page=page;$$('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`));
  $$('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
  const names={dashboard:'Genel bakış',transactions:'İşlemler',accounts:'Hesaplar',plans:'Tekrarlananlar',debts:'Borç / alacak',goals:'Hedefler',reports:'Raporlar',categories:'Kategoriler'};
  $('#page-name').textContent=names[page]||'Budget';
  if(page==='transactions')loadEntries();if(page==='accounts')renderAccounts();if(page==='plans')loadPlans();
  if(page==='debts')loadDebts();if(page==='goals')loadGoals();if(page==='reports')loadAnalytics();if(page==='categories')loadCategories();
}
function monthLabel(month){const [y,m]=month.split('-').map(Number);return new Intl.DateTimeFormat('tr-TR',{month:'short',year:'2-digit'}).format(new Date(y,m-1,1))}
function dateLabel(value){if(!value)return 'Vade belirtilmedi';return new Intl.DateTimeFormat('tr-TR',{day:'2-digit',month:'short',year:'numeric'}).format(new Date(`${value}T12:00:00`))}
function accountAsset(id){return state.accounts.find(a=>a.id===Number(id))?.asset_code||'TRY'}
function entryRow(e, detailed=false){
  const trade=e.trade_type;const transfer=e.entry_type==='transfer';const type=transfer?'transfer':e.entry_type;
  const desc=e.description||e.category||'İşlem';const dateText=dateLabel(e.entry_date);
  let amount;
  if(trade==='buy')amount=`+${fmtAsset(e.asset_quantity,e.traded_asset_code)} · ${fmtAsset(e.cash_amount,e.cash_asset_code)} maliyet`;
  else if(trade==='sell')amount=`−${fmtAsset(e.asset_quantity,e.traded_asset_code)} · +${fmtAsset(e.cash_amount,e.cash_asset_code)} net satış`;
  else if(transfer)amount=`${fmtAsset(e.amount,e.asset_code)} → ${fmtAsset(e.target_amount||e.amount,e.target_asset_code||e.asset_code)}`;
  else amount=`${moneySign(type)}${fmtAsset(e.amount,e.asset_code)}`;
  const subtitle=trade?`${dateText} · ${trade==='buy'?'Varlık alımı':'Varlık satışı'}`:`${dateText} · ${esc(e.account_name||'')}`;
  return `<div class="list-row"><div class="row-main"><span class="row-symbol">${trade?'◇':iconFor(type)}</span><div><div class="row-title">${esc(desc)}</div><div class="row-sub">${subtitle}</div></div></div><div class="row-actions"><div class="row-amount ${trade?'transfer':type}">${amount}</div>${detailed?`<button class="tiny-button" data-edit-entry="${e.id}">Düzenle</button><button class="tiny-button danger" data-delete-entry="${e.id}">Sil</button>`:''}</div></div>`;
}
function plannedRow(item){const income=item.entry_type==='income';const due=dateLabel(item.due_date);return `<div class="list-row"><div class="row-main"><span class="row-symbol">${income?'↗':'↘'}</span><div><div class="row-title">${esc(item.description)}</div><div class="row-sub">${due} · ${esc(item.account_name||'')}</div></div></div><div class="row-actions"><div class="row-amount ${income?'income':'expense'}">${income?'+':'−'}${fmtAsset(item.amount,item.asset_code||'TRY')}</div><button class="tiny-button" data-edit-occurrence="${item.source_type}" data-source-id="${item.source_id}" data-key="${item.occurrence_key}">Düzenle</button><button class="tiny-button" data-complete="${item.source_type}" data-source-id="${item.source_id}" data-key="${item.occurrence_key}">Tamamlandı</button></div></div>`}
async function loadMeta(){
  state.meta=await api('/api/meta');
  const assets=Object.entries(state.meta.assets).map(([k,v])=>`<option value="${k}">${esc(v)} (${k})</option>`).join('');
  ['account-asset','debt-asset','goal-asset'].forEach(id=>{const el=$(`#${id}`);if(el)el.innerHTML=assets});
  updateCategoryOptions();
}
function updateCategoryOptions(){const cats=state.meta?.categories||[];$('#category-options').innerHTML=cats.map(x=>`<option value="${esc(x)}"></option>`).join('');}
async function loadAccounts(){state.accounts=await api('/api/accounts');fillAccountSelects();renderAccounts();updateGoalAccountOptions();updateTradeAccountOptions();updateDebtOpeningAccounts();updateDebtPaymentAccounts();}
function accountOptions(accounts, includeEmpty=false){return (includeEmpty?'<option value="">Hesap bağlama</option>':'')+accounts.map(a=>`<option value="${a.id}">${esc(a.name)} · ${esc(a.asset_code)}</option>`).join('')}
function fillAccountSelects(){
  const options=accountOptions(state.accounts);
  ['entry-account','entry-target-account','schedule-account','installment-account','goal-account'].forEach(id=>{const el=$(`#${id}`);if(el){const old=el.value;el.innerHTML=id==='goal-account'?accountOptions(state.accounts.filter(a=>a.kind!=='credit_card'),true):options;if([...el.options].some(o=>o.value===old))el.value=old;}});
  const debtAccounts=state.accounts.filter(a=>a.kind!=='credit_card');
  const debtEl=$('#debt-payment-account');if(debtEl){const old=debtEl.value;debtEl.innerHTML=accountOptions(debtAccounts);if([...debtEl.options].some(o=>o.value===old))debtEl.value=old;}
}
function accountItem(a, detailed=false){
  const kind=({cash:'Nakit',bank:'Banka',credit_card:'Kredi kartı',investment:'Yatırım'})[a.kind]||a.kind;
  return `<div class="list-row"><div class="row-main"><span class="row-symbol">${a.kind==='credit_card'?'▣':a.asset_code.startsWith('GOLD')?'◇':'▤'}</span><div><div class="row-title">${esc(a.name)}</div><div class="row-sub">${kind} · ${esc(a.asset_label)}</div></div></div><div class="row-actions"><div class="account-value"><strong>${fmtAsset(a.balance,a.asset_code)}</strong><small>${a.rate_stale?'Kur eski · ':''}${fmtTRY(a.value_try)}${a.quote_bid?` · Alış ${fmtNum(a.quote_bid,2)} / satış ${fmtNum(a.quote_ask,2)} TL`:''}</small></div>${detailed?`<button class="tiny-button danger" data-delete-account="${a.id}">Sil</button>`:''}</div></div>`;
}
function renderAccounts(){const html=state.accounts.length?state.accounts.map(a=>accountItem(a,true)).join(''):'<div class="empty-state">Henüz hesap eklemedin. Soldaki formdan ilk hesabını oluştur.</div>';$('#all-accounts').innerHTML=html;$('#dashboard-accounts').innerHTML=state.accounts.length?state.accounts.slice(0,5).map(a=>accountItem(a)).join(''):'<div class="empty-state">Henüz hesap eklemedin.</div>'}
function renderNetWorthChart(points, target){
  const el=$(target);if(!points?.length){el.innerHTML='<div class="empty-state">Henüz geçmiş veri yok. Günlük kayıtlar burada birikecek.</div>';return;}
  const w=720,h=180,pad=22;const values=points.map(x=>Number(x.total_value));const min=Math.min(...values),max=Math.max(...values);const span=max-min||1;
  const coords=values.map((v,i)=>[pad+(points.length===1?0:i*(w-pad*2)/(points.length-1)),h-pad-((v-min)/span)*(h-pad*2)]);
  const poly=coords.map(x=>x.join(',')).join(' ');const area=`${pad},${h-pad} ${poly} ${coords.at(-1)[0]},${h-pad}`;
  const first=dateLabel(points[0].snapshot_date),last=dateLabel(points.at(-1).snapshot_date);
  el.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Net varlık geçmişi grafiği" class="line-chart"><line x1="${pad}" y1="${h-pad}" x2="${w-pad}" y2="${h-pad}" class="chart-axis"/><polygon points="${area}" class="chart-area-fill"/><polyline points="${poly}" class="chart-line"/>${coords.length<80?coords.map(([x,y])=>`<circle cx="${x}" cy="${y}" r="2.6" class="chart-dot"/>`).join(''):''}</svg><div class="chart-caption"><span>${esc(first)}</span><strong>${fmtTRY(values.at(-1))}</strong><span>${esc(last)}</span></div>`;
}
async function loadDashboard(){
  const [d,a]=await Promise.all([api(`/api/dashboard?month=${state.month}`),api(`/api/analytics?month=${state.month}&months=12`)]);
  $('#total-net-worth').textContent=fmtTRY(d.total_net_worth);$('#month-income').textContent=fmtTRY(d.income);$('#month-expense').textContent=fmtTRY(d.expense);$('#forecast-net').textContent=fmtTRY(d.forecast_net);
  $('#net-worth-note').textContent=`${d.accounts.length} hesap · borç/alacak dahil · ${monthLabel(state.month)}`;
  const max=Math.max(d.income,d.expense,1);$('#cashflow-bars').innerHTML=`<div class="bar-group"><span class="bar-legend">Gelir ${fmtTRY(d.income)}</span><div class="bar income" style="height:${Math.max(3,d.income/max*90)}%"></div><div class="bar expense" style="height:${Math.max(3,d.expense/max*90)}%"></div><span class="bar-label">${esc(monthLabel(state.month))}</span></div>`;
  $('#upcoming-list').innerHTML=d.upcoming.length?d.upcoming.map(plannedRow).join(''):'<div class="empty-state">Bu ay için yaklaşan işlem yok.</div>';
  $('#recent-list').innerHTML=d.recent.length?d.recent.map(e=>entryRow(e)).join(''):'<div class="empty-state">Henüz işlem yok.</div>';
  $('#rate-status').textContent=d.quote_updated_at?`Kur kaynağı: TCMB + piyasa verisi · Son güncelleme: ${new Date(d.quote_updated_at).toLocaleString('tr-TR')}${d.quote_stale?' · Bazı fiyatlar eski veya güncellenemedi.':''}`:'Kur verisi henüz alınmadı.';
  renderNetWorthChart(a.net_worth_history,'#dashboard-networth-chart');
}
async function loadEntries(){const entries=await api(`/api/entries?month=${state.month}&limit=300`);state.entriesById=Object.fromEntries(entries.map(e=>[e.id,e]));$('#entry-count').textContent=`${entries.length} kayıt`;$('#all-entries').innerHTML=entries.length?entries.map(e=>entryRow(e,true)).join(''):'<div class="empty-state">Bu ay henüz işlem yok.</div>'}

function isoMonthOffset(month,offset,day='01'){
  const [year,mon]=month.split('-').map(Number);const d=new Date(year,mon-1+offset,Number(day),12);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function occurrenceRow(item){
  const completed=item.status==='completed';
  const label=completed?'Gerçekleşti':'Planlandı';
  return `<div class="list-row"><div class="row-main"><span class="row-symbol">${completed?'✓':item.entry_type==='income'?'↗':'↘'}</span><div><div class="row-title">${esc(item.description)}</div><div class="row-sub">${dateLabel(item.due_date)} · ${esc(item.account_name||'')} · ${label}</div></div></div><div class="row-actions"><div class="row-amount ${item.entry_type}">${item.entry_type==='income'?'+':'−'}${fmtAsset(item.amount,item.asset_code||'TRY')}</div><button class="tiny-button" data-edit-occurrence="${item.source_type}" data-source-id="${item.source_id}" data-key="${item.occurrence_key}" data-status="${item.status}">Düzenle</button>${completed?'':`<button class="tiny-button" data-complete="${item.source_type}" data-source-id="${item.source_id}" data-key="${item.occurrence_key}">Tamamlandı</button>`}</div></div>`;
}
async function loadOccurrences(){
  const from=isoMonthOffset(state.month,-6),to=isoMonthOffset(state.month,13,'00');
  const items=await api(`/api/occurrences?from_date=${from}&to_date=${to}`);
  state.occurrencesByKey=Object.fromEntries(items.map(x=>[`${x.source_type}:${x.source_id}:${x.occurrence_key}`,x]));
  $('#occurrence-count').textContent=`${items.length} işlem`;
  $('#occurrence-list').innerHTML=items.length?items.map(occurrenceRow).join(''):'<div class="empty-state">Bu dönemde planlanmış işlem yok.</div>';
}
function openEditEntry(id){
  const entry=state.entriesById[id];if(!entry){toast('İşlem kaydı yenilenemedi. Listeyi yenile.');return}
  $('#edit-kind').value='entry';$('#edit-entry-id').value=id;
  $('#edit-title').textContent='İşlemi düzenle';
  $('#edit-description').textContent='Bu değişiklik yalnızca bu kayıt için geçerli.';
  $('#edit-date').value=toTurkishDate(entry.entry_date);
  $('#edit-description-input').value=entry.description||'';
  $('#edit-category').value=entry.category||'';
  $('#edit-account-field').classList.add('hidden');
  const trade=entry.trade_type==='buy'||entry.trade_type==='sell';
  const transfer=entry.entry_type==='transfer'&&!trade;
  $('#edit-trade-quantity-field').classList.toggle('hidden',!trade);
  $('#edit-trade-cash-field').classList.toggle('hidden',!trade);
  $('#edit-target-amount-field').classList.toggle('hidden',!transfer);
  $('#edit-amount-field').classList.toggle('hidden',trade);
  $('#edit-category-field').classList.toggle('hidden',trade);
  if(trade){$('#edit-trade-quantity').value=entry.asset_quantity;$('#edit-trade-cash').value=entry.cash_amount;}
  else{$('#edit-amount').value=entry.amount;if(transfer)$('#edit-target-amount').value=entry.target_amount||entry.amount;}
  $('#edit-dialog').showModal();bindDateInputs($('#edit-dialog'));
}
function openEditOccurrence(source,id,key){
  const item=state.occurrencesByKey[`${source}:${id}:${key}`];if(!item){toast('Planlanan işlem bulunamadı. Listeyi yenile.');return}
  $('#edit-kind').value='occurrence';$('#edit-entry-id').value=item.entry_id||'';
  $('#edit-source-type').value=source;$('#edit-source-id').value=id;$('#edit-occurrence-key').value=key;
  $('#edit-title').textContent=item.status==='completed'?'Gerçekleşen işlemi düzenle':'Planlanan işlemi düzenle';
  $('#edit-description').textContent='Yalnızca bu ay/tek taksit değişir; ana tekrarlanan işlem veya taksit planı aynı kalır.';
  $('#edit-date').value=toTurkishDate(item.due_date);$('#edit-amount').value=item.amount;
  $('#edit-description-input').value=item.description||'';$('#edit-category').value=item.category||'';
  $('#edit-trade-quantity-field').classList.add('hidden');$('#edit-trade-cash-field').classList.add('hidden');
  $('#edit-target-amount-field').classList.add('hidden');$('#edit-amount-field').classList.remove('hidden');$('#edit-category-field').classList.remove('hidden');
  const accountField=$('#edit-account-field');accountField.classList.toggle('hidden',item.status==='completed');
  if(item.status!=='completed'){
    $('#edit-account').innerHTML=accountOptions(state.accounts.filter(a=>a.kind!=='credit_card'));
    $('#edit-account').value=String(item.account_id);
  }
  $('#edit-dialog').showModal();bindDateInputs($('#edit-dialog'));
}
function closeEditDialog(){$('#edit-dialog').close()}

async function loadPlans(){
  const [schedules,installments]=await Promise.all([api('/api/schedules'),api('/api/installments')]);
  $('#schedule-list').innerHTML=schedules.length?schedules.map(s=>`<div class="schedule-item"><div><div class="schedule-title">${esc(s.name)}</div><div class="schedule-sub">${s.entry_type==='income'?'Gelir':'Gider'} · ${s.frequency==='monthly'?'Her ay':s.frequency==='weekly'?'Her hafta':'Her yıl'} · ${s.active?'Aktif':'Duraklatıldı'}</div></div><div class="schedule-side"><div class="schedule-amount ${s.entry_type}">${s.entry_type==='income'?'+':'−'}${fmtAsset(s.amount,accountAsset(s.account_id))}</div><button class="tiny-button" data-toggle-schedule="${s.id}">${s.active?'Duraklat':'Etkinleştir'}</button></div></div>`).join(''):'<div class="empty-state">Henüz tekrarlanan işlem yok.</div>';
  $('#installment-list').innerHTML=installments.length?installments.map(p=>{const left=p.total_installments-p.current_installment;return `<div class="schedule-item"><div><div class="schedule-title">${esc(p.name)}</div><div class="schedule-sub">${p.current_installment}/${p.total_installments} · ${left} gelecek taksit · ${esc(p.account_name)}</div></div><div class="schedule-side"><div class="schedule-amount">${fmtTRY(p.total_amount)}</div><div class="schedule-state">${fmtTRY(p.total_amount/p.total_installments)} / ay</div></div></div>`}).join(''):'<div class="empty-state">Henüz taksit yok.</div>';
  await loadOccurrences();
}
function setEntryType(type){$('#entry-form [name="entry_type"]').value=type;$$('#entry-type-switch button').forEach(b=>b.classList.toggle('selected',b.dataset.type===type));$('#entry-target-field').classList.toggle('hidden',type!=='transfer');$('#entry-target-amount-field').classList.toggle('hidden',type!=='transfer');$('#entry-category-field').classList.toggle('hidden',type==='transfer')}
function updateTradeAccountOptions(){
  const assetEl=$('#trade-asset-account'),cashEl=$('#trade-cash-account');if(!assetEl||!cashEl)return;
  const choices=state.accounts.filter(a=>a.kind!=='credit_card');const oldAsset=assetEl.value;assetEl.innerHTML=accountOptions(choices);if(choices.some(a=>String(a.id)===oldAsset))assetEl.value=oldAsset;
  updateTradeCashOptions();
}
function updateTradeCashOptions(){const assetId=Number($('#trade-asset-account').value);const asset=state.accounts.find(a=>a.id===assetId);const cashEl=$('#trade-cash-account');if(!cashEl)return;const old=cashEl.value;const choices=state.accounts.filter(a=>a.kind!=='credit_card'&&a.id!==assetId&&(!asset||a.asset_code!==asset.asset_code));cashEl.innerHTML=accountOptions(choices);if(choices.some(a=>String(a.id)===old))cashEl.value=old;}
function setTradeType(type){
  state.tradeType=type;$('#trade-type').value=type;$$('#trade-type-switch button').forEach(b=>b.classList.toggle('selected',b.dataset.tradeType===type));
  const buy=type==='buy';$('#trade-asset-account-label').textContent=buy?'Alınan varlığın hesabı':'Satılan varlığın hesabı';$('#trade-cash-account-label').textContent=buy?'Ödeme hesabı':'Satış gelirinin yatırılacağı hesap';$('#trade-asset-quantity-label').textContent=buy?'Alınan miktar':'Satılan miktar';$('#trade-cash-amount-label').textContent=buy?'Hesaptan çıkan gerçek tutar':'Hesaba geçen net tutar';$('#trade-hint').textContent=buy?'Ödeme hesabından girdiğin gerçek tutar düşer, varlık hesabına girdiğin miktar eklenir. Vergi ve makas dahil toplam maliyeti sen belirlersin.':'Varlık hesabından girdiğin miktar düşer, para hesabına satıştan eline geçen net tutar eklenir.';$('#trade-form button[type="submit"]').textContent=buy?'Alım işlemini kaydet':'Satış işlemini kaydet';
}
function updateGoalAccountOptions(){const asset=$('#goal-asset')?.value||'TRY';const el=$('#goal-account');if(!el)return;const old=el.value;const options=state.accounts.filter(a=>a.kind!=='credit_card'&&a.asset_code===asset);el.innerHTML=accountOptions(options,true);if(options.some(a=>String(a.id)===old))el.value=old;}
function updateDebtOpeningAccounts(){const asset=$('#debt-asset')?.value||'TRY';const el=$('#debt-opening-account');if(!el)return;const old=el.value;const choices=state.accounts.filter(a=>a.kind!=='credit_card'&&a.asset_code===asset);el.innerHTML='<option value="">Sadece borç/alacak kaydı oluştur</option>'+accountOptions(choices);if(choices.some(a=>String(a.id)===old))el.value=old;}
function updateDebtPaymentAccounts(){
  const debtId=Number($('#debt-payment-id')?.value);const debt=state.debts.find(d=>d.id===debtId);const asset=debt?.asset_code;const choices=state.accounts.filter(a=>a.kind!=='credit_card'&&(!asset||a.asset_code===asset));const el=$('#debt-payment-account');if(!el)return;const old=el.value;el.innerHTML=accountOptions(choices);if(choices.some(a=>String(a.id)===old))el.value=old;
}
async function loadDebts(){
  state.debts=await api('/api/debts');const open=state.debts.filter(d=>d.outstanding>0);
  $('#debt-list').innerHTML=state.debts.length?state.debts.map(d=>`<div class="schedule-item"><div><div class="schedule-title">${esc(d.name)} ${d.counterparty?`· ${esc(d.counterparty)}`:''}</div><div class="schedule-sub">${d.direction==='payable'?'Ben borçluyum':'Bana borçlular'} · ${esc(d.asset_code)} · ${dateLabel(d.due_date)}</div></div><div class="schedule-side"><div class="schedule-amount ${d.direction==='payable'?'expense':'income'}">${fmtAsset(d.outstanding,d.asset_code)} kaldı</div><div class="schedule-state">Başlangıç: ${fmtAsset(d.original_amount,d.asset_code)}</div></div></div>`).join(''):'<div class="empty-state">Henüz borç veya alacak kaydı yok.</div>';
  $('#debt-payment-id').innerHTML=open.length?open.map(d=>`<option value="${d.id}">${esc(d.name)} · ${fmtAsset(d.outstanding,d.asset_code)} kaldı</option>`).join(''):'<option value="">Açık borç/alacak yok</option>';
  updateDebtPaymentAccounts();
}
async function loadGoals(){
  const goals=await api('/api/goals');$('#goal-list').innerHTML=goals.length?goals.map(g=>`<div class="goal-item"><div class="goal-top"><div><div class="schedule-title">${esc(g.name)}</div><div class="schedule-sub">${g.account_name?`Hesap: ${esc(g.account_name)}`:'Hesap bağlanmamış'} · ${dateLabel(g.target_date)}</div></div><button class="tiny-button danger" data-delete-goal="${g.id}">Sil</button></div><div class="goal-values"><strong>${fmtAsset(g.progress,g.asset_code)}</strong><span>${fmtAsset(g.target_amount,g.asset_code)} hedef</span></div><div class="progress-track"><div class="progress-fill" style="width:${g.progress_percent}%"></div></div><div class="schedule-sub">${fmtNum(g.progress_percent,1)}% tamamlandı · ${fmtAsset(g.remaining,g.asset_code)} kaldı</div></div>`).join(''):'<div class="empty-state">Henüz hedef yok.</div>';
}
async function loadAnalytics(){
  const d=await api(`/api/analytics?month=${state.month}&months=12`);const max=Math.max(1,...d.monthly.flatMap(x=>[x.income,x.expense]));
  $('#monthly-chart').innerHTML=d.monthly.map(x=>`<div class="month-column"><div class="month-bars"><div class="month-bar income" title="Gelir ${fmtTRY(x.income)}" style="height:${Math.max(2,x.income/max*100)}%"></div><div class="month-bar expense" title="Gider ${fmtTRY(x.expense)}" style="height:${Math.max(2,x.expense/max*100)}%"></div></div><small>${esc(monthLabel(x.month))}</small></div>`).join('');
  $('#category-chart').innerHTML=d.categories.length?d.categories.map(c=>`<div class="category-row"><div class="category-row-top"><span>${esc(c.name)}</span><strong>${fmtTRY(c.amount)}</strong></div><div class="progress-track"><div class="category-fill" style="width:${Math.max(1,c.amount/Math.max(1,...d.categories.map(x=>x.amount))*100)}%"></div></div></div>`).join(''):'<div class="empty-state">Bu ay kaydedilmiş gider yok.</div>';
  renderNetWorthChart(d.net_worth_history,'#reports-networth-chart');$('#analytics-note').textContent=d.note;
}
async function loadCategories(){
  const rows=await api('/api/categories');state.meta.categories=rows.map(x=>x.name);updateCategoryOptions();
  $('#category-list').innerHTML=rows.length?rows.map(c=>`<div class="list-row"><div class="row-main"><span class="row-symbol">☷</span><div><div class="row-title">${esc(c.name)}</div><div class="row-sub">${c.kind==='income'?'Gelir':c.kind==='expense'?'Gider':'Gelir ve gider'}</div></div></div><div class="row-actions"><button class="tiny-button" data-edit-category="${c.id}" data-name="${esc(c.name)}" data-kind="${c.kind}">Düzenle</button><button class="tiny-button danger" data-delete-category="${c.id}">Sil</button></div></div>`).join(''):'<div class="empty-state">Kategori yok.</div>';
}
async function refreshAll(){await loadAccounts();await loadDashboard();if(state.page==='transactions')await loadEntries();if(state.page==='plans')await loadPlans();if(state.page==='debts')await loadDebts();if(state.page==='goals')await loadGoals();if(state.page==='reports')await loadAnalytics();if(state.page==='categories')await loadCategories()}

$('#schedule-start').addEventListener('change',e=>{try{if(e.target.value)$('#schedule-day').value=Number(toISODate(e.target.value).slice(-2))}catch{}});
$('#global-month').value=state.month;$('#global-month').addEventListener('change',async e=>{state.month=e.target.value;await loadDashboard();if(state.page==='transactions')await loadEntries();if(state.page==='reports')await loadAnalytics()});
$$('.nav-item').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.page)));$$('[data-go]').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.go)));$$('[data-page-link]').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.pageLink)));
$$('#entry-type-switch button').forEach(b=>b.addEventListener('click',()=>setEntryType(b.dataset.type)));
$$('#trade-type-switch button').forEach(b=>b.addEventListener('click',()=>setTradeType(b.dataset.tradeType)));
$('#trade-asset-account').addEventListener('change',updateTradeCashOptions);
$('#goal-asset').addEventListener('change',updateGoalAccountOptions);
$('#debt-payment-id').addEventListener('change',updateDebtPaymentAccounts);$('#debt-asset').addEventListener('change',updateDebtOpeningAccounts);
$('#entry-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const f=new FormData(form);const payload={entry_type:f.get('entry_type'),entry_date:getDateISO('entry-date'),account_id:Number(f.get('account_id')),amount:Number(f.get('amount')),category:f.get('category'),description:f.get('description')};if(payload.entry_type==='transfer'){payload.target_account_id=Number(f.get('target_account_id'));const t=f.get('target_amount');if(t)payload.target_amount=Number(t)}try{await api('/api/entries',{method:'POST',body:JSON.stringify(payload)});form.reset();$('#entry-date').value=localDate();setEntryType('expense');toast('İşlem kaydedildi.');await refreshAll()}catch(err){toast(err.message)}});
$('#trade-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);const payload={trade_type:f.get('trade_type'),trade_date:getDateISO('trade-date'),asset_account_id:Number(f.get('asset_account_id')),cash_account_id:Number(f.get('cash_account_id')),asset_quantity:Number(f.get('asset_quantity')),cash_amount:Number(f.get('cash_amount')),description:f.get('description')};try{await api('/api/trades',{method:'POST',body:JSON.stringify(payload)});e.currentTarget.reset();$('#trade-date').value=localDate();setTradeType(state.tradeType);updateTradeAccountOptions();toast(payload.trade_type==='buy'?'Varlık alımı kaydedildi.':'Varlık satışı kaydedildi.');await refreshAll()}catch(err){toast(err.message)}});
$('#account-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const f=new FormData(form);const payload={name:f.get('name'),kind:f.get('kind'),asset_code:f.get('asset_code'),opening_balance:Number(f.get('opening_balance')||0),note:f.get('note')};try{await api('/api/accounts',{method:'POST',body:JSON.stringify(payload)});form.reset();toast('Hesap oluşturuldu.');await refreshAll()}catch(err){toast(err.message)}});
$('#schedule-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const f=new FormData(form);const payload={name:f.get('name'),entry_type:f.get('entry_type'),amount:Number(f.get('amount')),account_id:Number(f.get('account_id')),category:f.get('category'),frequency:f.get('frequency'),day_of_month:Number(f.get('day_of_month')||1),start_date:getDateISO('schedule-start'),end_date:getDateISO('schedule-end',true)};try{await api('/api/schedules',{method:'POST',body:JSON.stringify(payload)});form.reset();$('#schedule-start').value=localDate();$('#schedule-day').value=Number($('#schedule-start').value.slice(-2));toast('Tekrarlanan işlem oluşturuldu.');await refreshAll()}catch(err){toast(err.message)}});
$('#installment-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const f=new FormData(form);const payload={name:f.get('name'),total_amount:Number(f.get('total_amount')),account_id:Number(f.get('account_id')),category:f.get('category'),total_installments:Number(f.get('total_installments')),current_installment:Number(f.get('current_installment')),statement_date:getDateISO('installment-date')};try{const res=await api('/api/installments',{method:'POST',body:JSON.stringify(payload)});form.reset();$('#installment-date').value=localDate();$('#installment-no').value=1;$('#installment-count').value=1;toast(`Taksit planı oluşturuldu. ${res.historical_installments_added} geçmiş taksit işlendi, ${payload.total_installments-payload.current_installment} gelecek taksit planlandı.`);await refreshAll()}catch(err){toast(err.message)}});
$('#debt-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);const payload={name:f.get('name'),counterparty:f.get('counterparty'),direction:f.get('direction'),asset_code:f.get('asset_code'),amount:Number(f.get('amount')),account_id:f.get('account_id')?Number(f.get('account_id')):null,due_date:getDateISO('debt-due',true),note:f.get('note')};try{await api('/api/debts',{method:'POST',body:JSON.stringify(payload)});e.currentTarget.reset();updateDebtOpeningAccounts();toast('Borç / alacak kaydedildi.');await refreshAll()}catch(err){toast(err.message)}});
$('#debt-payment-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);const id=Number(f.get('debt_id'));const payload={account_id:Number(f.get('account_id')),amount:Number(f.get('amount')),payment_date:getDateISO('debt-payment-date')};try{await api(`/api/debts/${id}/payment`,{method:'POST',body:JSON.stringify(payload)});e.currentTarget.reset();$('#debt-payment-date').value=localDate();toast('Ödeme / tahsilat kaydedildi.');await refreshAll()}catch(err){toast(err.message)}});
$('#goal-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);const payload={name:f.get('name'),target_amount:Number(f.get('target_amount')),asset_code:f.get('asset_code'),account_id:f.get('account_id')?Number(f.get('account_id')):null,target_date:getDateISO('goal-date',true),note:f.get('note')};try{await api('/api/goals',{method:'POST',body:JSON.stringify(payload)});e.currentTarget.reset();updateGoalAccountOptions();toast('Birikim hedefi oluşturuldu.');await refreshAll()}catch(err){toast(err.message)}});
$('#category-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{await api('/api/categories',{method:'POST',body:JSON.stringify({name:f.get('name'),kind:f.get('kind')})});e.currentTarget.reset();toast('Kategori eklendi.');await loadCategories()}catch(err){toast(err.message)}});
$('#all-entries').addEventListener('click',async e=>{const b=e.target.closest('[data-delete-entry]');if(!b)return;if(!confirm('Bu işlemi silmek istiyor musun?'))return;try{await api(`/api/entries/${b.dataset.deleteEntry}`,{method:'DELETE'});toast('İşlem silindi.');await refreshAll()}catch(err){toast(err.message)}});
$('#all-accounts').addEventListener('click',async e=>{const b=e.target.closest('[data-delete-account]');if(!b)return;if(!confirm('Bu hesabı silmek istiyor musun?'))return;try{await api(`/api/accounts/${b.dataset.deleteAccount}`,{method:'DELETE'});toast('Hesap silindi.');await refreshAll()}catch(err){toast(err.message)}});
document.body.addEventListener('click',async e=>{
  const complete=e.target.closest('[data-complete]');if(complete){const source=complete.dataset.complete==='schedule'?'schedules':'installments';try{await api(`/api/${source}/${complete.dataset.sourceId}/complete`,{method:'POST',body:JSON.stringify({occurrence_key:complete.dataset.key})});toast('İşlem gerçekleşti olarak kaydedildi.');await refreshAll()}catch(err){toast(err.message)}return;}
  const toggle=e.target.closest('[data-toggle-schedule]');if(toggle){try{await api(`/api/schedules/${toggle.dataset.toggleSchedule}/toggle`,{method:'PATCH'});await loadPlans();await loadDashboard()}catch(err){toast(err.message)}return;}
  const delGoal=e.target.closest('[data-delete-goal]');if(delGoal&&confirm('Bu hedef silinsin mi?')){try{await api(`/api/goals/${delGoal.dataset.deleteGoal}`,{method:'DELETE'});await loadGoals();toast('Hedef silindi.')}catch(err){toast(err.message)}return;}
  const editCat=e.target.closest('[data-edit-category]');if(editCat){const name=prompt('Yeni kategori adı:',editCat.dataset.name);if(name===null)return;try{await api(`/api/categories/${editCat.dataset.editCategory}`,{method:'PATCH',body:JSON.stringify({name,kind:editCat.dataset.kind})});await loadCategories();toast('Kategori güncellendi.')}catch(err){toast(err.message)}return;}
  const delCat=e.target.closest('[data-delete-category]');if(delCat&&confirm('Bu kategori silinsin mi?')){try{await api(`/api/categories/${delCat.dataset.deleteCategory}`,{method:'DELETE'});await loadCategories();toast('Kategori silindi.')}catch(err){toast(err.message)}}
});
$('#refresh-btn').addEventListener('click',async()=>{try{const r=await api('/api/quotes/refresh',{method:'POST'});toast(r.ok?`Kurlar güncellendi (${r.updated} fiyat).`:'Kur güncellenemedi; son fiyatlar korundu.');await refreshAll()}catch(err){toast(err.message)}});
$('#backup-btn').addEventListener('click',()=>{window.location.href='/api/backup'});
(function initTheme(){const saved=localStorage.getItem('budget-theme');if(saved==='dark')document.documentElement.dataset.theme='dark';$('#theme-toggle').textContent=saved==='dark'?'☀':'☾';$('#theme-toggle').addEventListener('click',()=>{const dark=document.documentElement.dataset.theme!=='dark';if(dark)document.documentElement.dataset.theme='dark';else delete document.documentElement.dataset.theme;localStorage.setItem('budget-theme',dark?'dark':'light');$('#theme-toggle').textContent=dark?'☀':'☾'});})();
$('#edit-close').addEventListener('click',closeEditDialog);$('#edit-cancel').addEventListener('click',closeEditDialog);
$('#edit-form').addEventListener('submit',async e=>{e.preventDefault();const kind=$('#edit-kind').value;const entryDate=getDateISO('edit-date');if(!entryDate)return;
  try{
    if(kind==='entry'){
      const id=Number($('#edit-entry-id').value);const original=state.entriesById[id];
      const payload={entry_date:entryDate,description:$('#edit-description-input').value,category:$('#edit-category').value};
      if(original.trade_type){payload.trade_asset_quantity=Number($('#edit-trade-quantity').value);payload.trade_cash_amount=Number($('#edit-trade-cash').value)}
      else{payload.amount=Number($('#edit-amount').value);if(original.entry_type==='transfer')payload.target_amount=Number($('#edit-target-amount').value||original.target_amount||original.amount)}
      await api(`/api/entries/${id}`,{method:'PATCH',body:JSON.stringify(payload)});
    }else{
      const source=$('#edit-source-type').value,id=$('#edit-source-id').value,key=$('#edit-occurrence-key').value;
      const payload={due_date:entryDate,amount:Number($('#edit-amount').value),category:$('#edit-category').value,description:$('#edit-description-input').value};
      if(!$('#edit-account-field').classList.contains('hidden'))payload.account_id=Number($('#edit-account').value);
      await api(`/api/occurrences/${source}/${id}/${encodeURIComponent(key)}`,{method:'PATCH',body:JSON.stringify(payload)});
    }
    closeEditDialog();toast('Değişiklikler kaydedildi.');await refreshAll();
  }catch(err){toast(err.message)}
});
(async function init(){const today=localDate();bindDateInputs();['entry-date','schedule-start','installment-date','trade-date','debt-payment-date'].forEach(id=>{const el=$(`#${id}`);if(el)el.value=toTurkishDate(today)});$('#schedule-day').value=Number(today.slice(-2));$('#today-label').textContent=new Intl.DateTimeFormat('tr-TR',{weekday:'long',day:'numeric',month:'long'}).format(new Date()).toLocaleUpperCase('tr-TR');try{await loadMeta();await refreshAll();updateDebtOpeningAccounts();setEntryType('expense');setTradeType('buy')}catch(err){toast(err.message)}})();
