import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s
import csv_import

class CsvImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=s.DB;s.DB=Path(self.tmp.name)/'import.sqlite';s.init(seed_demo=True);self.c=s.connect();self.user=s.one(self.c,"SELECT * FROM users WHERE id='admin'")
    def tearDown(self):self.c.close();s.DB=self.old;self.tmp.cleanup()
    def event(self,dataset,text,**extra):return {'id':s.uid(),'kind':'import','event_time':s.now(),'device':'PHARMACY-01','payload':{'dataset':dataset,'csv':text,'filename':'test.csv'},**extra}
    def execute(self,e):
        with self.c:return s.process(self.c,self.user,e)
    def preview(self,e):return csv_import.execute(self.c,self.user,e,s,preview=True)
    def count(self,t):return self.c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
    def test_preview_has_no_side_effects_and_reports_row_errors(self):
        before={t:self.count(t) for t in ['products','audit','events','imports']}
        r=self.preview(self.event('products','name,sku,barcode,cost,price\nCSV good,CSV-1,987654321,0.25,1.00\nCSV bad,CSV-2,987654322,no,1.00\n'))
        self.assertEqual((r['valid'],r['invalid']),(1,1));self.assertEqual(r['rows'][1]['row'],3)
        self.assertEqual(before,{t:self.count(t) for t in before})
    def test_product_success_replay_and_duplicate_upload(self):
        e=self.event('products','name,sku,barcode,cost,price,rx\nCSV Product,CSV-1,987654321,0.25,1.00,true\n');result=self.execute(e)
        p=s.one(self.c,"SELECT * FROM products WHERE sku='CSV-1'");self.assertEqual(p['cost'],25);self.assertEqual(p['rx'],1)
        self.assertEqual(self.execute(json.loads(json.dumps(e))),result);self.assertEqual(self.count('imports'),1)
        other={**e,'id':s.uid()}
        with self.assertRaisesRegex(ValueError,'already been imported'):self.execute(other)
    def test_invalid_row_rolls_back_entire_import(self):
        e=self.event('products','name,sku,barcode,cost,price\nFirst,CSV-1,987654321,0.25,1.00\nDuplicate,MED-0001,987654322,0.25,1.00\n')
        before={t:self.count(t) for t in ['products','audit','events','imports']}
        with self.assertRaisesRegex(ValueError,'No rows imported'):self.execute(e)
        self.assertEqual(before,{t:self.count(t) for t in before})
        # An independent connection must also see no committed first row.
        with closing(s.connect()) as other:self.assertEqual(other.execute("SELECT COUNT(*) FROM products WHERE sku='CSV-1'").fetchone()[0],0)
    def test_batch_receiving_creates_linked_accounting_and_blocks_duplicates(self):
        expiry=(s.date.today()+s.timedelta(days=365)).isoformat();text=f'sku,number,expiry,quantity,cost,supplier,status\nMED-0001,CSV-LOT,{expiry},25,0.45,MedSupply,available\n'
        before=s.one(self.c,"SELECT SUM(quantity) AS qty FROM batches WHERE product_id='p1'")['qty']
        e=self.event('batches',text);r=self.preview(e);self.assertEqual(r['invalid'],0)
        self.assertEqual(before,s.one(self.c,"SELECT SUM(quantity) AS qty FROM batches WHERE product_id='p1'")['qty'])
        self.execute(e);b=s.one(self.c,"SELECT * FROM batches WHERE number='CSV-LOT'");self.assertEqual(b['quantity'],25)
        d=s.one(self.c,"SELECT * FROM documents WHERE kind='purchase' AND json_extract(payload,'$.import_id')=?",(e['id'],));self.assertEqual(d['total'],1125)
        self.assertEqual(self.c.execute('SELECT SUM(debit-credit) FROM journals WHERE source_id=?',(d['id'],)).fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM movements WHERE batch_id=?',(b['id'],)).fetchone()[0],1)
        r=self.preview(self.event('batches',text.replace('25,','30,')));self.assertEqual(r['invalid'],1)
    def test_expenses_payment_and_cumulative_balance_validation(self):
        opening=s.balance(self.c,'cash-usd');text='description,supplier,category,currency,amount,paid,account_id\nCSV Electricity,ZESA,Utilities,USD,10.00,4.00,cash-usd\n'
        self.execute(self.event('expenses',text));self.assertEqual(s.balance(self.c,'cash-usd'),opening-400)
        d=s.one(self.c,"SELECT * FROM documents WHERE json_extract(payload,'$.description')='CSV Electricity'");self.assertEqual(d['total']-d['paid'],600)
        balance=s.balance(self.c,'cash-usd');large=str(s.Decimal(balance)/100)
        text=f'description,supplier,category,currency,amount,paid,account_id\nFirst large,Test,Other,USD,{large},{large},cash-usd\nSecond large,Test,Other,USD,{large},{large},cash-usd\n'
        with self.assertRaisesRegex(ValueError,'Insufficient account balance'):self.execute(self.event('expenses',text))
        self.assertEqual(s.balance(self.c,'cash-usd'),balance)
    def test_customers_bom_quotes_and_multiline_and_csv_shape(self):
        text='\ufeffname,email,phone\r\n"Care, East\nClinic",care@example.com,+263770000001\r\n'
        self.execute(self.event('customers',text));self.assertEqual(s.one(self.c,'SELECT * FROM customers WHERE email=?',('care@example.com',))['name'],'Care, East\nClinic')
        r=self.preview(self.event('customers','name,email,phone\nBad,x@example.com,123,extra\n'));self.assertEqual(r['invalid'],1)
        for bad in ['name,name\nA,B','wrong\nX','name\n']:
            with self.assertRaises(ValueError):self.preview(self.event('customers',bad))
    def test_asset_import_posts_acquisition_and_rejects_bad_date(self):
        today=s.date.today().isoformat();text=f'description,category,date,cost,currency,account_id,life,residual\nCSV computer,Computers,{today},100.00,USD,bank-usd,5,10.00\n'
        before=s.balance(self.c,'bank-usd');self.execute(self.event('assets',text));self.assertEqual(s.balance(self.c,'bank-usd'),before-10000)
        asset=s.one(self.c,"SELECT * FROM assets WHERE description='CSV computer'");self.assertEqual(asset['cost'],10000)
        r=self.preview(self.event('assets',text.replace(today,'not-a-date')));self.assertEqual(r['invalid'],1)
    def test_permissions_offline_and_limits(self):
        cashier=s.one(self.c,"SELECT * FROM users WHERE id='cashier'");e=self.event('products','name,sku,barcode,cost,price\nP,CSV1,123,1,2')
        with self.assertRaises(PermissionError):csv_import.execute(self.c,cashier,e,s,preview=True)
        with self.assertRaises(PermissionError):self.execute({**e,'offline':True})
        with self.assertRaisesRegex(ValueError,'500'):self.preview(self.event('customers','name\n'+'Care\n'*501))
        with self.assertRaisesRegex(ValueError,'1 MB'):self.preview(self.event('customers','x'*1_000_001))

if __name__=='__main__':unittest.main(verbosity=2)
