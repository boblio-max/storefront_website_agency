(function(){
/* ambient glow canvas */
var cv=document.getElementById('glow');
if(cv&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
var ctx=cv.getContext('2d'),W,H,t=0;
function size(){W=cv.width=innerWidth;H=cv.height=innerHeight}
size();addEventListener('resize',size);
var blobs=[{x:.2,y:.25,r:.42,hue:258,a:.5},{x:.85,y:.6,r:.5,hue:220,a:.35},{x:.6,y:.95,r:.45,hue:280,a:.4}];
(function draw(){
t+=.004;ctx.clearRect(0,0,W,H);ctx.globalCompositeOperation='lighter';
blobs.forEach(function(b,i){
var x=(b.x+Math.sin(t*(1+i*.3)+i*2)*.06)*W,y=(b.y+Math.cos(t*(1+i*.25)+i)*.06)*H,r=b.r*Math.min(W,H);
var g=ctx.createRadialGradient(x,y,0,x,y,r);
g.addColorStop(0,'hsla('+b.hue+',80%,62%,'+b.a+')');g.addColorStop(1,'hsla('+b.hue+',80%,62%,0)');
ctx.fillStyle=g;ctx.beginPath();ctx.arc(x,y,r,0,7);ctx.fill()});
requestAnimationFrame(draw)})}

/* overlay menu */
var open=document.getElementById('menuBtn'),close=document.getElementById('menuClose'),
ov=document.getElementById('overlay');
function setMenu(on){ov.classList.toggle('open',on);ov.setAttribute('aria-hidden',String(!on));
open.setAttribute('aria-expanded',String(on));document.body.style.overflow=on?'hidden':''}
if(open&&ov){open.addEventListener('click',function(){setMenu(true)});
close.addEventListener('click',function(){setMenu(false)});
ov.querySelectorAll('a').forEach(function(a){a.addEventListener('click',function(){setMenu(false)})});
addEventListener('keydown',function(e){if(e.key==='Escape')setMenu(false)})}

/* smooth anchors */
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){
var t2=document.querySelector(a.getAttribute('href'));
if(t2){e.preventDefault();t2.scrollIntoView({behavior:'smooth'})}})});

/* reveals */
var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){
es.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');io.unobserve(en.target)}})},{threshold:.12}):null;
document.querySelectorAll('.reveal').forEach(function(el){if(io){io.observe(el)}else{el.classList.add('visible')}});

/* counters */
document.querySelectorAll('.count').forEach(function(el){
var end=parseInt(el.dataset.count||'0',10),t0=null;
function tick(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/1200);
el.textContent=Math.round(end*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(tick)}
if('IntersectionObserver' in window){var o2=new IntersectionObserver(function(es){
es.forEach(function(en){if(en.isIntersecting){requestAnimationFrame(tick);o2.disconnect()}})},{threshold:.4});
o2.observe(el)}else{el.textContent=end}});

/* inquiry form -> prefilled email */
var f=document.getElementById('inquiry-form');
if(f){f.addEventListener('submit',function(e){e.preventDefault();
var name=f.name.value.trim(),biz=f.business.value.trim(),em=f.email.value.trim(),
ph=f.phone.value.trim(),topic=f.topic.value,ms=f.message.value.trim(),
note=f.querySelector('.form-note');
if(!name||!biz||!em){note.textContent='Please fill in your name, business, and email.';return}
if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)){note.textContent='That email doesn\u2019t look right \u2014 please double-check.';return}
var subject=encodeURIComponent('New project inquiry \u2014 '+biz),
body=encodeURIComponent('Name: '+name+'\nBusiness: '+biz+'\nEmail: '+em+'\nPhone: '+(ph||'(not given)')+'\nProject: '+topic+'\n\n'+(ms||'(no extra details)'));
window.location.href='mailto:storefront.webs@gmail.com?subject='+subject+'&body='+body;
note.textContent='Thanks '+name.split(' ')[0]+' \u2014 your email app should open with everything pre-filled. Just hit send.'})}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
})();
