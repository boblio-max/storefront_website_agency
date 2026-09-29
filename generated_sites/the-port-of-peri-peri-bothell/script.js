(function(){
var t=document.querySelector('.nav-toggle'),l=document.querySelector('.nav-links');
if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',o)})}
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener('click',function(e){
  var tgt=document.querySelector(a.getAttribute('href'));
  if(tgt){e.preventDefault();tgt.scrollIntoView({behavior:'smooth'});
    if(l&&l.classList.contains('open')){l.classList.remove('open');t.setAttribute('aria-expanded','false')}}
})});
var h=document.getElementById('siteHeader');
if(h){addEventListener('scroll',function(){h.classList.toggle('scrolled',scrollY>8)},{passive:true})}
var PREVIEW=true;
var f=document.getElementById('quote-form');
function formNote(f){return f.querySelector('.form-note')}
if(f){f.addEventListener('submit',function(e){
  e.preventDefault();
  var n=f.name.value.trim(),p=f.phone.value.trim(),m=f.message.value.trim(),note=formNote(f);
  if(!n||!p||!m){note.textContent='Please fill in your name, phone, and message.';return}
  if(p.replace(/\D/g,'').length<7){note.textContent='That phone number looks too short — please double-check.';return}
  if(f.hp&&f.hp.value){note.textContent='Thanks '+n.split(' ')[0]+'!';f.reset();return}
  if(PREVIEW){note.textContent='Thanks '+n.split(' ')[0]+'! (Design preview — this form goes live when the site launches.)';return}
  var ep=document.querySelector('meta[name="form-endpoint"]');
  if(ep&&ep.content){
    var data={name:n,phone:p,message:m};
    if(f.topic)data.topic=f.topic.value;
    data.hp=f.hp?f.hp.value:'';
    fetch(ep.content,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
      .then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})
      .then(function(){note.textContent='Thanks '+n.split(' ')[0]+'! We received your request and will reply shortly.';f.reset()})
      .catch(function(){note.textContent='Sorry — we could not send your request. Please call us directly.'});
    return;
  }
  note.textContent='Thanks '+n.split(' ')[0]+'! We received your request and will reply shortly.';f.reset()
})}
var dots=Array.prototype.slice.call(document.querySelectorAll('.dot')),
    reviews=Array.prototype.slice.call(document.querySelectorAll('.review')),cur=0;
function show(i){if(!reviews.length)return;cur=(i+reviews.length)%reviews.length;
  reviews.forEach(function(r,j){
    var on=j===cur;r.classList.toggle('active',on);
    r.setAttribute('aria-hidden',on?'false':'true')
  });
  dots.forEach(function(d,j){
    var on=j===cur;d.classList.toggle('active',on);
    if(d.hasAttribute('aria-selected'))d.setAttribute('aria-selected',on?'true':'false');
    if(d.hasAttribute('aria-controls'))d.setAttribute('tabindex',on?'0':'-1')
  })}
dots.forEach(function(d,i){d.addEventListener('click',function(){show(i)})});
var tabs=document.querySelector('.slider-dots');
if(tabs){tabs.addEventListener('keydown',function(e){
  if(e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;
  e.preventDefault();
  var idx=dots.indexOf(document.activeElement);if(idx<0)return;
  show((e.key==='ArrowRight'?idx+1:idx-1+dots.length)%dots.length);dots[idx<dots.length-1?idx+1:0].focus()
})}
var sliderEl=document.querySelector('.review-slider'),autoTimer=null;
function stopAuto(){if(autoTimer){clearInterval(autoTimer);autoTimer=null}}
function startAuto(){if(reviews.length>1&&!prefersReducedMotion){if(autoTimer)stopAuto();autoTimer=setInterval(function(){show(cur+1)},6000)}}
if(sliderEl){sliderEl.addEventListener('mouseenter',stopAuto);sliderEl.addEventListener('mouseleave',startAuto)}
var prefersReducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
var y=document.getElementById('year');if(y){y.textContent=new Date().getFullYear()}
startAuto();
if('IntersectionObserver' in window && !prefersReducedMotion){
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(en){if(en.isIntersecting){en.target.classList.add('visible');obs.unobserve(en.target)}})
  },{threshold:.12});
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){
    el.classList.add('reveal');obs.observe(el)
  });
} else {
  document.querySelectorAll('.card, .strip-item, .about-card, .review').forEach(function(el){el.classList.add('reveal','visible')});
}
/* motion v2 — award-tier entrances, split reveals, magnetic CTAs, progress,
   parallax, count-ups, aurora canvas. All gated on reduced-motion + no-JS. */
document.body.classList.add('js');
var reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
function pageLoaded(){document.body.classList.add('loaded')}
if(document.readyState==='complete'){pageLoaded()}
else{addEventListener('load',pageLoaded);setTimeout(pageLoaded,2500)}
if(!reduceMotion&&'IntersectionObserver' in window){
  var swObs=new IntersectionObserver(function(es){es.forEach(function(en){
    if(en.isIntersecting){en.target.classList.add('in');swObs.unobserve(en.target)}})},{threshold:.4});
  document.querySelectorAll('main h2').forEach(function(h){
    var words=h.textContent.trim().split(/\s+/);
    if(words.length<2)return;
    h.setAttribute('aria-label',h.textContent.trim());
    h.innerHTML=words.map(function(w,i){return '<span class="wmask" aria-hidden="true"><span class="w" style="transition-delay:'+(i*45)+'ms">'+w+'</span></span>'}).join(' ');
    Array.prototype.forEach.call(h.querySelectorAll('.wmask'),function(m){swObs.observe(m)});
  });
}
if(!reduceMotion&&matchMedia('(pointer:fine)').matches){
  document.querySelectorAll('.hero .btn-primary, .cta-banner .btn-primary').forEach(function(btn){
    btn.addEventListener('pointermove',function(e){
      var r=btn.getBoundingClientRect();
      btn.style.transform='translate('+((e.clientX-(r.left+r.width/2))*.18)+'px,'+((e.clientY-(r.top+r.height/2))*.28)+'px)';
    });
    btn.addEventListener('pointerleave',function(){
      btn.style.transition='transform .25s ease';btn.style.transform='';
      setTimeout(function(){btn.style.transition=''},260);
    });
  });
}
var pBar=document.getElementById('progressBar');
var plx=Array.prototype.slice.call(document.querySelectorAll('[data-parallax]'));
var ticking=false;
function onScroll2(){ticking=false;
  if(pBar){var max=document.documentElement.scrollHeight-innerHeight;
    pBar.style.transform='scaleX('+(max>0?scrollY/max:0)+')'}
  if(!reduceMotion){plx.forEach(function(el){
    el.style.transform='translateY('+(scrollY*parseFloat(el.getAttribute('data-parallax')))+'px)'})}
}
addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(onScroll2)}},{passive:true});
onScroll2();
var counters=document.querySelectorAll('[data-count]');
function runCount(el){
  var target=parseFloat(el.getAttribute('data-count'));
  var dec=parseInt(el.getAttribute('data-decimals')||'0',10);
  if(reduceMotion){el.textContent=target.toFixed(dec);return}
  var t0=null,dur=1400;
  function step(t){if(!t0)t0=t;var p=Math.min((t-t0)/dur,1);
    el.textContent=(target*(1-Math.pow(1-p,3))).toFixed(dec);
    if(p<1)requestAnimationFrame(step)}
  requestAnimationFrame(step);
}
if(counters.length){
  if('IntersectionObserver' in window){
    var cObs=new IntersectionObserver(function(es){es.forEach(function(en){
      if(en.isIntersecting){runCount(en.target);cObs.unobserve(en.target)}})},{threshold:.5});
    counters.forEach(function(c){cObs.observe(c)});
  }else{counters.forEach(runCount)}
}
var aurora=document.getElementById('heroAurora');
if(aurora&&!reduceMotion){
  var actx=aurora.getContext('2d'),AW,AH,parts=[],running=true;
  function sizeAurora(){var r=aurora.parentElement.getBoundingClientRect();
    AW=aurora.width=Math.max(1,Math.round(r.width));AH=aurora.height=Math.max(1,Math.round(r.height))}
  sizeAurora();addEventListener('resize',sizeAurora);
  var cs=getComputedStyle(document.documentElement);
  var brandC=(cs.getPropertyValue('--brand')||'#ffffff').trim();
  var goldC=(cs.getPropertyValue('--gold')||'#ffffff').trim();
  for(var pi=0;pi<46;pi++){parts.push({x:Math.random(),y:Math.random(),
    r:1+Math.random()*2.6,s:.0004+Math.random()*.0012,o:.15+Math.random()*.5,hue:Math.random()<.5?0:1})}
  function drawAurora(){
    if(!running)return;
    actx.clearRect(0,0,AW,AH);
    var t=Date.now()*.0002;
    var g1x=AW*(.25+.15*Math.sin(t)),g1y=AH*(.3+.1*Math.cos(t*1.3));
    var g=actx.createRadialGradient(g1x,g1y,0,g1x,g1y,AW*.4);
    g.addColorStop(0,goldC);g.addColorStop(1,'rgba(0,0,0,0)');
    actx.globalAlpha=.28;actx.fillStyle=g;actx.fillRect(0,0,AW,AH);
    var g2x=AW*(.8+.12*Math.cos(t*.8)),g2y=AH*(.7+.12*Math.sin(t*1.1));
    var g2=actx.createRadialGradient(g2x,g2y,0,g2x,g2y,AW*.35);
    g2.addColorStop(0,brandC);g2.addColorStop(1,'rgba(0,0,0,0)');
    actx.fillStyle=g2;actx.fillRect(0,0,AW,AH);
    parts.forEach(function(p){
      p.y-=p.s;if(p.y<-.02){p.y=1.02;p.x=Math.random()}
      actx.globalAlpha=p.o;actx.fillStyle=p.hue?goldC:'#ffffff';
      actx.beginPath();actx.arc(p.x*AW,p.y*AH,p.r,0,6.283);actx.fill();
    });
    actx.globalAlpha=1;
    requestAnimationFrame(drawAurora);
  }
  if('IntersectionObserver' in window){
    new IntersectionObserver(function(es){es.forEach(function(en){
      var was=running;running=en.isIntersecting;if(running&&!was)drawAurora()})}).observe(aurora);
  }
  drawAurora();
}
})();