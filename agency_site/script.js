(function(){
/* scroll progress */
var bar=document.getElementById('progressBar');
if(bar){addEventListener('scroll',function(){
var max=document.documentElement.scrollHeight-innerHeight;
bar.style.transform='scaleX('+(max>0?scrollY/max:0)+')'},{passive:true})}

/* before/after slider */
var ba=document.getElementById('ba'),range=document.getElementById('baRange'),
after=document.getElementById('baAfter'),handle=document.getElementById('baHandle');
if(ba&&range&&after&&handle){range.addEventListener('input',function(){
var v=Math.max(2,Math.min(98,Number(range.value)||50));
after.style.clipPath='inset(0 0 0 '+v+'%)';handle.style.left=v+'%'})}

/* demo tabs + desktop/phone toggle */
document.querySelectorAll('.mini-tab').forEach(function(tab){tab.addEventListener('click',function(){
document.querySelectorAll('.mini-tab').forEach(function(o){o.classList.remove('active');o.setAttribute('aria-selected','false')});
tab.classList.add('active');tab.setAttribute('aria-selected','true');
document.querySelectorAll('.mini-page').forEach(function(p){p.hidden=p.id!=='page-'+tab.dataset.page})})});
var stage=document.getElementById('demoStage');
document.querySelectorAll('.view-btn').forEach(function(btn){btn.addEventListener('click',function(){
document.querySelectorAll('.view-btn').forEach(function(o){o.classList.remove('active')});
btn.classList.add('active');
if(stage){stage.classList.toggle('phone',btn.dataset.view==='mobile')}})});

/* mobile nav */
var t=document.querySelector('.nav-toggle'),l=document.querySelector('.nav-links');
if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',String(o));t.textContent=o?'Close':'Menu'})}
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){
var t2=document.querySelector(a.getAttribute('href'));
if(t2){e.preventDefault();t2.scrollIntoView({behavior:'smooth'});
if(l&&l.classList.contains('open')){l.classList.remove('open');t.setAttribute('aria-expanded','false');t.textContent='Menu'}}})});

/* reveals */
var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){
es.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');io.unobserve(en.target)}})},{threshold:.1}):null;
document.querySelectorAll('.reveal').forEach(function(el){if(io){io.observe(el)}else{el.classList.add('visible')}});

/* booking month: next month name keeps the availability line evergreen */
var bm=document.getElementById('bookMonth');
if(bm){var now=new Date(),nm=new Date(now.getMonth()+1>11?now.getFullYear()+1:now.getFullYear(),(now.getMonth()+1)%12,1);
bm.textContent=nm.toLocaleString('en-US',{month:'long'})}

/* Bothell clock */
var clock=document.getElementById('clock');
function tick(){if(!clock)return;
try{clock.textContent=new Intl.DateTimeFormat('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/Los_Angeles'}).format(new Date())}catch(e){clock.textContent=''}}
if(clock){tick();setInterval(tick,30000)}

/* gentle parallax on work visuals */
var shots=Array.prototype.slice.call(document.querySelectorAll('.work-shot'));
if(shots.length&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
var ticking=false;
addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(function(){
var vh=innerHeight;shots.forEach(function(s){var r=s.getBoundingClientRect();
var p=(r.top+r.height/2-vh/2)/vh;if(Math.abs(p)<1){s.style.backgroundPosition='50% '+(50+p*14)+'%'}});
ticking=false})}},{passive:true})}

/* inquiry form -> POST /api/inquiry, mailto fallback */
var f=document.getElementById('inquiry-form');
if(f){f.addEventListener('submit',function(e){e.preventDefault();
var name=f.name.value.trim(),biz=f.business.value.trim(),em=f.email.value.trim(),
ph=f.phone.value.trim(),topic=f.topic.value,ms=f.message.value.trim(),
note=f.querySelector('.form-note'),btn=f.querySelector('button[type="submit"]');
function fail(msg){note.innerHTML=msg+' <a href="mailto:storefront.webs@gmail.com?subject='
+encodeURIComponent('New project inquiry \u2014 '+biz)+'">Email me directly instead</a>.';
if(btn){btn.disabled=false;btn.textContent='Send it over \u2192'}}
if(!name||!biz||!em){note.textContent='Name, business, and email \u2014 that\u2019s all I need to start.';return}
if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)){note.textContent='That email doesn\u2019t look right \u2014 mind checking it?';return}
if(btn){btn.disabled=true;btn.textContent='Sending\u2026'}
note.textContent='';
fetch('/api/inquiry',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({name:name,business:biz,email:em,phone:ph,topic:topic,message:ms,
company_website:(f.company_website&&f.company_website.value)||''})})
.then(function(r){return r.json().then(function(d){return{ok:r.ok&&d&&d.ok,err:(d&&d.err)||(d&&d.error)}})})
.then(function(out){
if(out.ok){note.textContent='Received \u2014 thanks '+name.split(' ')[0]+'! I\u2019ll reply within a day or so.';f.reset()}
else{fail(out.err||'Something went wrong sending that.')}}
,function(){fail('Network trouble sending that.')})
.finally(function(){if(btn&&note.textContent.indexOf('Received')!==0){btn.disabled=false;btn.textContent='Send it over \u2192'}})})}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
})();
