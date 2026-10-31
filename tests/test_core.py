import json, os, tempfile, unittest, uuid, sys
from pathlib import Path
from datetime import timedelta, datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=s.DB;s.DB=Path(self.tmp.name)/'test.db';s.init(seed_demo=True)
        self.c=s.connect();self.user=s.one(self.c,"SELECT * FROM users WHERE id='admin'")
    def tearDown(self):self.c.close();s.DB=self.old;self.tmp.cleanup()
    def event(self,kind,payload,**extra):return {'id':str(uuid.uuid4()),'kind':kind,'payload':payload,'event_time':s.now(),'device':'PHARMACY-01',**extra}
    def post(self,kind,payload,**extra):
        e=self.event(kind,payload,**extra)
        with self.c:s.process(self.c,self.user,e)
        return e
    def sale(self,paid=False,qty=2,currency='USD'):
        price=s.convert(120,1/float(s.rate_for(self.c,currency)))
        return self.post('sale',{'customer_id':'c1','currency':currency,'items':[{'product_id':'p1','quantity':qty}],'payments':[{'account_id':'cash-usd','amount':str(qty*price/100)}] if paid else []})
    def assertBalanced(self):
        bad=s.rows(self.c,'SELECT source_id,SUM(debit)-SUM(credit) AS diff FROM journals GROUP BY source_id HAVING diff<>0')
        self.assertEqual(bad,[])
    def test_cash_sale_atomic_stock_sales_cogs_cash(self):
        q=s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity'];bal=s.balance(self.c,'cash-usd')
        e=self.sale(True);d=s.one(self.c,'SELECT * FROM documents WHERE id=?',(e['id'],))
        self.assertEqual(s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity'],q-2)
        self.assertEqual(s.balance(self.c,'cash-usd'),bal+240);self.assertEqual(d['paid'],240);self.assertEqual(d['cost'],90)
        self.assertEqual(s.one(self.c,"SELECT credit FROM journals WHERE source_id=? AND account='sales'",(e['id'],))['credit'],240);self.assertBalanced()
    def test_credit_partial_payment(self):
        b=s.balance(self.c,'cash-usd');e=self.sale();self.assertEqual(b,s.balance(self.c,'cash-usd'))
        self.post('receipt',{'document_id':e['id'],'amount':'1.00','account_id':'cash-usd'})
        d=s.one(self.c,'SELECT * FROM documents WHERE id=?',(e['id'],));self.assertEqual(d['total']-d['paid'],140);self.assertEqual(s.balance(self.c,'cash-usd'),b+100);self.assertBalanced()
    def test_refund_quarantine_then_inspection(self):
        b=s.balance(self.c,'cash-usd');q=s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity'];e=self.sale(True)
        self.post('refund',{'document_id':e['id'],'reason':'Customer return; damaged package'})
        self.assertEqual(b,s.balance(self.c,'cash-usd'));self.assertEqual(s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity'],q-2)
        ret=s.one(self.c,"SELECT * FROM batches WHERE number LIKE '%-RETURN'");self.assertEqual(ret['status'],'quarantine')
        self.post('inspect',{'batch_id':ret['id'],'decision':'release','reason':'Sealed packaging inspected by pharmacist'})
        self.assertEqual(s.one(self.c,'SELECT status FROM batches WHERE id=?',(ret['id'],))['status'],'available');self.assertBalanced()
    def test_frozen_currency_rates(self):
        e=self.sale(currency='ZAR');d=s.one(self.c,'SELECT * FROM documents WHERE id=?',(e['id'],));self.assertEqual(d['rate'],'0.056')
        self.post('rate',{'currency':'ZAR','rate':'0.06','effective':s.date.today().isoformat(),'source':'Test'})
        self.assertEqual(s.one(self.c,'SELECT rate FROM documents WHERE id=?',(e['id'],))['rate'],'0.056')
        self.post('receipt',{'document_id':e['id'],'amount':str(d['total']/100),'account_id':'cash-zar'})
        self.assertBalanced()
    def test_expired_quarantined_insufficient_block(self):
        self.c.execute("UPDATE batches SET quantity=0 WHERE id='b1'");self.c.commit()
        before=self.c.execute('SELECT count(*) FROM documents').fetchone()[0]
        with self.assertRaisesRegex(ValueError,'Stock conflict'):self.sale(True)
        self.assertEqual(before,self.c.execute('SELECT count(*) FROM documents').fetchone()[0]);self.assertBalanced()
        self.c.execute("UPDATE batches SET status='quarantine',quantity=10 WHERE id='b1'");self.c.commit()
        with self.assertRaisesRegex(ValueError,'Stock conflict'):self.sale()
    def test_queue_replay_after_restart_exactly_once(self):
        e=self.event('sale',{'customer_id':'c1','currency':'USD','items':[{'product_id':'p1','quantity':2}],'payments':[]},offline=True)
        with self.c:s.process(self.c,self.user,json.loads(json.dumps(e)))
        self.c.close();self.c=s.connect();q=s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity']
        with self.c:s.process(self.c,self.user,json.loads(json.dumps(e)))
        self.assertEqual(q,s.one(self.c,"SELECT quantity FROM batches WHERE id='b1'")['quantity']);self.assertEqual(self.c.execute('SELECT count(*) FROM events WHERE id=?',(e['id'],)).fetchone()[0],1)
    def test_conflict_no_partial_financial_posting(self):
        e=self.event('sale',{'customer_id':'c1','currency':'USD','items':[{'product_id':'p1','quantity':99999}],'payments':[]},offline=True)
        with self.assertRaisesRegex(ValueError,'Stock conflict'):
            with self.c:s.process(self.c,self.user,e)
        for table,field in [('events','id'),('documents','id'),('journals','source_id')]:self.assertEqual(self.c.execute(f'SELECT count(*) FROM {table} WHERE {field}=?',(e['id'],)).fetchone()[0],0)
    def test_snapshot_dashboard_report_reconciliation(self):
        snap=s.snapshot(self.c,self.user);net=sum(j['credit']-j['debit'] for j in snap['journals'] if j['account']=='sales')
        sales=sum(d['base_total']-s.convert(d['tax'],d['rate']) for d in snap['documents'] if d['kind']=='sale')
        self.assertEqual(net,sales)
        for a in snap['accounts']:
            ledger=sum(j['debit']-j['credit'] for j in snap['journals'] if j['account']==a['id']);payments=sum(p['base_amount']*(1 if p['direction']=='in' else -1) for p in snap['payments'] if p['account_id']==a['id']);self.assertEqual(ledger,payments)
        self.assertBalanced()
    def test_overpayment_rolls_back(self):
        e=self.sale();b=s.balance(self.c,'cash-usd')
        with self.assertRaises(ValueError):self.post('receipt',{'document_id':e['id'],'amount':'999.00','account_id':'cash-usd'})
        self.assertEqual(b,s.balance(self.c,'cash-usd'));self.assertBalanced()
    def test_transfer_not_profit(self):
        before=self.c.execute("SELECT COALESCE(SUM(credit-debit),0) FROM journals WHERE account='sales'").fetchone()[0]
        self.post('transfer',{'account_id':'cash-usd','to_account':'bank-usd','amount':10,'received':10})
        self.assertEqual(before,self.c.execute("SELECT COALESCE(SUM(credit-debit),0) FROM journals WHERE account='sales'").fetchone()[0]);self.assertBalanced()
    def test_role_and_rx_enforced(self):
        cashier=s.one(self.c,"SELECT * FROM users WHERE id='cashier'")
        with self.assertRaises(PermissionError):s.process(self.c,cashier,self.event('expense',{'amount':1}))
        with self.assertRaises(PermissionError):
            with self.c:s.process(self.c,cashier,self.event('sale',{'customer_id':'c1','items':[{'product_id':'p2','quantity':1}],'prescription':'RX-123'}))
    def test_backup_restore_integrity(self):
        p=Path(self.tmp.name)/'backup.db';target=s.sqlite3.connect(p);self.c.backup(target);target.close();r=s.sqlite3.connect(p)
        self.assertEqual(r.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(r.execute('SELECT count(*) FROM documents').fetchone()[0],self.c.execute('SELECT count(*) FROM documents').fetchone()[0]);r.close()
    def test_stock_receipt_supplier_payment_and_count(self):
        e=self.post('stock',{'product_id':'p1','supplier':'MedSupply','number':'NEW-001','expiry':(s.date.today()+timedelta(days=120)).isoformat(),'quantity':10,'cost':'0.50','status':'available'})
        self.post('expense_payment',{'document_id':e['id'],'account_id':'bank-usd','amount':'2.00'})
        d=s.one(self.c,'SELECT * FROM documents WHERE id=?',(e['id'],));self.assertEqual(d['total']-d['paid'],300)
        b=s.one(self.c,"SELECT * FROM batches WHERE number='NEW-001'")
        self.post('adjust',{'batch_id':b['id'],'counted':'8','reason':'Two damaged tablets written off'})
        self.assertEqual(s.one(self.c,'SELECT quantity FROM batches WHERE id=?',(b['id'],))['quantity'],8);self.assertBalanced()
    def test_fefo_recall_and_stale_rate(self):
        self.c.execute("UPDATE batches SET expiry=? WHERE id='b1'",((s.date.today()+timedelta(days=200)).isoformat(),));self.c.commit()
        e=self.post('stock',{'product_id':'p1','supplier':'MedSupply','number':'FEFO-1','expiry':(s.date.today()+timedelta(days=20)).isoformat(),'quantity':3,'cost':'0.50','status':'available'})
        sale=self.sale(qty=2);line=s.one(self.c,'SELECT * FROM lines WHERE document_id=?',(sale['id'],));b=s.one(self.c,'SELECT * FROM batches WHERE id=?',(line['batch_id'],));self.assertEqual(b['number'],'FEFO-1')
        self.post('batch_status',{'batch_id':b['id'],'status':'recalled','reason':'Manufacturer quality recall'})
        sale=self.sale(qty=2);line=s.one(self.c,'SELECT * FROM lines WHERE document_id=?',(sale['id'],));self.assertEqual(line['batch_id'],'b1')
        self.c.execute("UPDATE rates SET effective=? WHERE currency='ZAR'",((s.date.today()-timedelta(days=10)).isoformat(),));self.c.commit()
        with self.assertRaisesRegex(ValueError,'stale'):self.sale(currency='ZAR')
    def test_role_snapshot_hides_financial_costs(self):
        cashier=s.one(self.c,"SELECT * FROM users WHERE id='cashier'");snap=s.snapshot(self.c,cashier)
        self.assertNotIn('cost',snap['products'][0]);self.assertNotIn('cost',snap['batches'][0]);self.assertEqual(snap['journals'],[]);self.assertIsNone(snap['accounts'][0]['balance'])
    def test_foreign_partial_rounding_clears_receivable(self):
        e=self.sale(currency='ZAR');d=s.one(self.c,'SELECT * FROM documents WHERE id=?',(e['id'],));ids=[e['id']]
        for amount in [100,100,d['total']-200]:
            receipt=self.post('receipt',{'document_id':e['id'],'amount':str(s.Decimal(amount)/100),'account_id':'cash-zar'});ids.append(receipt['id'])
        entries=s.rows(self.c,"SELECT * FROM journals WHERE account='receivables'")
        self.assertEqual(sum(j['debit']-j['credit'] for j in entries if j['source_id'] in ids),0);self.assertBalanced()
    def test_customer_billing_address_and_frozen_tax_rate(self):
        e=self.post('customer',{'name':'PENDULUM PHARMACEUTICALS','email':'amanda@kedivine.com','address':'BLUFFHILL INDUSTRIAL PARK\nHARARE'})
        customer=s.one(self.c,'SELECT * FROM customers WHERE id=?',(e['id'],));self.assertEqual(customer['address'],'BLUFFHILL INDUSTRIAL PARK\nHARARE')
        self.post('settings',{'tax':15});sale=self.sale();doc=s.one(self.c,'SELECT * FROM documents WHERE id=?',(sale['id'],))
        self.assertEqual(json.loads(doc['payload'])['tax_rate'],15);self.assertEqual(doc['tax'],36)
        self.post('settings',{'tax':0});doc=s.one(self.c,'SELECT * FROM documents WHERE id=?',(sale['id'],))
        self.assertEqual(json.loads(doc['payload'])['tax_rate'],15);self.assertEqual(doc['tax'],36)
    def test_reset_epoch_blocks_old_requests_and_accepts_current_import(self):
        epoch=s.uid();self.c.execute("INSERT INTO settings VALUES('data_epoch',?)",(json.dumps(epoch),));self.c.commit()
        old=self.event('customer',{'name':'Stale offline customer'})
        with self.assertRaisesRegex(ValueError,'workspace was reset'):
            with self.c:s.process(self.c,self.user,old)
        current={**self.event('customer',{'name':'Current customer'}),'data_epoch':epoch}
        with self.c:s.process(self.c,self.user,current)
        self.assertEqual(s.one(self.c,'SELECT name FROM customers WHERE id=?',(current['id'],))['name'],'Current customer')
        event={**self.event('import',{'dataset':'products','filename':'current.csv','csv':'name,sku,barcode,cost,price\nNew Product,EPOCH-1,123450001,1,2\n'}),'data_epoch':epoch}
        with self.c:s.process(self.c,self.user,event)
        self.assertEqual(s.one(self.c,"SELECT name FROM products WHERE sku='EPOCH-1'")['name'],'New Product')

if __name__=='__main__':unittest.main(verbosity=2)
