"""One-time, explicit removal of known bundled demo data, with a full backup.

Retains real users/customers and saved quotes; aborts if posted user activity
depends on demo stock/customers. Never runs automatically at application boot.
"""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

def clean(database,backup):
    database=database.resolve();backup=backup.resolve()
    if database==backup:raise ValueError('Backup must be different from the live database')
    if backup.exists():raise ValueError('Choose a new backup filename; existing backups are preserved')
    s.DB=database
    with closing(s.connect()) as c:
        if c.execute("SELECT 1 FROM settings WHERE key='demo_data_cleared'").fetchone():raise ValueError('Demo cleanup was already completed')
        first=s.one(c,"SELECT MIN(received_time) AS time FROM events WHERE kind='sale'")['time']
        if not first:raise ValueError('No recognizable demo seed window was found')
        deadline=(datetime.fromisoformat(first)+timedelta(seconds=5)).isoformat()
        candidates=s.rows(c,'SELECT e.id,e.kind,d.payload,d.supplier,d.category FROM events e LEFT JOIN documents d ON d.id=e.id WHERE e.received_time>=? AND e.received_time<=?',(first,deadline))
        seed=[];assets=[]
        expenses={('Harare Properties','Rent'),('ZESA','Utilities'),('Payroll','Salaries'),('Swift Logistics','Transport')}
        for event in candidates:
            if event['kind']=='sale':
                p=json.loads(event['payload']);items=p.get('items',[])
                if len(items)==2 and [i['product_id'] for i in items]==['p1','p3'] and p.get('currency')=='USD':seed.append(event['id'])
            elif event['kind']=='expense' and (event['supplier'],event['category']) in expenses:seed.append(event['id'])
            elif event['kind']=='asset':
                a=s.rows(c,"SELECT id FROM assets WHERE id=? AND description='Medical vaccine refrigerator' AND cost=240000",(event['id'],))
                if a:assets.append(event['id'])
        if len(seed)!=18 or len(assets)!=1:raise ValueError('Bundled seed records did not match the expected 14 sales, 4 expenses and 1 asset; no changes made')
        products=s.rows(c,"SELECT * FROM products WHERE id IN ('p1','p2','p3','p4','p5','p6','p7','p8','p9','p10') AND sku GLOB 'MED-000[1-9]' OR (id='p10' AND sku='MED-0010')")
        if len(products)!=10:raise ValueError('Seed product identities have changed; no changes made')
        ids=seed+assets;marks=','.join('?' for _ in ids)
        c.execute('CREATE TEMP TABLE cleanup_sources(id TEXT PRIMARY KEY)');c.executemany('INSERT INTO cleanup_sources VALUES(?)',[(i,) for i in ids])
        c.execute('CREATE TEMP TABLE cleanup_products(id TEXT PRIMARY KEY)');c.executemany('INSERT INTO cleanup_products VALUES(?)',[(p['id'],) for p in products])
        if c.execute('SELECT COUNT(*) FROM lines WHERE product_id IN (SELECT id FROM cleanup_products) AND document_id NOT IN (SELECT id FROM cleanup_sources)').fetchone()[0]:raise ValueError('User-posted stock transactions reference demo products; manual reconciliation is required')
        if c.execute("SELECT COUNT(*) FROM documents WHERE customer_id IN ('c1','c2','c3') AND id NOT IN (SELECT id FROM cleanup_sources)").fetchone()[0]:raise ValueError('A saved user document references a demo customer; manual review is required')
        if c.execute("SELECT COUNT(*) FROM events WHERE user_id IN ('cashier','pharmacist','accountant') AND id NOT IN (SELECT id FROM cleanup_sources)").fetchone()[0]:raise ValueError('A demo staff account has user-created activity; manual review is required')
        if c.execute('SELECT COUNT(*) FROM journals WHERE source_id NOT IN (SELECT id FROM cleanup_sources) AND source_id NOT LIKE ? AND source_id<>?',('opening-%','opening-stock')).fetchone()[0]:raise ValueError('Non-demo accounting entries exist; manual reconciliation is required')
        c.commit()  # Only temporary selection tables have been written so far.
        backup.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(backup)) as target:c.backup(target)
        c.execute('PRAGMA secure_delete=ON')
        before={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['products','batches','customers','documents','assets','payments','journals','events','users']}
        preserved=[]
        with c:
            for d in s.rows(c,"SELECT * FROM documents WHERE kind IN ('quote','draft') AND id NOT IN (SELECT id FROM cleanup_sources)"):
                p=json.loads(d['payload'])
                for item in p.get('items',[]):
                    matches=[prod for prod in products if prod['id']==item['product_id']]
                    if matches:
                        prod=matches[0];item['product_snapshot']={k:prod[k] for k in ['id','name','generic','brand','sku','barcode','strength','form','price']}
                        item['unit_price']=s.convert(prod['price'],Decimal(1)/Decimal(d['rate']))
                subtotal=sum(i['unit_price']*int(i['quantity']) for i in p.get('items',[]))
                if subtotal!=d['subtotal']:raise ValueError('Saved quotation prices do not match its original subtotal')
                c.execute('UPDATE documents SET payload=? WHERE id=?',(json.dumps(p),d['id']));preserved.append(d['number'])
            c.execute('DELETE FROM payments WHERE document_id IN (SELECT id FROM cleanup_sources) OR reference IN (SELECT id FROM cleanup_sources) OR reference LIKE ?',('opening-%',))
            c.execute('DELETE FROM journals WHERE source_id IN (SELECT id FROM cleanup_sources) OR source_id LIKE ? OR source_id=?',('opening-%','opening-stock'))
            c.execute('DELETE FROM movements WHERE product_id IN (SELECT id FROM cleanup_products) OR document_id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM lines WHERE document_id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM documents WHERE id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM assets WHERE id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM batches WHERE product_id IN (SELECT id FROM cleanup_products)')
            c.execute('DELETE FROM products WHERE id IN (SELECT id FROM cleanup_products)')
            c.execute("DELETE FROM customers WHERE id IN ('c1','c2','c3')")
            c.execute('DELETE FROM audit WHERE source_id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM events WHERE id IN (SELECT id FROM cleanup_sources)')
            c.execute('DELETE FROM sessions')
            c.execute("DELETE FROM users WHERE id IN ('cashier','pharmacist','accountant')")
            c.execute("UPDATE users SET name='Administrator' WHERE id='admin' AND name='Sarah Mitchell'")
            c.execute("DELETE FROM rates WHERE source='Demo manual rate' AND currency<>'USD'")
            for key,value in [('data_epoch',s.uid()),('demo_data_cleared',s.now())]:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value)))
            after={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before}
            s.audit(c,'admin','explicit-demo-data-cleanup',None,{'backup':str(backup),'before':before,'after':after,'preserved_quotes':preserved})
            if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign-key check failed')
            if c.execute('SELECT COALESCE(SUM(debit-credit),0) FROM journals').fetchone()[0]!=0:raise ValueError('Ledger check failed')
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
        result={'backup':str(backup),'before':before,'after':after,'preserved_quotes':preserved,'integrity':'ok'}
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',type=Path,default=s.DB);parser.add_argument('--backup',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(clean(args.database,args.backup),indent=2))
