(() => {
  const parse = c => { const m = (c||'').match(/[\d.]+/g); if(!m) return null; return {r:+m[0],g:+m[1],b:+m[2],a:m[3]===undefined?1:+m[3]}; };
  const lum = ({r,g,b}) => { const f = v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b); };
  const ratio = (a,b) => { const l1=lum(a), l2=lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05); };
  const bgOf = el => {
    const stack=[]; let e=el, overImage=false;
    while(e){ const st=getComputedStyle(e);
      if(st.backgroundImage && st.backgroundImage!=='none') overImage=true;
      const p=parse(st.backgroundColor);
      if(p && p.a>0.001) stack.push(p);
      if(p && p.a>=0.999) break;
      e=e.parentElement;
    }
    let base={r:255,g:255,b:255,a:1};
    for(let i=stack.length-1;i>=0;i--){ const c=stack[i]; base={r:c.r*c.a+base.r*(1-c.a), g:c.g*c.a+base.g*(1-c.a), b:c.b*c.a+base.b*(1-c.a), a:1}; }
    return {bg:base, overImage};
  };
  const out=[];
  document.querySelectorAll('body *').forEach(el=>{
    const txt=[...el.childNodes].filter(n=>n.nodeType===3 && n.textContent.trim()).map(n=>n.textContent.trim()).join(' ').slice(0,40);
    if(!txt) return;
    const st=getComputedStyle(el);
    if(st.visibility==='hidden'||st.display==='none'||parseFloat(st.opacity)===0) return;
    const rect=el.getBoundingClientRect(); if(rect.width===0||rect.height===0) return;
    const {bg, overImage}=bgOf(el);
    if(overImage) return;                      // 文字压在图片上：静态算不准，跳过
    let fg=parse(st.color); if(!fg) return;
    if(fg.a<1) fg={r:fg.r*fg.a+bg.r*(1-fg.a), g:fg.g*fg.a+bg.g*(1-fg.a), b:fg.b*fg.a+bg.b*(1-fg.a), a:1};
    const size=parseFloat(st.fontSize), bold=parseInt(st.fontWeight)>=700;
    const need=(size>=24||(size>=18.66&&bold))?3:4.5;
    const r=ratio(fg,bg);
    if(r<need){
      const path=[]; let e=el; for(let i=0;i<3&&e;i++){ path.unshift(e.tagName.toLowerCase()+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\s+/).slice(0,2).join('.'):'')); e=e.parentElement; }
      out.push(r.toFixed(2)+' | '+st.color+' on rgb('+Math.round(bg.r)+','+Math.round(bg.g)+','+Math.round(bg.b)+') | '+Math.round(size)+'px | '+path.join(' > ')+' | "'+txt+'"');
    }
  });
  return JSON.stringify(out.slice(0,60));
})()
