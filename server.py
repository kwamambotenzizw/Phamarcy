"""PharmaFlow: dependency-free, transactional reference implementation."""
import csv, hashlib, hmac, io, json, os, secrets, sqlite3, threading, uuid, sys, re, smtplib, ssl, tempfile
import csv_import
from email.message import EmailMessage
from http.cookies import SimpleCookie
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from contextlib import closing

ROOT = Path(__file__).parent
DB = Path(os.environ.get('PHARMA_DB', ROOT / 'data' / 'pharma.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
POSTGRES = DATABASE_URL.startswith(('postgres://','postgresql://'))
LOCK = threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
def money(v):
    d=Decimal(str(v))
    if not d.is_finite(): raise ValueError('Amount must be finite')
    return int((d*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def convert(cents, rate): return int((Decimal(cents)*Decimal(str(rate))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def positive(v):
    n=money(v)
    if n<=0: raise ValueError('Amount must be greater than zero')
    return n
def integer(v):
    n=int(v)
    if Decimal(str(v))!=n or n<=0: raise ValueError('Quantity must be a positive whole sales unit')
    return n
PASSWORD_ITERATIONS=600000
def password(p,s,iterations=PASSWORD_ITERATIONS): return hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),iterations).hex()
def connect():
    if POSTGRES:
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError('Install psycopg[binary] to connect to the configured PostgreSQL database') from error
        class HybridRow(dict):
            def __getitem__(self,key):
                if isinstance(key,int):return tuple(self.values())[key]
                return super().__getitem__(key)
        def row_factory(cursor):
            names=[column.name for column in cursor.description]
            def make_row(values):return HybridRow(zip(names,values))
            return make_row
        class PostgresConnection:
            def __init__(self): self.connection=psycopg.connect(DATABASE_URL,connect_timeout=10,row_factory=row_factory)
            def execute(self,sql,parameters=()):
                sql=sql.replace('BEGIN IMMEDIATE','BEGIN').replace('?','%s')
                try:return self.connection.execute(sql,parameters)
                except psycopg.IntegrityError as error:raise sqlite3.IntegrityError(str(error)) from error
            def executescript(self,script):
                for statement in script.split(';'):
                    statement=statement.strip()
                    if statement and not statement.upper().startswith('PRAGMA '):self.execute(statement)
            def commit(self):return self.connection.commit()
            def rollback(self):return self.connection.rollback()
            def close(self):return self.connection.close()
            def __enter__(self):return self
            def __exit__(self,kind,value,traceback):
                if kind:self.rollback()
                else:self.commit()
                return False
        return PostgresConnection()
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=10000'); return c
def rows(c,q,args=()): return [dict(r) for r in c.execute(q,args)]
def one(c,q,args=()):
    r=c.execute(q,args).fetchone()
    if not r: raise ValueError('Record not found')
    return dict(r)
def setting(c,key): return json.loads(one(c,'SELECT value FROM settings WHERE key=?',(key,))['value'])
def workspace_epoch(c):
    row=c.execute("SELECT value FROM settings WHERE key='data_epoch'").fetchone()
    return json.loads(row[0]) if row else 'legacy'
def audit(c,user,action,source,detail): c.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(uid(),user,now(),action,source,json.dumps(detail)))
class RateLimitError(PermissionError): pass
def consume_rate_limit(c,scope,client_ip,limit,window_seconds):
    timestamp=int(datetime.now(timezone.utc).timestamp())
    key=scope+':'+hashlib.sha256(client_ip.encode()).hexdigest()
    c.execute('BEGIN IMMEDIATE')
    if POSTGRES:c.execute('SELECT pg_advisory_xact_lock(hashtext(?))',(key,))
    c.execute('DELETE FROM auth_throttle WHERE window_start<?',(timestamp-86400,))
    row=c.execute('SELECT window_start,attempts FROM auth_throttle WHERE bucket=?',(key,)).fetchone()
    if not row or timestamp-int(row['window_start'])>=window_seconds:
        c.execute('INSERT INTO auth_throttle(bucket,window_start,attempts) VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,attempts=1',(key,timestamp))
        return True
    if int(row['attempts'])>=limit:
        c.commit()
        return False
    c.execute('UPDATE auth_throttle SET attempts=attempts+1 WHERE bucket=?',(key,))
    return True
def journal(c,source,entries,when=None):
    if sum(x[1] for x in entries)!=sum(x[2] for x in entries): raise ValueError('Unbalanced journal')
    for account,debit,credit in entries:
        if debit or credit: c.execute('INSERT INTO journals VALUES(?,?,?,?,?,?)',(uid(),source,when or now(),account,debit,credit))
def rate_for(c,currency,provided=None,event_time=None):
    rates=rows(c,'SELECT * FROM rates WHERE currency=? ORDER BY effective DESC,created DESC',(currency,))
    rates=[r for r in rates if r['effective']<=(event_time or now())[:10] and (not event_time or r['created']<=event_time or r['source']=='Demo manual rate')]
    if not rates: raise ValueError('No approved exchange rate available')
    r=next((x for x in rates if provided and x['id']==provided),rates[0] if not provided else None)
    if not r: raise ValueError('Approved rate was not found')
    age=(date.fromisoformat((event_time or now())[:10])-date.fromisoformat(r['effective'])).days
    if currency!='USD' and age>setting(c,'policy')['rateAgeDays']: raise ValueError('Exchange rate is stale. Manager must approve a new rate online.')
    return r['rate']
def allowed(user,kind):
    roles={'sale':['super_admin','admin','manager','pharmacist','cashier'],'quote':['super_admin','admin','manager','pharmacist','cashier'],'receipt':['super_admin','admin','manager','accountant','cashier'],'expense':['super_admin','admin','manager','accountant'],'expense_payment':['super_admin','admin','manager','accountant'],'transfer':['super_admin','admin','manager','accountant'],'cash':['super_admin','admin','manager','accountant'],'asset':['super_admin','admin','manager','accountant'],'dispose':['super_admin','admin','manager','accountant'],'refund':['super_admin','admin','manager'],'inspect':['super_admin','admin','manager','pharmacist','stock'],'stock':['super_admin','admin','manager','stock'],'adjust':['super_admin','admin','manager','stock'],'batch_status':['super_admin','admin','manager','pharmacist','stock'],'product':['super_admin','admin','manager','stock'],'customer':['super_admin','admin','manager','cashier','accountant'],'settings':['super_admin','admin','manager'],'rate':['super_admin','admin','manager','accountant'],'till':['super_admin','admin','manager','cashier'],'draft':['super_admin','admin','manager','pharmacist','cashier'],'profile':['super_admin','admin','manager','pharmacist','cashier','accountant','stock']}
    if user['role'] not in roles.get(kind,[]): raise PermissionError('Your role cannot perform this action')
def document(c,id,kind,user,p,subtotal,total,cost=0,tax=0,discount=0,parent=None,rate=None):
    currency=p.get('currency','USD'); rate=rate or rate_for(c,currency,p.get('rate_id'),p.get('event_time'))
    num=kind[:3].upper()+'-'+datetime.now().strftime('%y%m')+'-'+id[:8].upper()
    c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,num,kind,p.get('event_time',now()),p.get('customer_id') or 'walkin',p.get('supplier'),currency,rate,subtotal,discount,tax,total,convert(total,rate),0,cost,'posted' if kind not in ['quote','draft'] else kind,user['id'],p.get('due'),p.get('reference'),p.get('category'),parent,json.dumps(p)))
    return num,rate
def account(c,id): return one(c,'SELECT * FROM accounts WHERE id=?',(id,))
def balance(c,id):
    return c.execute("SELECT COALESCE(SUM(CASE direction WHEN 'in' THEN amount ELSE -amount END),0) FROM payments WHERE account_id=?",(id,)).fetchone()[0]
def cash(c,source,user,p,amount,direction,doc=None,offset=None,when=None):
    a=account(c,p['account_id']); r=rate_for(c,a['currency'],p.get('rate_id'),when)
    if direction=='out' and balance(c,a['id'])<amount: raise ValueError('Insufficient account balance')
    b=convert(amount,r)
    c.execute('INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),doc,a['id'],when or now(),amount,a['currency'],r,b,p.get('method',a['kind']),direction,p.get('reference',source)))
    if offset:
        journal(c,source,[(a['id'],b if direction=='in' else 0,b if direction=='out' else 0),(offset,b if direction=='out' else 0,b if direction=='in' else 0)],when)
    return b
def settle(c,source,user,p,docid,direction,when=None):
    d=one(c,'SELECT * FROM documents WHERE id=?',(docid,)); a=account(c,p['account_id']); amount=positive(p['amount'])
    pay_rate=rate_for(c,a['currency'],p.get('rate_id'),when); base=convert(amount,pay_rate)
    current_invoice_rate=d['rate'] if source==docid else rate_for(c,d['currency'],event_time=when)
    applied=amount if a['currency']==d['currency'] else int((Decimal(base)/Decimal(current_invoice_rate)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if p.get('applied') is not None: applied=positive(p['applied'])
    if applied>d['total']-d['paid']: raise ValueError('Payment exceeds outstanding balance; enter tendered cash separately')
    historical=convert(d['paid']+applied,d['rate'])-convert(d['paid'],d['rate']); diff=base-historical
    # An explicit applied amount is supported only within rounding tolerance of FX conversion.
    if p.get('applied') is not None and abs(convert(applied,current_invoice_rate)-base)>2: raise ValueError('Applied amount does not match the approved conversion rate')
    cash(c,source,user,p,amount,direction,docid,when=when)
    if direction=='in': ent=[(a['id'],base,0),('receivables',0,historical),('fx_gain_loss',max(-diff,0),max(diff,0))]
    else: ent=[('payables',historical,0),(a['id'],0,base),('fx_gain_loss',max(diff,0),max(-diff,0))]
    journal(c,source,ent,when)
    c.execute('UPDATE documents SET paid=paid+? WHERE id=?',(applied,docid))
    return applied
def process(c,user,event):
    id=event['id']; uuid.UUID(id)
    stored_event=json.loads(json.dumps(event))
    if event.get('kind')=='profile':
        for key in ['password','current_password']:
            if key in stored_event.get('payload',{}): stored_event['payload'][key]='[REDACTED]'
    old=rows(c,'SELECT * FROM events WHERE id=?',(id,))
    if old:
        if old[0]['user_id']!=user['id'] or json.loads(old[0]['payload'])!=stored_event: raise ValueError('Event ID already belongs to a different request')
        return json.loads(old[0]['result'])
    epoch=workspace_epoch(c)
    if epoch!='legacy' and event.get('data_epoch')!=epoch:
        raise ValueError('This workspace was reset after this request was created. Refresh and review the original offline transaction before recording a new one.')
    kind=event['kind']
    if kind=='import': return csv_import.execute(c,user,event,sys.modules[__name__])
    allowed(user,kind); p=dict(event.get('payload',{})); when=event.get('event_time',now())
    dt=datetime.fromisoformat(when.replace('Z','+00:00'))
    if abs((datetime.now(timezone.utc)-dt).total_seconds())>7*86400: raise ValueError('Offline authorization expired; reconcile with manager')
    if event.get('offline') and os.environ.get('PHARMA_ENV')=='production': raise PermissionError('Offline transactions are disabled until production devices are provisioned.')
    if event.get('offline') and (event.get('device')!=setting(c,'policy')['offlineDevice'] or kind not in ['sale','receipt','expense','draft','quote']): raise PermissionError('Offline action or terminal is not authorized')
    p['event_time']=when; result={'id':id,'message':'Saved successfully'}
    if kind in ['sale','quote','draft']:
        cart=p.get('items',[])
        if not cart: raise ValueError('Add at least one product')
        currency=p.get('currency','USD'); rate=rate_for(c,currency,p.get('rate_id'),when); subtotal=0; cost=0; allocations=[]
        reserved={}
        for item in cart:
            prod=one(c,'SELECT * FROM products WHERE id=?',(item['product_id'],)); qty=integer(item['quantity'])
            price=convert(prod['price'],Decimal(1)/Decimal(rate)); subtotal+=qty*price
            if prod['rx'] and kind=='sale' and (user['role'] not in ['super_admin','pharmacist','manager','admin'] or len(p.get('prescription','').strip())<3): raise PermissionError('Prescription verification requires an authorized pharmacist and reference')
            remaining=qty
            for b in rows(c,"SELECT * FROM batches WHERE product_id=? AND status='available' AND expiry>=? ORDER BY expiry,id",(prod['id'],when[:10])):
                available=b['quantity']-reserved.get(b['id'],0); take=min(available,remaining)
                if take<=0: continue
                allocations.append((prod,b,take,price)); reserved[b['id']]=reserved.get(b['id'],0)+take; cost+=take*b['cost']; remaining-=take
                if not remaining: break
            if remaining: raise ValueError('Stock conflict: insufficient eligible stock for '+prod['name']+'. Review inventory; no financial records were posted.')
        discount=money(p.get('discount',0))
        if discount<0 or discount>subtotal: raise ValueError('Invalid discount')
        if subtotal and discount*100>subtotal*setting(c,'policy')['discountLimit'] and user['role'] not in ['super_admin','admin','manager']: raise PermissionError('Manager approval required for this discount')
        p['tax_rate']=setting(c,'tax')
        tax=convert(subtotal-discount,Decimal(str(p['tax_rate']))/100); total=subtotal-discount+tax
        num,rate=document(c,id,kind,user,p,subtotal,total,cost,tax,discount,rate=rate); result.update(number=num,total=total)
        if kind=='sale':
            if p.get('customer_id','walkin')=='walkin' and not p.get('payments'): raise ValueError('Register a customer for credit sales')
            journal(c,id,[('receivables',convert(total,rate),0),('sales',0,convert(total,rate)-convert(tax,rate)),('tax_payable',0,convert(tax,rate)),('cogs',cost,0),('inventory',0,cost)],when)
            for prod,b,q,price in allocations:
                c.execute('UPDATE batches SET quantity=quantity-? WHERE id=? AND quantity>=?',(q,b['id'],q))
                c.execute('INSERT INTO lines VALUES(?,?,?,?,?,?,?)',(uid(),id,prod['id'],b['id'],q,price,b['cost']))
                c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),prod['id'],b['id'],id,when,-q,'Sale '+num,user['id']))
            for payment in p.get('payments',[]): settle(c,id,user,payment,id,'in',when)
            d=one(c,'SELECT * FROM documents WHERE id=?',(id,))
            if d['customer_id']=='walkin' and d['paid']!=total: raise ValueError('Walk-in sales must be fully paid')
    elif kind in ['receipt','expense_payment']:
        d=one(c,'SELECT * FROM documents WHERE id=?',(p['document_id'],))
        if d['kind'] not in (['sale'] if kind=='receipt' else ['expense','purchase']): raise ValueError('Wrong document type')
        settle(c,id,user,p,d['id'],'in' if kind=='receipt' else 'out',when)
    elif kind=='expense':
        amount=positive(p['amount']); tax=money(p.get('tax',0))
        if tax<0 or tax>amount: raise ValueError('Tax must be within expense amount')
        if not p.get('description') or not p.get('supplier'): raise ValueError('Description and supplier required')
        num,r=document(c,id,kind,user,p,amount,amount,tax=tax)
        journal(c,id,[('expense:'+p.get('category','Other'),convert(amount,r),0),('payables',0,convert(amount,r))],when)
        paid=money(p.get('paid',0))
        if paid<0: raise ValueError('Payment cannot be negative')
        if paid: settle(c,id,user,{'account_id':p['account_id'],'amount':p['paid']},id,'out',when)
        result['number']=num
    elif kind=='transfer':
        a=account(c,p['account_id']); b=account(c,p['to_account']); amount=positive(p['amount'])
        if a['id']==b['id']: raise ValueError('Choose two different accounts')
        base=cash(c,id,user,p,amount,'out',when=when)
        target=positive(p['received']); rb=rate_for(c,b['currency'],event_time=when)
        target_base=cash(c,id,user,{'account_id':b['id']},target,'in',when=when); diff=target_base-base
        journal(c,id,[(b['id'],target_base,0),(a['id'],0,base),('fx_gain_loss',max(-diff,0),max(diff,0))],when)
    elif kind=='cash':
        offsets={'contribution':('equity','in'),'drawing':('equity','out'),'loan':('loan_payable','in'),'repayment':('loan_payable','out')}
        offset,direction=offsets[p['category']]; cash(c,id,user,p,positive(p['amount']),direction,offset=offset,when=when)
    elif kind=='refund':
        d=one(c,"SELECT * FROM documents WHERE id=? AND kind='sale'",(p['document_id'],))
        if rows(c,"SELECT id FROM documents WHERE parent_id=? AND kind='refund'",(d['id'],)): raise ValueError('Sale already reversed')
        if len(p.get('reason','').strip())<4: raise ValueError('Refund reason required')
        pp={**p,'currency':d['currency'],'customer_id':d['customer_id']}; num,r=document(c,id,'refund',user,pp,d['subtotal'],d['total'],d['cost'],d['tax'],d['discount'],d['id'],d['rate'])
        journal(c,id,[('sales',d['base_total']-convert(d['tax'],r),0),('tax_payable',convert(d['tax'],r),0),('receivables',0,d['base_total']),('returned_inventory',d['cost'],0),('cogs',0,d['cost'])],when)
        if d['paid']:
            # Refund exactly the original tender by account and original frozen rates.
            for payment in rows(c,"SELECT * FROM payments WHERE document_id=? AND direction='in'",(d['id'],)):
                if balance(c,payment['account_id'])<payment['amount']: raise ValueError('Insufficient balance for refund')
                c.execute('INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),id,payment['account_id'],when,payment['amount'],payment['currency'],payment['rate'],payment['base_amount'],payment['method'],'out',d['number']))
                hist=convert(d['paid'],r) # FX adjustment consolidated below
                journal(c,id,[('receivables',payment['base_amount'],0),(payment['account_id'],0,payment['base_amount'])],when)
            paidbase=sum(x['base_amount'] for x in rows(c,"SELECT * FROM payments WHERE document_id=? AND direction='in'",(d['id'],)))
            diff=convert(d['paid'],r)-paidbase
            journal(c,id,[('receivables',max(diff,0),max(-diff,0)),('fx_gain_loss',max(-diff,0),max(diff,0))],when)
        for line in rows(c,'SELECT l.*,b.expiry,b.number FROM lines l JOIN batches b ON b.id=l.batch_id WHERE document_id=?',(d['id'],)):
            bid=uid(); c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',(bid,line['product_id'],line['number']+'-RETURN',line['expiry'],line['quantity'],line['quantity'],line['cost'],'quarantine'))
            c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),line['product_id'],bid,id,when,line['quantity'],'Return awaiting inspection',user['id']))
        c.execute("UPDATE documents SET status='reversed' WHERE id=?",(d['id'],)); result['number']=num
    elif kind=='inspect':
        b=one(c,'SELECT * FROM batches WHERE id=?',(p['batch_id'],))
        if b['status']!='quarantine' or not b['number'].endswith('-RETURN'): raise ValueError('Only quarantined customer returns can be inspected here')
        if len(p.get('reason','').strip())<4: raise ValueError('Inspection notes required')
        release=p.get('decision')=='release'
        if release and b['expiry']<when[:10]: raise ValueError('Expired stock cannot be released')
        c.execute('UPDATE batches SET status=?,quantity=? WHERE id=?',('available' if release else 'damaged',b['quantity'] if release else 0,b['id']))
        val=b['quantity']*b['cost']; journal(c,id,[('inventory' if release else 'stock_loss',val,0),('returned_inventory',0,val)],when)
        c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),b['product_id'],b['id'],None,when,0 if release else -b['quantity'],'Inspection: '+p['reason'],user['id']))
    elif kind=='stock':
        prod=one(c,'SELECT * FROM products WHERE id=?',(p['product_id'],)); q=integer(p['quantity']); cost=positive(p['cost']); expiry=p['expiry']; date.fromisoformat(expiry)
        if not p.get('number') or not p.get('supplier'): raise ValueError('Batch and supplier required')
        if expiry<when[:10]: raise ValueError('Cannot receive expired goods')
        bid=uid(); c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',(bid,prod['id'],p['number'],expiry,q,q,cost,p.get('status','available')))
        if p.get('status','available') not in ['available','quarantine','recalled']: raise ValueError('Invalid batch state')
        total=q*cost; p['currency']='USD'; num,r=document(c,id,'purchase',user,p,total,total,rate='1')
        journal(c,id,[('inventory',total,0),('payables',0,total)],when)
        c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),prod['id'],bid,id,when,q,'Goods received '+num,user['id']))
    elif kind in ['adjust','batch_status']:
        b=one(c,'SELECT * FROM batches WHERE id=?',(p['batch_id'],))
        if len(p.get('reason','').strip())<4: raise ValueError('A detailed reason is required')
        if kind=='batch_status':
            if p['status'] not in ['quarantine','recalled','available']: raise ValueError('Invalid status')
            if p['status']=='available' and (b['expiry']<when[:10] or b['number'].endswith('-RETURN')): raise ValueError('Expired or uninspected return cannot be released')
            c.execute('UPDATE batches SET status=? WHERE id=?',(p['status'],b['id']))
            q=0;reason='Batch status '+p['status']+': '+p['reason']
        else:
            if b['number'].endswith('-RETURN') and b['status']=='quarantine': raise ValueError('Inspect returned goods before adjusting them')
            q=int(p['counted'])-b['quantity']
            if int(p['counted'])<0 or str(int(p['counted']))!=str(p['counted']): raise ValueError('Count must be a non-negative whole unit')
            c.execute('UPDATE batches SET quantity=? WHERE id=?',(int(p['counted']),b['id']))
            value=abs(q)*b['cost'];journal(c,id,[('inventory',value if q>0 else 0,value if q<0 else 0),('stock_loss',value if q<0 else 0,value if q>0 else 0)],when)
            reason='Stock count / adjustment: '+p['reason']
        c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),b['product_id'],b['id'],None,when,q,reason,user['id']))
    elif kind=='product':
        if not p.get('name') or not p.get('sku') or not p.get('barcode'): raise ValueError('Product name, SKU and barcode required')
        c.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,p['name'],p.get('generic',''),p.get('brand',''),p.get('manufacturer',''),p.get('supplier',''),p['sku'],p['barcode'],p.get('category','Other'),p.get('strength',''),p.get('form','Tablet'),integer(p.get('pack_size',1)),positive(p['cost']),positive(p['price']),integer(p.get('reorder',10)),p.get('location','Main store'),int(bool(p.get('rx'))),p.get('restricted','none')))
    elif kind=='customer':
        if not p.get('name'): raise ValueError('Customer name required')
        c.execute('INSERT INTO customers(id,name,email,phone,address) VALUES(?,?,?,?,?)',(id,p['name'],p.get('email',''),p.get('phone',''),p.get('address','')))
    elif kind=='asset':
        cost=positive(p['cost']); residual=money(p.get('residual',0)); life=integer(p['life']); r=rate_for(c,p['currency'])
        if residual<0 or residual>cost: raise ValueError('Residual value must be between zero and cost')
        c.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,'AST-'+id[:6].upper(),p['description'],p['category'],p['date'],cost,p['currency'],r,p.get('location',''),p.get('custodian',''),p.get('condition','Good'),life,residual,None,0,p.get('notes','')))
        b=cash(c,id,user,{**p,'amount':p['cost']},cost,'out',when=when)
        if account(c,p['account_id'])['currency']!=p['currency']: raise ValueError('Asset payment account must match currency')
        journal(c,id,[('fixed_assets',b,0),(p['account_id'],0,b)],when)
    elif kind=='dispose':
        a=one(c,'SELECT * FROM assets WHERE id=?',(p['asset_id'],))
        if a['disposed']: raise ValueError('Asset already disposed')
        proceeds=money(p.get('proceeds',0))
        if proceeds<0: raise ValueError('Invalid proceeds')
        if account(c,p['account_id'])['currency']!=a['currency']: raise ValueError('Disposal account currency must match asset')
        nbv=asset_value(a,when[:10])[1]; dep=a['cost']-nbv; bp=convert(proceeds,a['rate']); bc=convert(a['cost'],a['rate']); bn=convert(nbv,a['rate'])
        # Depreciation recognized on disposal; periodic reporting shows an accrual preview.
        journal(c,id,[('depreciation',bc-bn,0),('accumulated_depreciation',0,bc-bn)],when)
        if proceeds: cash(c,id,user,{'account_id':p['account_id']},proceeds,'in',when=when)
        actual=convert(proceeds,rate_for(c,a['currency']))
        journal(c,id,[(p['account_id'],actual,0),('accumulated_depreciation',bc-bn,0),('fixed_assets',0,bc),('asset_gain_loss',max(bn-actual,0),max(actual-bn,0))],when)
        c.execute('UPDATE assets SET disposed=?,proceeds=? WHERE id=?',(when,proceeds,a['id']))
    elif kind=='rate':
        if p['currency'] not in ['USD','ZAR','ZiG'] or p['currency']=='USD': raise ValueError('USD is the fixed base currency')
        r=Decimal(str(p['rate']))
        if not r.is_finite() or r<=0: raise ValueError('Rate must be positive')
        date.fromisoformat(p['effective']); c.execute('INSERT INTO rates VALUES(?,?,?,?,?,?)',(id,p['currency'],str(r),p['effective'],p.get('source','Manager approved'),now()))
    elif kind=='settings':
        for k in ['business','tax','policy']:
            if k in p:
                if k=='tax' and not 0<=Decimal(str(p[k]))<=100: raise ValueError('Tax must be between 0 and 100')
                if k=='business' and (not isinstance(p[k],dict) or not p[k].get('name')): raise ValueError('Business name is required')
                if k=='policy':
                    for field,low,high in [('rateAgeDays',1,30),('discountLimit',0,100),('expiryDays',1,365),('offlineHours',1,24)]:
                        if not low<=int(p[k].get(field,0))<=high: raise ValueError('Invalid policy: '+field)
                    if not str(p[k].get('offlineDevice','')).strip(): raise ValueError('Offline terminal is required')
                c.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(p[k]),k))
    elif kind=='till':
        a=account(c,p['account_id'])
        if a['kind']!='cash': raise ValueError('Select a cash drawer')
        opens=rows(c,'SELECT * FROM tills WHERE account_id=? AND closed IS NULL',(a['id'],))
        if p['action']=='open':
            if opens: raise ValueError('Till is already open')
            c.execute('INSERT INTO tills VALUES(?,?,?,?,?,?,?,?,?)',(id,a['id'],when,None,balance(c,a['id']),None,None,None,user['id']))
        else:
            if not opens: raise ValueError('Open the till first')
            counted=money(p['counted']); expected=balance(c,a['id'])
            if counted<0: raise ValueError('Count cannot be negative')
            c.execute('UPDATE tills SET closed=?,expected=?,counted=?,variance=? WHERE id=?',(when,expected,counted,counted-expected,opens[0]['id']))
    elif kind=='profile':
        if not p.get('name') or '@' not in p.get('email',''): raise ValueError('Name and valid email required')
        c.execute('UPDATE users SET name=?,email=? WHERE id=?',(p['name'],p['email'],user['id']))
        if p.get('password'):
            minimum=16 if os.environ.get('PHARMA_ENV')=='production' else 12
            if len(p['password'])<minimum: raise ValueError(f'Password must contain at least {minimum} characters')
            if not hmac.compare_digest(password(p.get('current_password',''),user['salt'],int(user['password_iterations'])),user['password_hash']): raise ValueError('Current password is incorrect')
            salt=secrets.token_hex(16); c.execute('UPDATE users SET salt=?,password_hash=?,password_iterations=? WHERE id=?',(salt,password(p['password'],salt),PASSWORD_ITERATIONS,user['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    else: raise ValueError('Unsupported action')
    audit(c,user['id'],kind,id,{k:v for k,v in p.items() if 'password' not in k and k!='prescription'})
    c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',(id,user['id'],event.get('device',''),kind,when,now(),'synced',json.dumps(stored_event),json.dumps(result)))
    return result

def asset_value(a,asof):
    months=max(0,(date.fromisoformat(asof)-date.fromisoformat(a['date'])).days)
    dep=min(a['cost']-a['residual'],int(Decimal(a['cost']-a['residual'])*Decimal(months)/Decimal(a['life']*365)))
    return dep,a['cost']-dep
def snapshot(c,user):
    tables=['products','batches','customers','accounts','rates','documents','lines','payments','movements','assets','tills','journals','audit']
    out={t:rows(c,'SELECT * FROM '+t) for t in tables}
    out['event_types']=rows(c,'SELECT id,kind FROM events')
    out['document_meta']=rows(c,'SELECT e.id,e.device,e.event_time,e.received_time,u.name AS user_name FROM events e JOIN documents d ON d.id=e.id JOIN users u ON u.id=e.user_id')
    out['settings']={r['key']:json.loads(r['value']) for r in rows(c,'SELECT * FROM settings')}; out['user']={k:user[k] for k in ['id','name','email','role']}; out['server_time']=now();out['production']=os.environ.get('PHARMA_ENV')=='production';out['database_backend']='postgresql' if POSTGRES else 'sqlite'
    for a in out['accounts']: a['balance']=balance(c,a['id'])
    for a in out['assets']: a['depreciation'],a['nbv']=asset_value(a,date.today().isoformat())
    for d in out['documents']:
        p=json.loads(d['payload']); p.pop('prescription',None); d['payload']=p
    if user['role'] not in ['super_admin','admin','manager','accountant']:
        out['journals']=[];out['audit']=[];out['assets']=[]
        out['documents']=[d for d in out['documents'] if d['kind'] in ['sale','quote','draft','refund']]
        for d in out['documents']: d.pop('cost',None)
        for p in out['products']: p.pop('cost',None)
        for b in out['batches']: b.pop('cost',None)
        for line in out['lines']: line.pop('cost',None)
        out['event_types']=[]
        visible={d['id'] for d in out['documents']}
        out['document_meta']=[m for m in out['document_meta'] if m['id'] in visible]
        out['payments']=[p for p in out['payments'] if p['document_id'] in visible]
        for a in out['accounts']: a['balance']=None
    return out

def init(seed_demo=None):
    if seed_demo is None: seed_demo=os.environ.get('PHARMA_SEED_DEMO')=='1' and os.environ.get('PHARMA_ENV')!='production'
    if seed_demo and os.environ.get('PHARMA_ENV')=='production': raise RuntimeError('Demo seeding is forbidden in production.')
    if not POSTGRES: DB.parent.mkdir(parents=True,exist_ok=True)
    with closing(connect()) as c, c:
        if POSTGRES:c.execute("SELECT pg_advisory_xact_lock(hashtext('pharma-schema-init'))")
        if not POSTGRES:c.execute('PRAGMA journal_mode=WAL')
        c.executescript((ROOT/'schema.sql').read_text())
        def columns(table):
            if POSTGRES:return {row['column_name'] for row in c.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?',(table,)).fetchall()}
            return {row['name'] for row in c.execute('PRAGMA table_info('+table+')').fetchall()}
        if 'last_activity' not in columns('sessions'):
            c.execute('ALTER TABLE sessions ADD COLUMN last_activity TEXT')
            c.execute('UPDATE sessions SET last_activity=? WHERE last_activity IS NULL',(now(),))
        if 'password_iterations' not in columns('users'):
            c.execute('ALTER TABLE users ADD COLUMN password_iterations INTEGER NOT NULL DEFAULT 240000')
        if 'address' not in columns('customers'):
            c.execute("ALTER TABLE customers ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if c.execute("SELECT 1 FROM settings WHERE key='demo_data_cleared'").fetchone(): return
        if c.execute('SELECT count(*) FROM users').fetchone()[0]: return
        if not seed_demo: return
        for key,value in {'business':{'name':'Thachi Investiments','logo':'/brand-logo-current.png','address':'24 Samora Machel Avenue, Harare','email':'accounts@medledger.example','phone':'+263 24 270 4100','registration':'DEMO • REG 2026/0142'},'tax':0,'policy':{'rateAgeDays':7,'discountLimit':10,'expiryDays':90,'offlineDevice':'PHARMACY-01','offlineHours':24,'prescriptionRequired':True}}.items(): c.execute('INSERT INTO settings VALUES(?,?)',(key,json.dumps(value)))
        salt=secrets.token_hex(16)
        c.execute('INSERT INTO users(id,name,email,role,salt,password_hash,active,password_iterations) VALUES(?,?,?,?,?,?,1,?)',('admin','Sarah Mitchell','admin@medledger.local','admin',salt,password('PharmaDemo!2026',salt),PASSWORD_ITERATIONS))
        for role,name in [('cashier','James Banda'),('pharmacist','Dr. Tendai Moyo'),('accountant','Ruth Dube')]:
            salt=secrets.token_hex(16);c.execute('INSERT INTO users(id,name,email,role,salt,password_hash,active,password_iterations) VALUES(?,?,?,?,?,?,1,?)',(role,name,role+'@medledger.local',role,salt,password('PharmaDemo!2026',salt),PASSWORD_ITERATIONS))
        for id,name,currency,kind in [('cash-usd','Main cash drawer','USD','cash'),('bank-usd','CBZ business account','USD','bank'),('mobile-usd','EcoCash merchant','USD','mobile'),('cash-zar','Rand cash drawer','ZAR','cash'),('bank-zig','ZiG business account','ZiG','bank')]: c.execute('INSERT INTO accounts VALUES(?,?,?,?)',(id,name,currency,kind))
        for currency,rate in [('USD','1'),('ZAR','0.056'),('ZiG','0.038')]: c.execute('INSERT INTO rates VALUES(?,?,?,?,?,?)',(uid(),currency,rate,(date.today()-timedelta(days=30) if currency=='USD' else date.today()).isoformat(),'Demo manual rate',now()))
        for id,name,email,phone in [('walkin','Walk-in customer','',''),('c1','Chiedza Medical Centre','billing@chiedza.example','+263 77 234 5678'),('c2','David Moyo','david@example.com','+263 71 432 1980'),('c3','Sunrise Care Clinic','accounts@sunrise.example','+263 78 892 1100')]: c.execute('INSERT INTO customers(id,name,email,phone) VALUES(?,?,?,?)',(id,name,email,phone))
        products=[('Paracetamol','Paracetamol','Panado','Analgesics','500 mg','Tablet',45,120,480,60,False),('Amoxicillin','Amoxicillin','Amoxil','Antibiotics','250 mg','Capsule',95,250,260,50,True),('Vitamin C','Ascorbic acid','Vital Health','Vitamins','1000 mg','Tablet',180,450,320,40,False),('Ibuprofen','Ibuprofen','Brufen','Analgesics','400 mg','Tablet',65,180,28,50,False),('Cetirizine','Cetirizine','Zyrtec','Allergy','10 mg','Tablet',55,150,180,30,False),('Omeprazole','Omeprazole','Losec','Gastrointestinal','20 mg','Capsule',120,320,210,40,False),('Metformin','Metformin','Glucophage','Diabetes','500 mg','Tablet',80,200,90,30,True),('Oral Rehydration Salts','ORS','Hydralyte','First aid','4.1 g','Sachet',70,175,150,25,False),('Salbutamol Inhaler','Salbutamol','Ventolin','Respiratory','100 mcg','Inhaler',420,850,0,15,True),('Digital Thermometer','Thermometer','MediCheck','Equipment','','Unit',380,950,35,10,False)]
        for i,(name,generic,brand,category,strength,form,cost,price,qty,reorder,rx) in enumerate(products):
            id='p'+str(i+1);c.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(id,name,generic,brand,'Aspen Pharmacare','MedSupply Zimbabwe','MED-'+str(i+1).zfill(4),'600100'+str(i+1).zfill(7),category,strength,form,10,cost,price,reorder,'A'+str(i//3+1)+' / Shelf '+str(i%3+1),int(rx),'prescription' if rx else 'none'))
            expiry=(date.today()+timedelta(days=45 if i==3 else 250+i*30)).isoformat();bid='b'+str(i+1)
            c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',(bid,id,'BT-26'+str(i+1).zfill(3),expiry,qty,qty,cost,'available'))
            c.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?)',(uid(),id,bid,None,now(),qty,'Opening stock','admin'))
        c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',('b-expired','p1','BT-EXPIRED',(date.today()-timedelta(days=14)).isoformat(),20,20,45,'available'))
        c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',('b-quarantine','p2','BT-QA',(date.today()+timedelta(days=365)).isoformat(),30,30,95,'quarantine'))
        opening=sum(r[0]*r[1] for r in c.execute('SELECT quantity,cost FROM batches'))
        journal(c,'opening-stock',[('inventory',opening,0),('equity',0,opening)])
        user=one(c,'SELECT * FROM users WHERE id=?',('admin',))
        for aid,amount in [('cash-usd',420),('bank-usd',12400),('mobile-usd',1830),('cash-zar',2200),('bank-zig',18000)]: cash(c,'opening-'+aid,user,{'account_id':aid},money(amount),'in',offset='equity')
        for days in range(13,-1,-1):
            when=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
            p={'customer_id':['walkin','c1','c2','c3'][days%4],'currency':'USD','items':[{'product_id':'p1','quantity':2+days%5},{'product_id':'p3','quantity':1+days%3}], 'payments':[]}
            total=(2+days%5)*120+(1+days%3)*450
            if p['customer_id']=='walkin' or days%3: p['payments']=[{'account_id':'cash-usd' if days%2 else 'mobile-usd','amount':str(Decimal(total)/100)}]
            # Seed dates are permitted independently of offline access validity.
            when=(datetime.now(timezone.utc)-timedelta(days=min(days,6),hours=days)).isoformat()
            process(c,user,{'id':uid(),'kind':'sale','device':'PHARMACY-01','event_time':when,'payload':p})
        for category,amount,supplier in [('Rent',450,'Harare Properties'),('Utilities',86,'ZESA'),('Salaries',320,'Payroll'),('Transport',45,'Swift Logistics')]: process(c,user,{'id':uid(),'kind':'expense','device':'PHARMACY-01','event_time':now(),'payload':{'amount':amount,'paid':amount if category!='Transport' else 0,'description':category+' — October operations','supplier':supplier,'category':category,'currency':'USD','account_id':'bank-usd'}})
        process(c,user,{'id':uid(),'kind':'asset','event_time':now(),'payload':{'description':'Medical vaccine refrigerator','category':'Cold chain','date':(date.today()-timedelta(days=460)).isoformat(),'cost':2400,'residual':240,'life':8,'currency':'USD','account_id':'bank-usd','location':'Dispensary','custodian':'Dr. Tendai Moyo'}})

def ensure_production_workspace(c):
    if not c.execute("SELECT 1 FROM settings WHERE key='demo_data_cleared'").fetchone():
        raise RuntimeError('Production database is not marked as clean. Bootstrap a new workspace or verify and clean the migration source first.')
    if not c.execute('SELECT 1 FROM users WHERE role IN (\'super_admin\',\'admin\') AND active=1 LIMIT 1').fetchone():
        raise RuntimeError('An active administrator account is required before production startup.')
    if c.execute("SELECT 1 FROM users WHERE lower(email) LIKE '%@medledger.local' LIMIT 1").fetchone():
        raise RuntimeError('Demo accounts remain in the production database.')
    if not c.execute("SELECT 1 FROM accounts WHERE currency='USD' LIMIT 1").fetchone():
        raise RuntimeError('Configure a USD account before production startup.')
    if not c.execute("SELECT 1 FROM rates WHERE currency='USD' LIMIT 1").fetchone():
        raise RuntimeError('Configure the fixed USD rate before production startup.')
    if os.environ.get('PHARMA_SELF_REGISTRATION', '0') != '0':
        raise RuntimeError('Public self-registration must be disabled in the production environment.')

def register_user(c,data):
    """Public account creation; privileged roles are never client-selectable."""
    default_registration='0' if os.environ.get('PHARMA_ENV')=='production' else '1'
    if os.environ.get('PHARMA_SELF_REGISTRATION',default_registration)!='1': raise PermissionError('Self-registration is disabled. Contact your administrator.')
    if not isinstance(data,dict): raise ValueError('Invalid registration details')
    for field in ['name','email','password','confirm_password']:
        if not isinstance(data.get(field),str): raise ValueError('Name, email, password and password confirmation are required')
    name=' '.join(data['name'].split());email=data['email'].strip().lower();plain=data['password']
    if not 2<=len(name)<=100: raise ValueError('Name must contain 2 to 100 characters')
    if len(email)>254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email): raise ValueError('Enter a valid email address')
    minimum=16 if os.environ.get('PHARMA_ENV')=='production' else 12
    if not minimum<=len(plain)<=128 or not plain.strip(): raise ValueError(f'Password must contain {minimum} to 128 characters')
    if plain!=data['confirm_password']: raise ValueError('Passwords do not match')
    if data.get('role','cashier')!='cashier': raise PermissionError('Self-registered accounts receive the cashier role. An administrator assigns other roles.')
    if rows(c,'SELECT id FROM users WHERE lower(email)=?',(email,)): raise ValueError('An account already exists for this email. Sign in instead.')
    id=uid();salt=secrets.token_hex(16)
    c.execute('INSERT INTO users(id,name,email,role,salt,password_hash,active,password_iterations) VALUES(?,?,?,?,?,?,1,?)',(id,name,email,'cashier',salt,password(plain,salt),PASSWORD_ITERATIONS))
    audit(c,id,'self-registration',id,{'name':name,'email':email,'role':'cashier'})
    return {'id':id,'name':name,'email':email,'role':'cashier'}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'web'),**kwargs)
    def log_message(self,fmt,*args): pass
    def send_json(self,data,status=200,headers=()):
        body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        for key,value in headers:self.send_header(key,value)
        self.end_headers();self.wfile.write(body)
    def session_token(self):
        if os.environ.get('PHARMA_ENV')=='production':
            cookie=SimpleCookie(self.headers.get('Cookie','')).get('__Host-pharma_session')
            return cookie.value if cookie else ''
        return self.headers.get('Authorization','').removeprefix('Bearer ')
    def user(self,c):
        token=self.session_token()
        token_hash=hashlib.sha256(token.encode()).hexdigest()
        r=rows(c,'SELECT u.*,s.last_activity AS session_activity FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',(token_hash,now()))
        if not r: raise PermissionError('Session expired. Sign in online to continue.')
        last=datetime.fromisoformat(r[0]['session_activity'])
        if (datetime.now(timezone.utc)-last).total_seconds()>=300:
            c.execute('DELETE FROM sessions WHERE token=?',(token_hash,));c.commit()
            raise PermissionError('Session expired after 5 minutes of inactivity. Sign in again.')
        return r[0]
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/state':
            try:
                with closing(connect()) as c: self.send_json(snapshot(c,self.user(c)))
            except PermissionError as e:self.send_json({'error':str(e)},401)
            return
        if path=='/api/backup':
            if POSTGRES:
                self.send_json({'error':'PostgreSQL backups are managed by the connected database provider.'},501);return
            target=None
            try:
                with LOCK,closing(connect()) as c, c:
                    u=self.user(c)
                    if u['role'] not in ['super_admin','admin']:raise PermissionError('Administrator required')
                    audit(c,u['id'],'backup',None,{});c.commit()
                    with tempfile.NamedTemporaryFile(prefix='.pharma-backup-',suffix='.sqlite',dir=DB.parent,delete=False) as tmp: target=Path(tmp.name)
                    dest=sqlite3.connect(target)
                    try:c.backup(dest)
                    finally:dest.close()
                self.send_response(200);self.send_header('Content-Type','application/vnd.sqlite3');self.send_header('Content-Length',str(target.stat().st_size));self.send_header('Content-Disposition','attachment; filename=pharma-backup.sqlite');self.end_headers()
                with target.open('rb') as source:
                    while True:
                        chunk=source.read(1024*1024)
                        if not chunk:break
                        self.wfile.write(chunk)
            except PermissionError as e:self.send_json({'error':str(e)},403)
            finally:
                if target and target.exists():target.unlink()
            return
        if path.startswith('/api/'):self.send_json({'error':'Not found'},404);return
        super().do_GET()
    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if os.environ.get('PHARMA_ENV')=='production' and not origin:raise PermissionError('Origin header is required for production writes')
            if origin and urlparse(origin).netloc!=self.headers.get('Host'):raise PermissionError('Cross-origin writes blocked')
            length=int(self.headers.get('Content-Length',0))
            if length<0:raise ValueError('Invalid content length')
            if length>2_000_000:raise ValueError('Request too large')
            if self.headers.get_content_type()!='application/json':raise ValueError('Requests must use application/json')
            data=json.loads(self.rfile.read(length))
            with LOCK,closing(connect()) as c, c:
                if self.path=='/api/register':
                    if not consume_rate_limit(c,'register',self.client_address[0],5,3600):raise RateLimitError('Too many registration attempts. Try again later or contact your administrator.')
                    c.commit()
                    user=register_user(c,data);c.commit();self.send_json({'user':user,'message':'Account created. Sign in with your new credentials.'},201);return
                if self.path=='/api/login':
                    if not consume_rate_limit(c,'login',self.client_address[0],10,300):raise RateLimitError('Too many attempts. Try again in five minutes.')
                    users=rows(c,'SELECT * FROM users WHERE email=? AND active=1',(data.get('email','').lower(),))
                    if not users:
                        c.commit();raise PermissionError('Invalid email or password')
                    stored_iterations=int(users[0]['password_iterations'])
                    if not hmac.compare_digest(password(data.get('password',''),users[0]['salt'],stored_iterations),users[0]['password_hash']):
                        c.commit();raise PermissionError('Invalid email or password')
                    throttle_key='login:'+hashlib.sha256(self.client_address[0].encode()).hexdigest()
                    c.execute('DELETE FROM auth_throttle WHERE bucket=?',(throttle_key,))
                    if stored_iterations<PASSWORD_ITERATIONS:
                        new_salt=secrets.token_hex(16)
                        c.execute('UPDATE users SET salt=?,password_hash=?,password_iterations=? WHERE id=?',(new_salt,password(data.get('password',''),new_salt),PASSWORD_ITERATIONS,users[0]['id']))
                    token=secrets.token_urlsafe(48);c.execute('INSERT INTO sessions(token,user_id,expires,last_activity) VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),users[0]['id'],(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(),now()))
                    cookie_headers=[('Set-Cookie','__Host-pharma_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=86400')] if os.environ.get('PHARMA_ENV')=='production' else []
                    login_response={'state':snapshot(c,users[0])}
                    if os.environ.get('PHARMA_ENV')!='production':login_response['token']=token
                    self.send_json(login_response,headers=cookie_headers);return
                if self.path=='/api/logout':
                    token=self.session_token()
                    if token:c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
                    cookie_headers=[('Set-Cookie','__Host-pharma_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0')] if os.environ.get('PHARMA_ENV')=='production' else []
                    self.send_json({'ok':True},headers=cookie_headers);return
                user=self.user(c)
                if self.path=='/api/activity':
                    elapsed=max(0,min(300000,int(data.get('idle_ms',0))))
                    activity=datetime.now(timezone.utc)-timedelta(milliseconds=elapsed)
                    previous=datetime.fromisoformat(user['session_activity'])
                    if activity>previous:
                        token_hash=hashlib.sha256(self.session_token().encode()).hexdigest()
                        c.execute('UPDATE sessions SET last_activity=? WHERE token=?',(activity.isoformat(),token_hash))
                    c.commit();self.send_json({'ok':True,'idle_timeout_seconds':300});return
                if self.path=='/api/import/preview':
                    event={'id':uid(),'kind':'import','event_time':now(),'payload':data,'device':'preview','data_epoch':workspace_epoch(c)}
                    self.send_json(csv_import.execute(c,user,event,sys.modules[__name__],preview=True));return
                if self.path=='/api/delete-document':
                    d=one(c,'SELECT * FROM documents WHERE id=?',(data.get('id',''),))
                    if d['kind'] not in ['quote','draft'] or d['status'] not in ['quote','draft']:
                        raise ValueError('Only quotations and saved drafts can be deleted')
                    if d['user_id']!=user['id'] and user['role'] not in ['super_admin','admin','manager']:
                        raise PermissionError('Only the creator or a manager can delete this document')
                    if c.execute('SELECT 1 FROM documents WHERE parent_id=? LIMIT 1',(d['id'],)).fetchone():
                        raise ValueError('This document has linked records and cannot be deleted')
                    audit(c,user['id'],'delete_'+d['kind'],d['id'],{'number':d['number']})
                    c.execute('DELETE FROM documents WHERE id=?',(d['id'],));c.commit()
                    self.send_json({'state':snapshot(c,one(c,'SELECT * FROM users WHERE id=?',(user['id'],))) });return
                if self.path=='/api/email-document':
                    if not consume_rate_limit(c,'email-'+user['id'],user['id'],20,3600):raise RateLimitError('Email sending limit reached. Try again later.')
                    c.commit()
                    host=os.environ.get('PHARMA_SMTP_HOST','smtp.gmail.com')
                    port=int(os.environ.get('PHARMA_SMTP_PORT','587'))
                    sender=os.environ.get('PHARMA_SMTP_USER','nyashamusingafi23@gmail.com').strip()
                    smtp_password=os.environ.get('PHARMA_SMTP_PASSWORD','').replace(' ','')
                    sender_name=os.environ.get('PHARMA_SMTP_FROM_NAME','Thachi Investiments')
                    if not smtp_password: raise ValueError('Email is not configured yet. Set PHARMA_SMTP_PASSWORD to the Gmail App Password, then restart the server.')
                    recipient=data.get('to','').strip()
                    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',recipient): raise ValueError('Enter a valid recipient email address')
                    d=one(c,'SELECT * FROM documents WHERE id=?',(data.get('id',''),))
                    if d['kind'] not in ['sale','quote'] or d['status']=='reversed': raise ValueError('Only active invoices or quotations can be emailed')
                    customer=one(c,'SELECT * FROM customers WHERE id=?',(d['customer_id'],)) if d['customer_id']!='walkin' else {'name':'Walk-in customer'}
                    if os.environ.get('PHARMA_ENV')=='production' and recipient.lower()!=str(customer.get('email','')).strip().lower():
                        raise PermissionError('In production, send only to the email address saved on the customer record.')
                    business=setting(c,'business')
                    title='Invoice' if d['kind']=='sale' else 'Quotation'
                    subject=(data.get('subject') or f'{title} {d["number"]} from {business.get("name",sender_name)}').strip()
                    if not subject or len(subject)>200: raise ValueError('Subject must be between 1 and 200 characters')
                    note=data.get('message','').strip()
                    if len(note)>10000: raise ValueError('Message is too long')
                    amount=lambda cents:f'{cents/100:.2f} {d["currency"]}'
                    body='Hello '+customer.get('name','customer')+',\n\n'+(note+'\n\n' if note else '')+f'{title} {d["number"]}\nDate: {d["date"][:10]}\nTotal: {amount(d["total"])}\nPaid: {amount(d["paid"])}\nBalance due: {amount(max(0,d["total"]-d["paid"]))}\n\n{business.get("name",sender_name)}'
                    if business.get('phone'): body+='\n'+business['phone']
                    if business.get('email'): body+='\n'+business['email']
                    msg=EmailMessage();msg['Subject']=subject;msg['From']=f'{sender_name} <{sender}>';msg['To']=recipient;msg['Reply-To']=sender;msg.set_content(body)
                    context=ssl.create_default_context()
                    with smtplib.SMTP(host,port,timeout=25) as smtp:
                        smtp.ehlo();smtp.starttls(context=context);smtp.ehlo();smtp.login(sender,smtp_password);smtp.send_message(msg)
                    audit(c,user['id'],'email_'+d['kind'],d['id'],{'to':recipient,'subject':subject})
                    c.commit();self.send_json({'ok':True,'to':recipient});return
                if self.path!='/api/events':raise ValueError('Unknown endpoint')
                result=process(c,user,data); c.commit();self.send_json({'result':result,'state':snapshot(c,one(c,'SELECT * FROM users WHERE id=?',(user['id'],)))})
        except RateLimitError as e:self.send_json({'error':str(e)},429)
        except PermissionError as e:self.send_json({'error':str(e)},403)
        except (smtplib.SMTPException,OSError) as e:self.send_json({'error':'Email could not be sent. Check the internet connection and Gmail App Password.'},502)
        except (ValueError,KeyError,sqlite3.IntegrityError,TypeError,ArithmeticError) as e:self.send_json({'error':str(e)},409)
        except Exception:
            self.send_json({'error':'Internal error. No transaction was posted.'},500)

if __name__=='__main__':
    if os.environ.get('PHARMA_ENV')=='production':raise SystemExit('Do not run the development HTTP server in production. Use the Passenger WSGI entry point.')
    init();port=int(os.environ.get('PORT',8000));print(f'Thachi Investiments running at http://localhost:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
