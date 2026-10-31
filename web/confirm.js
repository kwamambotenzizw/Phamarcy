// Independent dialog: opening a confirmation never replaces an active form.
let active=false;
export function confirmAction({title='Confirm action',message,confirmLabel='Confirm',danger=true,icon}){
  if(active)return Promise.resolve(false);
  active=true;
  return new Promise(resolve=>{
    const dialog=document.createElement('dialog');dialog.id='confirm-dialog';dialog.className=danger?'confirm-danger':'confirm-neutral';dialog.setAttribute('aria-labelledby','confirmation-title');dialog.setAttribute('aria-describedby','confirmation-message');
    dialog.innerHTML=`<div class="confirm-body"><span class="confirm-symbol">${icon(danger?'alert':'shield')}</span><h2 id="confirmation-title"></h2><p id="confirmation-message"></p><div class="confirm-actions"><button type="button" id="confirmation-cancel" autofocus>Cancel</button><button type="button" id="confirmation-accept" class="${danger?'danger-confirm':'primary'}"></button></div></div>`;
    dialog.querySelector('h2').textContent=title;dialog.querySelector('p').textContent=message;dialog.querySelector('#confirmation-accept').textContent=confirmLabel;
    let accepted=false;
    dialog.addEventListener('close',()=>{dialog.remove();active=false;resolve(accepted)},{once:true});
    dialog.querySelector('#confirmation-cancel').onclick=()=>dialog.close();
    dialog.querySelector('#confirmation-accept').onclick=()=>{accepted=true;dialog.close()};
    document.body.append(dialog);dialog.showModal();dialog.querySelector('#confirmation-cancel').focus();
  });
}
