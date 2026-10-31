// A4 document presentation only. Amounts, rates and batch allocations come
// from the posted transaction; this layout never posts accounting changes.
const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lines=v=>safe(v).replace(/\r?\n|,\s*/g,'<br>');
const invoiceDate=v=>String(v||'').slice(0,10).replaceAll('-','/');
export function renderInvoice({document:d,state,formatMoney}){
  const decimal=n=>new Intl.NumberFormat('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2}).format((n||0)/100);
  const totalAmount=n=>decimal(n)+' '+(d.currency==='USD'?'$':safe(d.currency));
  const business=state.settings.business,customer=state.customers.find(c=>c.id===d.customer_id)||{};
  const payload=d.payload||{},rate=Number(d.rate),isExpense=['expense','purchase'].includes(d.kind);
  const title={sale:'INVOICE',offline:'PROVISIONAL RECEIPT',refund:'CREDIT NOTE',quote:'QUOTATION',proforma:'PROFORMA INVOICE',draft:'DRAFT',expense:'EXPENSE VOUCHER',purchase:'SUPPLIER INVOICE'}[d.kind]||'INVOICE';
  const original=d.parent_id?state.documents.find(x=>x.id===d.parent_id):null;
  const items=payload.items?.length?payload.items:original?.payload?.items||[];
  const sourceId=original?.id||d.id;
  const allocations=(state.lines||[]).filter(l=>l.document_id===sourceId);
  const itemRows=items.map(item=>{
    const product=state.products.find(p=>p.id===item.product_id)||item.product_snapshot||{};
    const allocation=allocations.find(l=>l.product_id===item.product_id);
    const unit=allocation?.price??item.unit_price??Math.round((product.price||0)/rate);
    return `<tr><td>${safe(product.name||item.product_id)}${product.strength?' '+safe(product.strength):''}</td><td>${safe(item.quantity)}</td><td>${decimal(unit)}</td><td>${decimal(unit*Number(item.quantity))}</td></tr>`;
  });
  if(!itemRows.length)itemRows.push(`<tr><td>${safe(payload.description||payload.reason||(original?'Credit against '+original.number:'Business transaction'))}</td><td>1</td><td>${decimal(d.subtotal)}</td><td>${decimal(d.subtotal)}</td></tr>`);
  const blanks=Array.from({length:Math.max(0,8-itemRows.length)},()=>'<tr class="invoice-blank" aria-hidden="true"><td>&nbsp;</td><td></td><td></td><td>-</td></tr>').join('');
  const settlement=d.kind==='refund'?'Credit / refund issued':d.status==='reversed'?'Reversed by credit note':payload.terms||business.invoice_terms||'Due Upon Receipt';
  const taxableAmount=isExpense?d.total-d.tax:d.subtotal-d.discount;
  const taxRate=Number(payload.tax_rate??original?.payload?.tax_rate??(taxableAmount>0?d.tax*100/taxableAmount:0));
  const taxRateText=new Intl.NumberFormat('de-DE',{minimumFractionDigits:3,maximumFractionDigits:3}).format(taxRate)+'%';
  const totals=[['SUBTOTAL',decimal(d.subtotal)],...(d.discount?[['DISCOUNT',decimal(d.discount)]]:[]),['TAX RATE',taxRateText],['TAX',d.tax?decimal(d.tax):'-'],['TOTAL',totalAmount(d.total)],['PAID',totalAmount(d.paid)],['BALANCE DUE',totalAmount(d.status==='reversed'||d.kind==='refund'?0:d.total-d.paid)]];
  const billedTo=isExpense?d.supplier:customer.name||'Walk-in customer';
  const payments=state.payments.filter(p=>p.document_id===d.id);
  const balance=d.status==='reversed'||d.kind==='refund'?0:d.total-d.paid;
  return `<article class="receipt invoice-sheet">
    <header class="invoice-header">
      <div class="invoice-business"><img class="print-logo" src="/brand-logo-current.png" alt="${safe(business.name)} logo"><h1>${safe(business.name)}</h1><div class="invoice-business-details">${lines(business.address)}${business.tin?'<br>TIN :'+safe(business.tin):''}${business.registration?'<br>'+safe(business.registration):''}${business.phone?'<br>'+safe(business.phone):''}</div></div>
      <div class="invoice-heading"><h2 class="${d.kind==='offline'?'provisional-title':''}">${title}</h2><table class="invoice-metadata"><thead><tr><th>${d.kind==='refund'?'CREDIT NOTE':d.kind==='offline'?'RECEIPT':d.kind==='quote'?'QUOTATION':d.kind==='proforma'?'PROFORMA INVOICE':'INVOICE'} #</th><th>DATE</th></tr></thead><tbody><tr><td>${safe(d.number)}</td><td>${invoiceDate(d.date)}</td></tr></tbody></table></div>
    </header>
    <div class="invoice-party-grid"><section class="invoice-bill"><h3 class="invoice-band">BILL TO</h3><div class="invoice-party-name">${safe(billedTo)}</div>${!isExpense&&customer.address?'<div>'+lines(customer.address)+'</div>':''}${!isExpense&&customer.phone?'<div>'+safe(customer.phone)+'</div>':''}${original?'<div class="invoice-original">Against '+safe(original.number)+'</div>':''}</section><section class="invoice-customer-meta"><table class="invoice-metadata"><thead><tr><th>CUSTOMER ID</th><th>TERMS</th></tr></thead><tbody><tr><td>${isExpense?'—':safe(customer.id||d.customer_id||'—')}</td><td>${settlement}</td></tr></tbody></table></section></div>
    <div class="invoice-contact">${!isExpense&&customer.email?`<a href="mailto:${safe(encodeURIComponent(customer.email))}">${safe(customer.email)}</a>`:business.email?`<a href="mailto:${safe(encodeURIComponent(business.email))}">${safe(business.email)}</a>`:''}<span>${safe(d.currency)} · 1 ${safe(d.currency)} = ${safe(d.rate)} USD</span></div>
    <table class="invoice-items"><colgroup><col style="width:56%"><col style="width:10%"><col style="width:17%"><col style="width:17%"></colgroup><thead><tr><th>DESCRIPTION</th><th>QTY</th><th>UNIT PRICE</th><th>AMOUNT</th></tr></thead><tbody>${itemRows.join('')}${blanks}</tbody></table>
    <div class="invoice-bottom"><div class="invoice-thanks"><strong>Thank you for your business!</strong><p>Please quote ${safe(d.number)} with your payment.</p>${d.due?'<p>Payment due: '+invoiceDate(d.due)+'</p>':''}${d.kind==='offline'?'<p class="invoice-provisional-note">Awaiting synchronization and server validation.<br>Retain this provisional receipt.<br>Transaction: '+safe(d.id)+'</p>':''}${d.status==='reversed'?'<p class="error">Original document retained. Reversed by a credit note.</p>':''}</div><table class="invoice-totals"><tbody>${totals.map(([label,value])=>`<tr class="${label==='TOTAL'||label==='BALANCE DUE'?'invoice-total-emphasis':''}"><th>${label}</th><td>${value}</td></tr>`).join('')}</tbody></table></div>
    ${payments.length||allocations.length?`<div class="invoice-evidence">${payments.length?`<section><h3>PAYMENT HISTORY</h3>${payments.map(p=>`<p>${invoiceDate(p.date)} · ${safe(p.method)} · ${p.direction==='out'?'Refund ':''}${formatMoney(p.amount,p.currency)} · rate ${safe(p.rate)}</p>`).join('')}</section>`:''}${allocations.length?`<section><h3>BATCH TRACEABILITY</h3>${allocations.map(l=>{const b=state.batches.find(b=>b.id===l.batch_id)||{},product=state.products.find(p=>p.id===l.product_id)||{};return `<p>${safe(product.name)} · ${safe(b.number)} · expiry ${invoiceDate(b.expiry)} · ${l.quantity} units</p>`}).join('')}</section>`:''}</div>`:''}
    <p class="invoice-footer">${[business.name,business.email,business.phone].filter(Boolean).map(safe).join(' · ')}</p>
  </article>`;
}
