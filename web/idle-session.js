export const IDLE_TIMEOUT_MS=5*60*1000;

// Only actual interface interaction extends activity; API polling does not.
export function createIdleSession({hasSession,onTimeout,onActivity}){
  let active=false,lastActivity=0;
  const key='last-user-activity';
  function persist(){if(active)sessionStorage.setItem(key,String(lastActivity))}
  function check(){
    if(!active||!hasSession())return false;
    if(Date.now()-lastActivity<IDLE_TIMEOUT_MS)return false;
    active=false;sessionStorage.removeItem(key);Promise.resolve(onTimeout()).catch(()=>{});return true;
  }
  function interact(event){
    if(!active||!hasSession())return;
    if(check()){if(event.cancelable)event.preventDefault();event.stopImmediatePropagation();return}
    lastActivity=Date.now();persist();onActivity?.(lastActivity);
  }
  for(const type of ['pointerdown','pointermove','click','keydown','input','touchstart','wheel'])document.addEventListener(type,interact,{capture:true,passive:false});
  document.addEventListener('visibilitychange',()=>{persist();check()});
  window.addEventListener('focus',check);window.addEventListener('pageshow',check);
  setInterval(check,1000);
  return {
    start(resume=false){const saved=Number(sessionStorage.getItem(key));lastActivity=resume&&saved>0?saved:Date.now();active=true;persist();check()},
    stop(){active=false;sessionStorage.removeItem(key)},
    get lastActivity(){return lastActivity},
    get expired(){return active&&Date.now()-lastActivity>=IDLE_TIMEOUT_MS},
    check
  };
}
