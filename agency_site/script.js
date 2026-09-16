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

/* magnetic buttons (fine pointers only) */
if(matchMedia('(pointer:fine)').matches){document.querySelectorAll('.btn-ink').forEach(function(b){
b.addEventListener('mousemove',function(e){var r=b.getBoundingClientRect();
b.style.transform='translate('+((e.clientX-r.left-r.width/2)*.12)+'px,'+((e.clientY-r.top-r.height/2)*.18)+'px)'});
b.addEventListener('mouseleave',function(){b.style.transform=''})})}

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

/* inquiry form -> prefilled email */
var f=document.getElementById('inquiry-form');
if(f){f.addEventListener('submit',function(e){e.preventDefault();
var name=f.name.value.trim(),biz=f.business.value.trim(),em=f.email.value.trim(),
ph=f.phone.value.trim(),topic=f.topic.value,ms=f.message.value.trim(),
note=f.querySelector('.form-note');
if(!name||!biz||!em){note.textContent='Name, business, and email — that\u2019s all I need to start.';return}
if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)){note.textContent='That email doesn\u2019t look right \u2014 mind checking it?';return}
var subject=encodeURIComponent('New project inquiry \u2014 '+biz),
body=encodeURIComponent('Name: '+name+'\nBusiness: '+biz+'\nEmail: '+em+'\nPhone: '+(ph||'(rather email)')+'\nProject: '+topic+'\n\n'+(ms||'(they left this blank)'));
window.location.href='mailto:storefront.webs@gmail.com?subject='+subject+'&body='+body;
note.textContent='Thanks '+name.split(' ')[0]+' \u2014 your email app should open with it all filled in. Just press send.'})}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
})();
