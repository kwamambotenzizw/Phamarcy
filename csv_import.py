"""Validated CSV imports reuse the operational posting engine, never raw table writes."""
import csv
import hashlib
import io
import json
import sqlite3
import uuid
from datetime import date

SPECS = {
    'products': ('product', ['name','sku','barcode','cost','price'], ['generic','brand','manufacturer','supplier','category','strength','form','pack_size','reorder','location','rx','restricted']),
    'batches': ('stock', ['sku','number','expiry','quantity','cost','supplier'], ['status']),
    'customers': ('customer', ['name'], ['email','phone','address']),
    'expenses': ('expense', ['description','supplier','category','currency','amount'], ['tax','paid','account_id','due','reference']),
    'assets': ('asset', ['description','category','date','cost','currency','account_id','life'], ['residual','location','custodian','condition','notes']),
}

def prepare(c,user,data,engine):
    dataset=data.get('dataset')
    if dataset not in SPECS: raise ValueError('Choose products, batches, customers, expenses or assets')
    kind,required,optional=SPECS[dataset]
    engine.allowed(user,kind)
    text=data.get('csv','')
    if not isinstance(text,str) or len(text.encode('utf-8'))>1_000_000: raise ValueError('CSV limit is 1 MB')
    if '\x00' in text: raise ValueError('CSV contains invalid null characters')
    reader=csv.reader(io.StringIO(text.lstrip('\ufeff'),newline=''),strict=True)
    try:
        headers=next(reader,None)
        if not headers: raise ValueError('CSV is empty. Download a template first.')
        headers=[h.strip().lower() for h in headers]
        if len(headers)!=len(set(headers)) or any(not h for h in headers): raise ValueError('CSV has duplicate or blank column names')
        missing=set(required)-set(headers);unknown=set(headers)-set(required+optional)
        if missing: raise ValueError('Missing columns: '+', '.join(sorted(missing)))
        if unknown: raise ValueError('Unrecognized columns: '+', '.join(sorted(unknown))+'. Use the matching template.')
        records=[]
        for values in reader:
            if not values or all(not v.strip() for v in values): continue
            if len(records)>=500: raise ValueError('Import at most 500 rows per file')
            records.append({'row':reader.line_num,'values':dict(zip(headers,[v.strip() for v in values])),'width_error':len(values)!=len(headers)})
        if not records: raise ValueError('CSV has headers but no data rows')
    except csv.Error as error: raise ValueError('Malformed CSV: '+str(error)) from error
    canonical=json.dumps({'dataset':dataset,'rows':[(r['values'],r['width_error']) for r in records]},sort_keys=True,ensure_ascii=False)
    fingerprint=hashlib.sha256(canonical.encode()).hexdigest()
    if engine.rows(c,'SELECT id FROM imports WHERE fingerprint=?',(fingerprint,)): raise ValueError('This CSV data has already been imported. Existing records were not changed.')
    return kind,required,records,fingerprint

def payload_for(c,record,dataset,required,engine):
    if record['width_error']: raise ValueError('Column count does not match the header; quote fields containing commas')
    p={k:v for k,v in record['values'].items() if v!=''}
    for key in required:
        if not p.get(key): raise ValueError(key+' is required')
    if dataset=='products':
        boolean=p.get('rx','false').lower()
        if boolean not in ['true','false','yes','no','1','0']: raise ValueError('rx must be true or false')
        p['rx']=boolean in ['true','yes','1'];p.setdefault('pack_size',1);p.setdefault('reorder',10)
        p.setdefault('restricted','prescription' if p['rx'] else 'none')
    elif dataset=='batches':
        prod=engine.rows(c,'SELECT id FROM products WHERE sku=?',(p.pop('sku'),))
        if not prod: raise ValueError('Unknown SKU. Import its product first.')
        p['product_id']=prod[0]['id']
        if engine.rows(c,'SELECT id FROM batches WHERE product_id=? AND number=?',(p['product_id'],p['number'])): raise ValueError('Batch number already exists for this product')
    elif dataset=='customers':
        if p.get('email') and ('@' not in p['email'] or ' ' in p['email']): raise ValueError('Invalid customer email')
        if engine.rows(c,"SELECT id FROM customers WHERE lower(name)=lower(?) AND lower(COALESCE(email,''))=lower(?) AND COALESCE(phone,'')=?",(p['name'],p.get('email',''),p.get('phone',''))): raise ValueError('Customer with matching name/contact already exists')
    if dataset in ['expenses','assets']:
        if p['currency'] not in ['USD','ZAR','ZiG']: raise ValueError('currency must be USD, ZAR or ZiG')
        if p.get('due'): date.fromisoformat(p['due'])
        if dataset=='assets':
            acquired=date.fromisoformat(p['date'])
            if acquired>date.today(): raise ValueError('Asset acquisition date cannot be in the future')
        else:
            p.setdefault('paid',0);p.setdefault('tax',0)
            if engine.money(p['paid'])>0 and not p.get('account_id'): raise ValueError('account_id is required when paid is greater than zero')
    return p

def execute(c,user,event,engine,preview=False):
    if event.get('offline'): raise PermissionError('CSV import requires a connection')
    data=event.get('payload',{});dataset=data.get('dataset')
    kind,required,records,fingerprint=prepare(c,user,data,engine)
    namespace=uuid.UUID(event['id']);out=[]
    # Releasing an outermost SQLite savepoint commits it; keep the caller's
    # transaction open until rows, import metadata and inbox result are linked.
    if not c.in_transaction: c.execute('BEGIN')
    c.execute('SAVEPOINT csv_batch')
    try:
        for record in records:
            c.execute('SAVEPOINT csv_row')
            try:
                p=payload_for(c,record,dataset,required,engine);p['import_id']=event['id']
                child={'id':str(uuid.uuid5(namespace,str(record['row']))),'kind':kind,'device':event.get('device',''),'event_time':event.get('event_time',engine.now()),'payload':p,'data_epoch':event.get('data_epoch')}
                engine.process(c,user,child)
                out.append({'row':record['row'],'values':record['values'],'valid':True,'error':''})
            except (ValueError,KeyError,TypeError,ArithmeticError,sqlite3.IntegrityError) as error:
                c.execute('ROLLBACK TO csv_row')
                out.append({'row':record['row'],'values':record['values'],'valid':False,'error':str(error)})
            finally: c.execute('RELEASE csv_row')
        errors=[r for r in out if not r['valid']]
        if preview or errors: c.execute('ROLLBACK TO csv_batch')
    except Exception:
        c.execute('ROLLBACK TO csv_batch');raise
    finally: c.execute('RELEASE csv_batch')
    result={'id':event['id'],'dataset':dataset,'rows':out,'total':len(out),'valid':len(out)-len(errors),'invalid':len(errors)}
    if preview: return result
    if errors: raise ValueError('No rows imported. '+ '; '.join('Row '+str(r['row'])+': '+r['error'] for r in errors[:8]))
    filename=str(data.get('filename','upload.csv'))[:200]
    c.execute('INSERT INTO imports VALUES(?,?,?,?,?,?,?)',(event['id'],fingerprint,dataset,filename,len(out),user['id'],engine.now()))
    engine.audit(c,user['id'],'csv-import',event['id'],{'dataset':dataset,'filename':filename,'rows':len(out),'fingerprint':fingerprint})
    result={'id':event['id'],'message':f'{len(out)} {dataset} imported successfully','count':len(out),'dataset':dataset}
    c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',(event['id'],user['id'],event.get('device',''),'import',event.get('event_time',engine.now()),engine.now(),'synced',json.dumps(event),json.dumps(result)))
    return result
