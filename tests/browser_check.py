"""Chrome DevTools smoke test using only Python's standard library."""
import base64, hashlib, json, os, socket, struct, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CHROME=Path(os.environ.get('CHROME',r'C:\Program Files\Google\Chrome\Application\chrome.exe'))
class CDP:
    def __init__(self,url):
        from urllib.parse import urlparse
        u=urlparse(url);self.s=socket.create_connection((u.hostname,u.port));self.s.settimeout(15);key=base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f'GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode());header=b''
        while not header.endswith(b'\r\n\r\n'):header+=self.s.recv(1)
        if b'101' not in header:raise RuntimeError(header)
        self.n=0;self.errors=[]
    def read(self,n):
        out=b''
        while len(out)<n:out+=self.s.recv(n-len(out))
        return out
    def call(self,method,params={}):
        self.n+=1;body=json.dumps({'id':self.n,'method':method,'params':params}).encode();mask=os.urandom(4);n=len(body)
        header=bytes([0x81,0x80|n]) if n<126 else bytes([0x81,0x80|126])+struct.pack('!H',n) if n<65536 else bytes([0x81,0x80|127])+struct.pack('!Q',n)
        self.s.sendall(header+mask+bytes(c^mask[i%4] for i,c in enumerate(body)))
        while True:
            a,b=self.read(2);length=b&127
            if length==126:length=struct.unpack('!H',self.read(2))[0]
            if length==127:length=struct.unpack('!Q',self.read(8))[0]
            data=self.read(length)
            if a&15==8:raise RuntimeError('Browser disconnected')
            msg=json.loads(data)
            if msg.get('method')=='Runtime.exceptionThrown':self.errors.append(msg)
            if msg.get('id')==self.n:
                if 'error'in msg:raise RuntimeError(msg['error'])
                return msg.get('result',{})
    def js(self,expression):
        r=self.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
        if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
        return r.get('result',{}).get('value')
    def wait(self,expression,seconds=15):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if self.js('Boolean('+expression+')'):return
            time.sleep(.2)
        raise AssertionError('Timed out: '+expression)

def run():
    if not CHROME.exists():raise RuntimeError('Set CHROME to a Chromium executable')
    artifacts=ROOT/'artifacts';artifacts.mkdir(exist_ok=True)
    sock=socket.socket();sock.bind(('127.0.0.1',0));debug_port=sock.getsockname()[1];sock.close()
    sock=socket.socket();sock.bind(('127.0.0.1',0));app_port=sock.getsockname()[1];sock.close()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        env={**os.environ,'PORT':str(app_port),'PHARMA_DB':str(Path(temp)/'test.sqlite'),'PHARMA_SEED_DEMO':'1'}
        server=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        browser=None
        try:
            for _ in range(50):
                try:urllib.request.urlopen(f'http://localhost:{app_port}',timeout=1);break
                except Exception:time.sleep(.2)
            browser=subprocess.Popen([str(CHROME),'--headless=new','--no-sandbox','--no-first-run','--no-default-browser-check','--remote-debugging-port='+str(debug_port),'--remote-allow-origins=*','--disable-gpu','--user-data-dir='+str(Path(temp)/'chrome'),f'http://localhost:{app_port}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for _ in range(20):
                try:
                    targets=json.load(urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json',timeout=1));target=next(t for t in targets if t['type']=='page');break
                except Exception:time.sleep(.2)
            if 'target' not in locals():raise RuntimeError('Chrome debug port unavailable')
            c=CDP(target['webSocketDebuggerUrl']);c.call('Runtime.enable');c.call('Network.enable');c.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
            c.wait("!!document.querySelector('#login-form')")
            c.js("document.querySelector('#open-registration').click()")
            c.wait("!!document.querySelector('#registration-form')")
            c.js("document.querySelector('#registration-form [name=name]').value='Browser New Cashier';document.querySelector('#registration-form [name=email]').value='browser.signup@example.com';document.querySelector('#registration-form [name=password]').value='BrowserSignup!2026';document.querySelector('#registration-form [name=confirm_password]').value='DifferentSignup!2026';document.querySelector('#registration-form button[type=submit]').click()")
            c.wait("document.querySelector('#registration-error').textContent.includes('do not match')")
            c.js("document.querySelector('#registration-form [name=confirm_password]').value='BrowserSignup!2026';document.querySelector('#registration-form button[type=submit]').click()")
            c.wait("!document.querySelector('#modal').open")
            assert c.js("document.querySelector('#login-form [name=email]').value==='browser.signup@example.com'")
            c.js("document.querySelector('#login-form [name=password]').value='BrowserSignup!2026';document.querySelector('#login-form button[type=submit]').click()")
            c.wait("!!document.querySelector('.workspace')")
            assert c.js("document.querySelector('#main').textContent.includes('Financial access requires')")
            assert c.js("document.querySelector('.profile-name').textContent.includes('cashier')")
            c.js("document.querySelector('[data-action=logout]').click()")
            c.wait("!!document.querySelector('#confirmation-accept')")
            c.js("document.querySelector('#confirmation-accept').click()")
            c.wait("!!document.querySelector('#login-form')")
            print('PASS self-registration, password-match validation, new-user sign-in, cashier permissions and confirmed sign-out')
            c.js("document.querySelector('#login-form [name=email]').value='admin@medledger.local';document.querySelector('#login-form [name=password]').value='PharmaDemo!2026';document.querySelector('#login-form button[type=submit]').click()")
            try:c.wait("!!document.querySelector('.dashboard-grid')")
            except Exception:
                print(c.js("document.body.innerText"),c.errors,flush=True);raise
            c.wait("!!navigator.serviceWorker.controller")
            (artifacts/'dashboard.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})['data']))
            print('PASS sign-in and dashboard rendering')
            assert c.js("document.querySelector('.reference-sidebar .company-brand').textContent.trim()===''")
            print('PASS duplicate company-name label removed below sidebar logo')
            for route in ['sales','cash','assets','expenses','inventory','profile','settings','reports']:
                c.js(f"location.hash='{route}'")
                c.wait(f"document.querySelector('h1') && document.querySelector('#main') && location.hash==='#{route}'")
                time.sleep(.2)
                assert c.js("document.querySelector('#main').textContent.length")>100
                print('PASS navigation '+route)
            c.js("location.hash='sales'");time.sleep(.2)
            c.wait("!!document.querySelector('[data-invoice-view]')")
            assert c.js("!!document.querySelector('[data-invoice-view] svg')")
            c.js("[...document.querySelectorAll('#main tbody tr')].find(r=>r.textContent.includes('Chiedza')).querySelector('[data-invoice-view]').click()")
            c.wait("!!document.querySelector('.invoice-details-page')")
            assert c.js("location.hash.startsWith('#sales/invoice/')")
            assert c.js("document.querySelector('.invoice-details-page').textContent.includes('CUSTOMER INFORMATION')")
            assert c.js("document.querySelector('.invoice-details-page').textContent.includes('BATCH TRACEABILITY')")
            c.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1200,'deviceScaleFactor':1,'mobile':False})
            (artifacts/'invoice-details.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})['data']))
            c.call('Page.reload');c.wait("!!document.querySelector('.invoice-details-page')")
            assert c.js("document.querySelector('.invoice-details-page').textContent.includes('PHARMACY-01')")
            c.js("document.querySelector('[data-invoice-pdf]').click()")
            c.wait("!!document.querySelector('.invoice-sheet')")
            c.js("document.querySelector('#modal').close();document.querySelector('[data-invoice-delivery]').click()")
            c.wait("!!document.querySelector('.delivery-sheet')")
            assert not c.js("document.querySelector('.delivery-sheet').textContent.includes('UNIT PRICE')")
            c.js("document.querySelector('#modal').close();document.querySelector('[data-invoice-customer]').click()")
            c.wait("document.querySelector('#modal').textContent.includes('Customer statement')")
            c.js("document.querySelector('#modal').close()")
            c.js("document.querySelector('[data-invoice-credit]').click();document.querySelector('#entry-form [name=reason]').value='Confirmation cancellation test';document.querySelector('#entry-form button[type=submit]').click()")
            c.wait("!!document.querySelector('#confirm-dialog')")
            assert c.js("document.querySelector('#modal').open")
            c.js("document.querySelector('#confirmation-cancel').click()")
            c.wait("!document.querySelector('#confirm-dialog')")
            assert c.js("document.querySelector('#entry-form [name=reason]').value==='Confirmation cancellation test'")
            c.js("document.querySelector('#modal').close()")
            print('PASS refund confirmation cancellation preserves form data without posting')
            c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert c.js('document.documentElement.scrollWidth<=innerWidth')
            c.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
            c.js("document.querySelector('[data-invoice-back]').click()")
            c.wait("!!document.querySelector('[data-invoice-view]')")
            print('PASS invoice eye action, detail page, reloadable link, PDF preview, delivery note, customer statement and mobile layout')
            # Exercise actual file selection, server preview and atomic commit.
            c.js("location.hash='inventory'");time.sleep(.2)
            c.js("document.querySelector('[data-import=products]').click()")
            c.wait("!!document.querySelector('#csv-file')")
            def upload_csv(name,contents):
                path=Path(temp)/name;path.write_text(contents,encoding='utf-8')
                root=c.call('DOM.getDocument')['root']['nodeId']
                node=c.call('DOM.querySelector',{'nodeId':root,'selector':'#csv-file'})['nodeId']
                c.call('DOM.setFileInputFiles',{'nodeId':node,'files':[str(path)]})
                c.wait("!document.querySelector('#csv-validate').disabled")
                c.js("document.querySelector('#csv-validate').click()")
                c.wait("!!document.querySelector('.csv-summary')")
            upload_csv('products.csv','name,sku,barcode,cost,price,rx\nCSV Browser Product,CSV-BROWSER,987650000001,0.25,1.20,false\n')
            c.wait("!document.querySelector('#csv-confirm').disabled")
            assert not c.js("document.querySelector('#main').textContent.includes('CSV Browser Product')")
            c.js("document.querySelector('#csv-confirm').click()")
            c.wait("!document.querySelector('#modal').open")
            c.wait("document.querySelector('#main').textContent.includes('CSV Browser Product')")
            print('PASS CSV product upload, side-effect-free preview and persisted import')
            c.js("document.querySelector('[data-action=receive]').click();document.querySelector('[data-form-csv=batches]').click()")
            c.wait("document.querySelector('#csv-dataset').value==='batches'")
            from datetime import date,timedelta
            expiry=(date.today()+timedelta(days=365)).isoformat()
            upload_csv('batches.csv',f'sku,number,expiry,quantity,cost,supplier\nCSV-BROWSER,CSV-BROWSER-LOT,{expiry},7,0.25,CSV Supplier\n')
            c.wait("!document.querySelector('#csv-confirm').disabled")
            c.js("document.querySelector('#csv-confirm').click()")
            c.wait("!document.querySelector('#modal').open")
            assert c.js("[...document.querySelectorAll('#main tbody tr')].find(r=>r.textContent.includes('CSV Browser Product')).textContent.includes('7')")
            print('PASS CSV batch receiving from the add-stock form')
            c.js("document.querySelector('[data-import=products]').click()")
            upload_csv('invalid-products.csv','name,sku,barcode,cost,price\nBad CSV Product,CSV-BAD,987650000002,invalid,1.00\n')
            assert c.js("document.querySelector('#csv-confirm').disabled")
            assert c.js("!!document.querySelector('#csv-errors')")
            c.js("document.querySelector('#csv-cancel').click()")
            print('PASS invalid CSV rows block import and offer error export')
            c.js("location.hash='sales'");time.sleep(.2);c.js("document.querySelector('[data-action=pos]').click()")
            c.wait("!!document.querySelector('[data-add]')")
            c.js("document.querySelector('[data-add=p1]').click();document.querySelector('[data-add=p3]').click()")
            assert c.js("getComputedStyle(document.querySelector('[data-remove] svg')).stroke==='rgb(185, 28, 28)'")
            c.js("document.querySelector('[data-remove=\"1\"]').click()")
            c.wait("!!document.querySelector('#confirm-dialog')")
            assert c.js("document.activeElement.id==='confirmation-cancel'")
            (artifacts/'delete-confirmation.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png'})['data']))
            c.js("document.querySelector('#confirmation-cancel').click()")
            c.wait("!document.querySelector('#confirm-dialog')")
            assert c.js("document.querySelectorAll('.cart-row').length===2")
            c.js("document.querySelector('[data-remove=\"1\"]').click()")
            c.wait("!!document.querySelector('#confirmation-accept')")
            c.js("document.querySelector('#confirmation-accept').click()")
            c.wait("document.querySelectorAll('.cart-row').length===1")
            assert c.js("document.querySelector('#toast').textContent.includes('Item removed')")
            print('PASS red delete icons, cancel safety, explicit confirmation and success notification')
            c.js("document.querySelector('[data-action=add-payment]').click()")
            c.js("document.querySelector('#payment-form [name=amount]').value='1.20';document.querySelector('#payment-form button.primary').click()")
            c.wait("!document.querySelector('#modal').open")
            c.js("document.querySelector('[data-remove-payment]').click()")
            c.wait("!!document.querySelector('#confirm-dialog')")
            c.js("document.querySelector('#confirmation-cancel').click()")
            c.wait("!document.querySelector('#confirm-dialog')")
            assert c.js("document.querySelectorAll('[data-remove-payment]').length===1")
            c.js("document.querySelector('[data-checkout=sale]').click()")
            try:c.wait("document.querySelector('#modal').open && document.querySelector('#a4')")
            except Exception:
                print(c.js("document.body.innerText"),c.errors,flush=True);raise
            assert c.js("getComputedStyle(document.querySelector('.invoice-items th')).backgroundColor==='rgb(219, 234, 254)'")
            assert c.js("getComputedStyle(document.querySelector('.invoice-items th')).color==='rgb(23, 37, 84)'")
            assert c.js("getComputedStyle(document.querySelector('.invoice-totals td')).backgroundColor==='rgb(219, 234, 254)'")
            (artifacts/'receipt-pale-blue.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png'})['data']))
            pdf=c.call('Page.printToPDF',{'printBackground':True})
            (artifacts/'invoice.pdf').write_bytes(base64.b64decode(pdf['data']))
            print('PASS online cash sale and printable invoice PDF')
            c.js("document.querySelector('#modal').close();location.hash='expenses'");time.sleep(.2)
            c.js("document.querySelector('[data-action=expense]').click();document.querySelector('#entry-form [name=description]').value='Browser verification electricity';document.querySelector('[name=supplier]').value='ZESA test';document.querySelector('[name=amount]').value='12.50';document.querySelector('#entry-form button.primary').click()")
            try:c.wait("!document.querySelector('#modal').open")
            except Exception:
                print(c.js("document.querySelector('#modal').innerText"),c.js("[...document.querySelectorAll('#entry-form input')].map(i=>({name:i.name,value:i.value,valid:i.checkValidity(),message:i.validationMessage}))"),c.errors,flush=True);raise
            c.wait("document.querySelector('#main').textContent.includes('Browser verification electricity')")
            print('PASS expense form persistence')
            c.js("location.hash='sales'");time.sleep(.2);c.js("document.querySelector('[data-action=pos]').click()");c.wait("!!document.querySelector('[data-add]')");c.js("document.querySelector('[data-add=p1]').click();document.querySelector('[name=pos-customer]').value='c1'")
            c.call('Network.emulateNetworkConditions',{'offline':True,'latency':0,'downloadThroughput':0,'uploadThroughput':0})
            time.sleep(.3);c.js("document.querySelector('[name=pos-customer]').value='c1'; document.querySelector('[data-checkout=sale]').click()")
            c.wait("document.querySelector('#modal').textContent.includes('PROVISIONAL RECEIPT')")
            assert c.js("!!document.querySelector('.invoice-sheet')")
            assert c.js("getComputedStyle(document.querySelector('.invoice-items th')).backgroundColor==='rgb(219, 234, 254)'")
            print('PASS offline sale and provisional printable receipt')
            c.call('Page.reload');c.wait("!!document.querySelector('.workspace')")
            c.js("document.querySelector('[data-action=sync]').click()")
            c.wait("document.querySelector('#modal').textContent.includes('pending')")
            print('PASS offline queue and authorized session survive browser reload')
            c.js('sessionStorage.clear()');c.call('Page.reload')
            c.wait("!!document.querySelector('#login-form')")
            c.js("document.querySelector('#login-form [name=email]').value='admin@medledger.local';document.querySelector('#login-form [name=password]').value='PharmaDemo!2026';document.querySelector('#login-form button[type=submit]').click()")
            c.wait("!!document.querySelector('.workspace')")
            c.js("document.querySelector('[data-action=sync]').click()")
            c.wait("document.querySelector('#modal').textContent.includes('pending')")
            print('PASS offline password unlock after session-key loss (restart simulation)')
            encrypted=c.js("new Promise((resolve,reject)=>{let r=indexedDB.open('medledger-v1');r.onsuccess=()=>{let q=r.result.transaction('store').objectStore('store').get('queue');q.onsuccess=()=>{resolve(q.result.encrypted===true&&Array.isArray(q.result.bytes));r.result.close()}};r.onerror=()=>reject(r.error)})")
            assert encrypted
            print('PASS local queue stored as AES-GCM ciphertext')
            c.call('Network.emulateNetworkConditions',{'offline':False,'latency':0,'downloadThroughput':-1,'uploadThroughput':-1})
            time.sleep(1);c.js("document.querySelector('#retry-sync').click()")
            try:c.wait("document.querySelector('.sync-footer').textContent.includes('0 pending')",seconds=30)
            except Exception:
                print(c.js("document.querySelector('#modal').innerText"),c.errors,flush=True);raise
            print('PASS reconnect synchronization')
            c.js("document.querySelector('#modal').close();location.hash='dashboard'");time.sleep(.4)
            c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert not c.js('document.documentElement.scrollWidth>window.innerWidth')
            (artifacts/'mobile.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})['data']))
            print('PASS mobile layout without horizontal overflow')
            assert not c.errors,json.dumps(c.errors)
            print('PASS no uncaught JavaScript exceptions')
            try:c.call('Browser.close')
            except Exception:pass
            c.s.close()
        finally:
            if browser:
                subprocess.run(['taskkill','/PID',str(browser.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server.terminate();server.wait();time.sleep(1)
if __name__=='__main__':run()
