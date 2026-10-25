"use strict";
const $=id=>document.getElementById(id);
let token='';
function el(tag,text){
  const node=document.createElement(tag);
  if(text!==undefined)node.textContent=text;
  return node;
}
async function api(path,method='GET',data){
  const response=await fetch(path,{
    method,
    headers:{Authorization:'Bearer '+token,...(data?{'Content-Type':'application/json'}:{})},
    body:data?JSON.stringify(data):undefined,
    cache:'no-store'
  });
  const result=await response.json();
  if(!response.ok)throw Error(result.detail||'Could not load Stripe records.');
  return result;
}
function humanDuration(seconds){
  const minutes=Math.floor(seconds/60), remainder=seconds%60;
  if(!minutes)return seconds+' seconds';
  return minutes+' min'+(remainder?' '+remainder+' sec':'');
}
function emailLink(email,subject,body){
  const mail=el('a','EMAIL CUSTOMER ↗');
  mail.href='mailto:'+encodeURIComponent(email)+'?subject='+encodeURIComponent(subject)+'&body='+encodeURIComponent(body);
  mail.className='button small';
  return mail;
}
function displayOrders(orders){
  const node=$('orders');
  node.replaceChildren();
  if(!orders.length)node.append(el('p','No verified paid orders yet.'));
  for(const order of orders){
    const article=el('article');
    article.className='admin-order';
    const amount=typeof order.amount_cents==='number'?'$'+(order.amount_cents/100).toFixed(2):'';
    article.append(
      el('h3',(order.delivered_at?'DELIVERED':'READY TO PRODUCE')+' — '+order.email),
      el('p','VIDEO: '+humanDuration(order.duration_seconds||15)+' • '+amount+' paid'),
      el('p','IDEA: '+order.idea),
      el('p','STYLE: '+order.style),
      el('p','NOTES: '+(order.instructions||'None')),
      el('p','Stripe checkout: '+order.id)
    );
    if(order.delivered_at){
      article.append(el('p','Marked delivered: '+order.delivered_at));
    }else{
      article.append(emailLink(order.email,'Your OuttaPocketTV custom skit',
        'Your custom OuttaPocketTV skit is attached. Thanks for creating with us!'));
      const button=el('button','MARK DELIVERED AFTER EMAILING');
      button.className='button small';
      button.addEventListener('click',async()=>{
        if(!confirm('Have you actually emailed the finished MP4 to '+order.email+'?'))return;
        button.disabled=true;
        try{await api('/api/admin/delivered','POST',{session_id:order.id});await refresh();}
        catch(error){alert(error.message);button.disabled=false;}
      });
      article.append(button);
    }
    node.append(article);
  }
}
function displayRequests(requests){
  const node=$('custom-requests');
  node.replaceChildren();
  if(!requests.length)node.append(el('p','No custom-length quote requests yet.'));
  for(const request of requests){
    const article=el('article');
    article.className='admin-order';
    const contacted=request.status==='contacted';
    article.append(
      el('h3',(contacted?'CONTACTED':'QUOTE NEEDED')+' — '+request.email),
      el('p','REQUESTED LENGTH: '+humanDuration(request.duration_seconds)),
      el('p','IDEA: '+request.idea),
      el('p','STYLE: '+request.style),
      el('p','NOTES: '+(request.instructions||'None')),
      el('p','Stripe request: '+request.id)
    );
    article.append(emailLink(request.email,'Your OuttaPocketTV custom video quote',
      'Thanks for requesting a '+humanDuration(request.duration_seconds)+' OuttaPocketTV video.\n\nAbout your skit: '+request.idea+'\n\nYour custom quote: '));
    if(!contacted){
      const button=el('button','MARK CONTACTED AFTER EMAIL');
      button.className='button small';
      button.addEventListener('click',async()=>{
        if(!confirm('Have you contacted '+request.email+' with a quote?'))return;
        button.disabled=true;
        try{await api('/api/admin/custom-request-contacted','POST',{customer_id:request.id});await refresh();}
        catch(error){alert(error.message);button.disabled=false;}
      });
      article.append(button);
    }
    node.append(article);
  }
}
async function refresh(){
  const ordersData=await api('/api/admin/orders');
  const requestsData=await api('/api/admin/custom-requests').catch(error=>({requests:[],error:error.message}));
  $('login').hidden=true;
  $('dashboard').hidden=false;
  displayOrders(ordersData.orders||[]);
  displayRequests(requestsData.requests||[]);
  $('dashboard-alert').textContent=requestsData.error?
    'Custom requests could not load: '+requestsData.error:'';
}
$('login-button').addEventListener('click',async()=>{
  token=$('token').value.trim();
  try{await refresh();$('token').value='';$('login-alert').textContent='';}
  catch(error){$('login-alert').textContent=error.message;}
});
$('reload').addEventListener('click',async()=>{
  try{await refresh();}
  catch(error){$('dashboard-alert').textContent=error.message;}
});
