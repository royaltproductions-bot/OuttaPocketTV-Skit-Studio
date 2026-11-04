"use strict";
(async function loadMerch(){
  const grid=document.getElementById('merch-grid');
  const status=document.getElementById('merch-status');
  if(!grid||!status)return;
  try{
    const response=await fetch('/api/merch',{cache:'no-store'});
    if(!response.ok)return;
    const data=await response.json();
    if(data.mode!=='live'||!Array.isArray(data.products)||!data.products.length)return;
    const frag=document.createDocumentFragment();
    for(const item of data.products){
      if(!item||!item.name||!/^https:\/\/[a-z0-9.-]+\.fourthwall\.com\/products\/[A-Za-z0-9-]+$/.test(item.url))continue;
      const article=document.createElement('article');article.className='merch-card';
      if(item.image && typeof item.image==='string' && item.image.startsWith('https://')){
        const img=document.createElement('img');img.src=item.image;img.alt=item.name;img.className='merch-product-image';img.loading='lazy';article.appendChild(img);
      }else{
        const art=document.createElement('div');art.className='merch-art merch-brand';
        const tee=document.createElement('div');tee.className='merch-shirt';
        const text=document.createElement('span');text.textContent='OUTTA POCKET TV';
        tee.appendChild(text);art.appendChild(tee);article.appendChild(art);
      }
      const copy=document.createElement('div');copy.className='merch-copy';
      const small=document.createElement('small');small.textContent='OUTTAPOCKETTV MERCH';copy.appendChild(small);
      const title=document.createElement('h3');title.textContent=item.name;copy.appendChild(title);
      if(typeof item.price==='number'&&Number.isFinite(item.price)&&item.currency==='USD'){
        const price=document.createElement('strong');price.className='merch-price';
        price.textContent='From '+new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(item.price);copy.appendChild(price);
      }
      if(item.available===false){
        const sold=document.createElement('strong');sold.className='merch-pending';sold.textContent='SOLD OUT';copy.appendChild(sold);
      }else{
        const buy=document.createElement('a');buy.className='merch-buy';buy.href=item.url;buy.rel='noopener noreferrer';buy.target='_blank';buy.textContent='SEE OPTIONS & BUY ↗';copy.appendChild(buy);
      }
      article.appendChild(copy);frag.appendChild(article);
    }
    if(frag.childNodes.length){
      grid.replaceChildren(frag);
      status.textContent='Print-on-demand merchandise. Our partner handles secure checkout, printing, shipping and fulfillment.';
    }
  }catch(_){/* Show concept previews; never fake availability. */}
})();
