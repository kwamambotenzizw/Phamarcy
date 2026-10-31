import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=s.DB;s.DB=Path(self.tmp.name)/'registration.sqlite';s.init(seed_demo=True);self.c=s.connect();self.c.execute('DELETE FROM auth_throttle');self.c.commit()
    def tearDown(self):self.c.close();s.DB=self.old;self.tmp.cleanup()
    def details(self,**overrides):return {'name':'New Pharmacy User','email':'new.user@example.com','password':'NewUser!Pass2026','confirm_password':'NewUser!Pass2026',**overrides}
    def create(self,data):
        with self.c:return s.register_user(self.c,data)
    def test_cashier_registration_password_protection_and_audit(self):
        before=self.c.execute('SELECT COUNT(*) FROM documents').fetchone()[0];user=self.create(self.details(email=' NEW.User@EXAMPLE.com ',name='  New   Pharmacy User  '))
        self.assertEqual(user['email'],'new.user@example.com');self.assertEqual(user['role'],'cashier');self.assertEqual(user['name'],'New Pharmacy User')
        row=s.one(self.c,'SELECT * FROM users WHERE id=?',(user['id'],));self.assertEqual(row['active'],1);self.assertNotEqual(row['password_hash'],'NewUser!Pass2026');self.assertEqual(row['password_hash'],s.password('NewUser!Pass2026',row['salt']))
        other=self.create(self.details(email='other.user@example.com'));other=s.one(self.c,'SELECT * FROM users WHERE id=?',(other['id'],));self.assertNotEqual(row['salt'],other['salt']);self.assertNotEqual(row['password_hash'],other['password_hash'])
        entry=s.one(self.c,"SELECT * FROM audit WHERE source_id=? AND action='self-registration'",(user['id'],));self.assertNotIn('NewUser!Pass2026',entry['detail']);self.assertEqual(before,self.c.execute('SELECT COUNT(*) FROM documents').fetchone()[0])
        self.assertEqual(set(user),{'id','name','email','role'})
    def test_invalid_duplicate_and_privileged_role_registration_blocked(self):
        self.create(self.details());count=self.c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        for invalid in [self.details(email='NEW.USER@example.com'),self.details(email='invalid'),self.details(name='X',email='x@example.com'),self.details(password='short',confirm_password='short'),self.details(confirm_password='DifferentPass2026'),self.details(role='admin',email='admin-attempt@example.com'),self.details(password=' '*12,confirm_password=' '*12)]:
            with self.subTest(email=invalid['email']),self.assertRaises((ValueError,PermissionError)):self.create(invalid)
        self.assertEqual(count,self.c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
    def test_registration_can_be_disabled_for_deployment(self):
        with patch.dict(os.environ,{'PHARMA_SELF_REGISTRATION':'0'}):
            with self.assertRaisesRegex(PermissionError,'disabled'):self.create(self.details())
    def test_super_admin_permissions_snapshot_and_backup(self):
        self.c.execute("UPDATE users SET role='super_admin' WHERE id='admin'");self.c.commit()
        user=s.one(self.c,"SELECT * FROM users WHERE id='admin'")
        for operation in ['sale','quote','draft','receipt','expense','expense_payment','transfer','cash','asset','dispose','refund','inspect','stock','adjust','batch_status','product','customer','settings','rate','till','profile']:s.allowed(user,operation)
        snap=s.snapshot(self.c,user);self.assertIn('cost',snap['products'][0]);self.assertTrue(snap['journals']);self.assertIsNotNone(snap['accounts'][0]['balance'])
        with self.assertRaises(PermissionError):self.create(self.details(role='super_admin'))
        http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(http.server_address[1])
        try:
            req=urllib.request.Request(base+'/api/login',data=json.dumps({'email':'admin@medledger.local','password':'PharmaDemo!2026'}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=5) as response:login=json.load(response)
            self.assertEqual(login['state']['user']['role'],'super_admin')
            req=urllib.request.Request(base+'/api/backup',headers={'Authorization':'Bearer '+login['token']})
            with urllib.request.urlopen(req,timeout=5) as response:self.assertTrue(response.read().startswith(b'SQLite format 3'))
        finally:http.shutdown();http.server_close();thread.join(timeout=2)
    def test_five_minute_idle_expiry_and_activity_heartbeat(self):
        http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(http.server_address[1])
        def request(path,data=None,token=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:
                with error:return error.code,json.load(error)
        try:
            status,result=request('/api/login',{'email':'admin@medledger.local','password':'PharmaDemo!2026'});self.assertEqual(status,200);token=result['token'];hashed=s.hashlib.sha256(token.encode()).hexdigest()
            old=(s.datetime.now(s.timezone.utc)-s.timedelta(seconds=240)).isoformat();self.c.execute('UPDATE sessions SET last_activity=? WHERE token=?',(old,hashed));self.c.commit()
            self.assertEqual(request('/api/state',token=token)[0],200)
            self.assertEqual(s.one(self.c,'SELECT last_activity FROM sessions WHERE token=?',(hashed,))['last_activity'],old)
            self.assertEqual(request('/api/activity',{'idle_ms':1000},token)[0],200)
            activity=s.one(self.c,'SELECT last_activity FROM sessions WHERE token=?',(hashed,))['last_activity'];self.assertGreater(activity,old)
            expired=(s.datetime.now(s.timezone.utc)-s.timedelta(seconds=301)).isoformat();self.c.execute('UPDATE sessions SET last_activity=? WHERE token=?',(expired,hashed));self.c.commit()
            status,result=request('/api/state',token=token);self.assertEqual(status,401);self.assertIn('5 minutes',result['error']);self.assertEqual(s.rows(self.c,'SELECT token FROM sessions WHERE token=?',(hashed,)),[])
        finally:http.shutdown();http.server_close();thread.join(timeout=2)
    def test_http_registration_signin_permissions_and_throttling(self):
        http=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(http.server_address[1])
        def request(path,data=None,token=None,origin=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            if origin:headers['Origin']=origin
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:
                with e:return e.code,json.load(e)
        try:
            status,result=request('/api/register',self.details());self.assertEqual(status,201);self.assertEqual(result['user']['role'],'cashier')
            status,login=request('/api/login',{'email':'new.user@example.com','password':'NewUser!Pass2026'});self.assertEqual(status,200);self.assertEqual(login['state']['user']['role'],'cashier');self.assertNotIn('cost',login['state']['products'][0])
            status,result=request('/api/events',{'id':s.uid(),'kind':'settings','device':'PHARMACY-01','event_time':s.now(),'payload':{'tax':99}},token=login['token']);self.assertEqual(status,403)
            status,result=request('/api/register',self.details(email='cross-origin@example.com'),origin='https://untrusted.example');self.assertEqual(status,403)
            self.assertEqual(s.rows(self.c,"SELECT id FROM users WHERE email='cross-origin@example.com'"),[])
            self.c.execute("DELETE FROM auth_throttle WHERE bucket LIKE 'register:%'");self.c.commit()
            for _ in range(5):self.assertEqual(request('/api/register',self.details(email='bad-email'))[0],409)
            status,result=request('/api/register',self.details(email='throttled@example.com'));self.assertEqual(status,429);self.assertIn('Too many',result['error'])
        finally:http.shutdown();http.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main(verbosity=2)
