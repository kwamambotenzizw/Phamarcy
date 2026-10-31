"""Local operator tools. Protect server filesystem access."""
import argparse, getpass, json, secrets, shutil, socket, sqlite3
from pathlib import Path
from contextlib import closing
import server as s

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('bootstrap',help='Create the first non-demo production workspace')
    init.add_argument('--admin-email',required=True);init.add_argument('--admin-name',required=True);init.add_argument('--business-name',required=True)
    init.add_argument('--opening-cash-usd',default='0');init.add_argument('--opening-bank-usd',default='0')
    b=sub.add_parser('backup');b.add_argument('file',type=Path)
    r=sub.add_parser('restore');r.add_argument('file',type=Path);r.add_argument('--confirm',action='store_true')
    u=sub.add_parser('user');u.add_argument('email');u.add_argument('--name',required=True);u.add_argument('--role',choices=['super_admin','admin','manager','pharmacist','cashier','accountant','stock'],required=True)
    r=sub.add_parser('revoke');r.add_argument('email')
    sub.add_parser('check')
    args=parser.parse_args();s.init(seed_demo=False if args.command=='bootstrap' else None)
    if args.command=='bootstrap':
        email=args.admin_email.strip().lower()
        if not s.re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise SystemExit('Enter a valid admin email address')
        name=' '.join(args.admin_name.split());business_name=' '.join(args.business_name.split())
        if not name or not business_name:raise SystemExit('Admin and business names are required')
        plain=getpass.getpass('Initial administrator password (16+ characters): ')
        if len(plain)<16:raise SystemExit('Initial administrator password must contain at least 16 characters')
        if plain!=getpass.getpass('Confirm administrator password: '):raise SystemExit('Passwords do not match')
        with closing(s.connect()) as c,c:
            existing=['settings','users','sessions','products','batches','customers','accounts','rates','documents','lines','payments','movements','journals','events','audit','assets','tills','imports','auth_throttle']
            populated=[table for table in existing if c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()]
            if populated:raise SystemExit('Bootstrap only works on an empty database. Existing data in: '+', '.join(populated))
            business={'name':business_name,'logo':'/brand-logo-current.png','address':'','email':'','phone':'','registration':''}
            policy={'rateAgeDays':7,'discountLimit':0,'expiryDays':90,'offlineDevice':'UNCONFIGURED','offlineHours':1,'offlineEnabled':False,'prescriptionRequired':True}
            for key,value in [('business',business),('tax',0),('policy',policy),('data_epoch',s.uid()),('demo_data_cleared',s.json.dumps({'at':s.now(),'method':'clean-production-bootstrap'}))]:
                c.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,s.json.dumps(value)))
            salt=s.secrets.token_hex(16);admin_id=s.uid()
            c.execute('INSERT INTO users(id,name,email,role,salt,password_hash,active,password_iterations) VALUES(?,?,?,?,?,?,1,?)',(admin_id,name,email,'super_admin',salt,s.password(plain,salt),s.PASSWORD_ITERATIONS))
            c.execute('INSERT INTO customers(id,name,email,phone,address) VALUES(?,?,?,?,?)',('walkin','Walk-in customer','','',''))
            c.execute('INSERT INTO accounts(id,name,currency,kind) VALUES(?,?,?,?)',('cash-usd','Cash drawer · USD','USD','cash'))
            c.execute('INSERT INTO accounts(id,name,currency,kind) VALUES(?,?,?,?)',('bank-usd','Bank account · USD','USD','bank'))
            c.execute('INSERT INTO rates(id,currency,rate,effective,source,created) VALUES(?,?,?,?,?,?)',(s.uid(),'USD','1',s.date.today().isoformat(),'Administrator bootstrap',s.now()))
            for aid,opening in [('cash-usd',args.opening_cash_usd),('bank-usd',args.opening_bank_usd)]:
                cents=s.money(opening)
                if cents<0:raise SystemExit('Opening balances cannot be negative')
                if cents:s.cash(c,'opening-'+aid,{'id':admin_id},{'account_id':aid},cents,'in',offset='equity')
            s.audit(c,admin_id,'production-bootstrap',admin_id,{'email':email,'business':business_name})
        print('Production workspace created. Add products, suppliers, customers, exchange rates, and batches before operations.')
        return
    if args.command=='backup':
        if s.POSTGRES:raise SystemExit('PostgreSQL backups are managed by the connected database provider; use its encrypted backup and point-in-time recovery controls.')
        if args.file.resolve()==s.DB.resolve():raise SystemExit('Backup must use a different destination')
        args.file.parent.mkdir(parents=True,exist_ok=True)
        with closing(s.connect()) as src,closing(sqlite3.connect(args.file)) as dest:src.backup(dest)
        print('Consistent backup written:',args.file.resolve());return
    if args.command=='restore':
        if s.POSTGRES:raise SystemExit('SQLite restore cannot be used with PostgreSQL. Restore through the connected database provider.')
        if not args.confirm:raise SystemExit('Stop the server, preserve a current backup, then repeat with --confirm')
        probe=socket.socket();running=probe.connect_ex(('127.0.0.1',int(s.os.environ.get('PORT',8000))))==0;probe.close()
        if running:raise SystemExit('Stop the server before restoring')
        source=args.file.resolve()
        if source==s.DB.resolve() or not source.is_file():raise SystemExit('Choose an existing backup file different from the live database')
        with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True)) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise SystemExit('Backup failed integrity checks')
            c.execute('SELECT id FROM events LIMIT 1')
            if c.execute('SELECT COALESCE(SUM(debit-credit),0) FROM journals').fetchone()[0]!=0:raise SystemExit('Backup ledger is not balanced')
        recovery=s.DB.with_suffix('.pre-restore.sqlite')
        with closing(s.connect()) as c,closing(sqlite3.connect(recovery)) as dest:c.backup(dest)
        for suffix in ['-wal','-shm']:
            stale=Path(str(s.DB)+suffix)
            if stale.exists():stale.unlink()
        shutil.copy2(source,s.DB);print('Restored. Previous data preserved at',recovery);print('Clear browser caches on every terminal before reconnecting.');return
    with closing(s.connect()) as c,c:
        if s.os.environ.get('PHARMA_ENV')=='production':s.ensure_production_workspace(c)
        if args.command=='user':
            minimum=16 if s.os.environ.get('PHARMA_ENV')=='production' else 12
            p=getpass.getpass(f'New password ({minimum}+ characters): ')
            if len(p)<minimum:raise SystemExit(f'Password must be at least {minimum} characters')
            salt=secrets.token_hex(16);old=s.rows(c,'SELECT * FROM users WHERE email=?',(args.email.lower(),));id=old[0]['id'] if old else s.uid()
            c.execute('INSERT INTO users(id,name,email,role,salt,password_hash,active,password_iterations) VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,role=excluded.role,salt=excluded.salt,password_hash=excluded.password_hash,active=1,password_iterations=excluded.password_iterations',(id,args.name,args.email.lower(),args.role,salt,s.password(p,salt),s.PASSWORD_ITERATIONS))
            c.execute('DELETE FROM sessions WHERE user_id=?',(id,));s.audit(c,'local-operator','user-management',id,{'email':args.email,'role':args.role});print('User saved; existing sessions revoked.')
        elif args.command=='revoke':
            u=s.one(c,'SELECT * FROM users WHERE email=?',(args.email,));c.execute('UPDATE users SET active=0 WHERE id=?',(u['id'],));c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));s.audit(c,'local-operator','revoke-user',u['id'],{});print('Revoked. Offline access ends on reconnect or at the existing local lease expiry.')
        else:
            if s.POSTGRES:
                print('Database: PostgreSQL')
                print('Users:',c.execute('SELECT count(*) FROM users').fetchone()[0])
                print('Unbalanced sources:',len(s.rows(c,'SELECT source_id FROM journals GROUP BY source_id HAVING SUM(debit)<>SUM(credit)')))
            else:
                print('Database: SQLite');print('Integrity:',c.execute('PRAGMA integrity_check').fetchone()[0]);print('Foreign key errors:',len(c.execute('PRAGMA foreign_key_check').fetchall()));print('Unbalanced sources:',len(s.rows(c,'SELECT source_id FROM journals GROUP BY source_id HAVING SUM(debit)<>SUM(credit)')))

if __name__=='__main__':main()
