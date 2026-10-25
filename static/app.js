"use strict";
const $=id=>document.getElementById(id);let previewToken='',config={};
const VIDEO_TIERS={15:{price:'$29.99',label:'Up to 15 seconds'},30:{price:'$49.99',label:'Up to 30 seconds'},60:{price:'$89.99',label:'Up to 1 minute'}};
$('preview-duration').addEventListener('change',()=>{
  const choice=VIDEO_TIERS[$('preview-duration').value];
  if(choice){$('preview-price').textContent=choice.price;$('preview-length').textContent=choice.label+' • MP4 by email';}
});
$('year').textContent=new Date().getFullYear();
$('idea').addEventListener('input',()=>{$('char-count').textContent=$('idea').value.length+' / 450'});
function msg(id,message,error=false){const n=$(id);n.textContent=message;n.classList.toggle('error',error)}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json().catch(()=>({detail:'Service unavailable'}));if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Please check your information.');return data;}
function show(result){previewToken=result.preview_token;$('preview-result').hidden=false;$('preview-form').hidden=true;$('sample-image').src=result.preview_image;$('sample-mode').textContent='AI SAMPLE PREVIEW';$('sample-caption').textContent='Your original skit idea, illustrated by AI and watermarked with OuttaPocketTV. Like it? Order your custom video below.';sessionStorage.setItem('optv_sample',JSON.stringify({...result,stored_at:Date.now()}));$('preview-result').scrollIntoView({block:'center',behavior:'smooth'});}
const prior=sessionStorage.getItem('optv_sample');if(prior){try{const record=JSON.parse(prior);if(Date.now()-record.stored_at<72*3600*1000)show(record);else sessionStorage.removeItem('optv_sample');}catch(_){sessionStorage.removeItem('optv_sample')}}
async function setup(){try{const response=await fetch('/api/config',{cache:'no-store'});config=await response.json();if(!config.preview_enabled){msg('form-alert','Free AI previews are temporarily unavailable. You can still purchase a custom skit.',true);$('preview-button').disabled=true;}if(!config.checkout_enabled){$('direct-checkout-button').disabled=true;}}catch(_){msg('form-alert','The studio is unavailable right now. Please retry.',true);$('preview-button').disabled=true;}}setup();
$('preview-form').addEventListener('submit',async e=>{e.preventDefault();msg('form-alert','');const values={idea:$('idea').value.trim(),style:$('style').value,email:$('email').value.trim()};const button=$('preview-button');button.disabled=true;button.textContent='DIRECTING YOUR SCENE…';try{show(await post('/api/preview',values));}catch(err){msg('form-alert',err.message,true);}finally{button.disabled=false;button.textContent='GENERATE MY FREE PREVIEW ↗';}});
$('purchase-button').addEventListener('click',()=>{$('instructions').hidden=false;$('checkout-button').hidden=false;$('checkout-button').scrollIntoView({block:'center',behavior:'smooth'});});
$('checkout-button').addEventListener('click',async()=>{if(!previewToken)return;const button=$('checkout-button');button.disabled=true;msg('checkout-alert','');button.textContent='OPENING SECURE CHECKOUT…';try{const result=await post('/api/checkout',{preview_token:previewToken,instructions:$('instructions').value.trim(),duration_seconds:Number($('preview-duration').value)});if(!result.checkout_url.startsWith('https://checkout.stripe.com/'))throw Error('Unexpected checkout URL.');location.assign(result.checkout_url);}catch(e){msg('checkout-alert',e.message,true);button.disabled=false;button.textContent='CONTINUE TO SECURE CHECKOUT ↗';}});
$('new-idea-button').addEventListener('click',()=>{msg('checkout-alert','Your one free preview is already used. You can review it above or order the video.');});

$('direct-checkout-button').addEventListener('click',async()=>{const button=$('direct-checkout-button');const idea=$('idea').value.trim(),email=$('email').value.trim(),style=$('style').value;if(idea.length<18||idea.length>450||!$('email').validity.valid)return msg('form-alert','Enter your skit idea (18–450 characters) and a valid email.',true);button.disabled=true;msg('form-alert','Opening secure checkout…');try{const r=await post('/api/checkout-direct',{idea,email,style,duration_seconds:Number($('direct-duration').value)});if(!r.checkout_url.startsWith('https://checkout.stripe.com/'))throw Error('Unexpected checkout URL.');location.assign(r.checkout_url);}catch(e){msg('form-alert',e.message,true);button.disabled=false;}});

$('custom-request-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const form=$('custom-request-form');
  if(!form.reportValidity())return;
  const button=$('custom-request-button');
  const data={
    email:$('custom-email').value.trim(),
    duration_seconds:Number($('custom-duration').value),
    style:$('custom-style').value,
    idea:$('custom-idea').value.trim(),
    instructions:$('custom-instructions').value.trim()
  };
  button.disabled=true;
  button.textContent='SUBMITTING REQUEST…';
  msg('custom-alert','');
  try{
    const result=await post('/api/custom-request',data);
    msg('custom-alert',result.message||'Request received! Watch your email for a custom quote.');
    form.reset();
    button.textContent='REQUEST RECEIVED ✓';
  }catch(err){
    msg('custom-alert',err.message,true);
    button.disabled=false;
    button.textContent='SUBMIT CUSTOM REQUEST ↗';
  }
});

document.querySelectorAll('.tier-card a[data-duration]').forEach(link=>{
  link.addEventListener('click',()=>{
    const duration=link.dataset.duration;
    $('direct-duration').value=duration;
    $('preview-duration').value=duration;
    $('preview-duration').dispatchEvent(new Event('change'));
  });
});
