(function(){
var t=document.querySelector('.nav-toggle'),l=document.querySelector('.nav-links');
if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',o)})}
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){
var t2=document.querySelector(a.getAttribute('href'));
if(t2){e.preventDefault();t2.scrollIntoView({behavior:'smooth'});
if(l&&l.classList.contains('open')){l.classList.remove('open');t.setAttribute('aria-expanded','false')}}})});
var h=document.getElementById('siteHeader');
if(h){addEventListener('scroll',function(){h.classList.toggle('scrolled',scrollY>8)},{passive:true})}
var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){
es.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');io.unobserve(en.target)}})},{threshold:.12}):null;
document.querySelectorAll('.reveal').forEach(function(el){if(io){io.observe(el)}else{el.classList.add('visible')}});
var f=document.getElementById('inquiry-form');
if(f){f.addEventListener('submit',function(e){e.preventDefault();
var name=f.name.value.trim(),biz=f.business.value.trim(),em=f.email.value.trim(),
ph=f.phone.value.trim(),topic=f.topic.value,ms=f.message.value.trim(),
note=f.querySelector('.form-note');
if(!name||!biz||!em){note.textContent='Please fill in your name, business, and email.';return}
if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)){note.textContent='That email doesn\u2019t look right \u2014 please double-check.';return}
var subject=encodeURIComponent('Free preview request \u2014 '+biz),
body=encodeURIComponent('Name: '+name+'\nBusiness: '+biz+'\nEmail: '+em+'\nPhone: '+(ph||'(not given)')+'\nInterested in: '+topic+'\n\n'+(ms||'(no extra details)'));
window.location.href='mailto:storefront.webs@gmail.com?subject='+subject+'&body='+body;
note.textContent='Thanks '+name.split(' ')[0]+'! Your email app should open with everything pre-filled \u2014 just hit send.'})}
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
})();
