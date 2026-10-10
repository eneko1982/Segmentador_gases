"""
viewer.py - Imágenes y reproductor de vídeo con máscaras superpuestas.

  compose_frame()   superpone máscaras sobre un fotograma (Python; para exportar imágenes y GIF)
  build_payload()   codifica fotogramas y máscaras para el reproductor
  player_html()     reproductor autónomo (HTML + JavaScript): reproducir, pausar, avanzar,
                    activar/desactivar la máscara real y la detectada, contorno/relleno/acierto-error,
                    opacidad, grosor, zoom y descarga del fotograma en PNG.
"""
from __future__ import annotations

import base64
import io
import json

import matplotlib
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

TRUE_RGB, PRED_RGB, TP_RGB = (42, 120, 214), (235, 104, 52), (46, 160, 67)   # real, detectada, acierto
STYLES = {"contour": "Contorno", "fill": "Relleno", "error": "Acierto / error"}


# ---------------------------------------------------------------------------
# Imágenes
# ---------------------------------------------------------------------------
def colorize(x_mk: np.ndarray, vmax: float) -> np.ndarray:
    """Mapa divergente azul–blanco–rojo (cambio en mK) como imagen RGB uint8."""
    rgba = matplotlib.colormaps["RdBu_r"](np.clip(x_mk / vmax, -1, 1) * 0.5 + 0.5)
    return (rgba[..., :3] * 255).astype(np.uint8)


def gray_rgb(frame: np.ndarray) -> np.ndarray:
    return np.repeat(frame[:, :, None], 3, 2)


def png_uri(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=3)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def edge(mask: np.ndarray, thick: int = 1) -> np.ndarray:
    e = mask & ~ndi.binary_erosion(mask, border_value=0)
    return ndi.binary_dilation(e, iterations=thick - 1) if thick > 1 else e


def compose_frame(base: np.ndarray, truth: np.ndarray | None = None, pred: np.ndarray | None = None,
                  style: str = "contour", opacity: float = 0.9, thick: int = 1, scale: int = 1) -> np.ndarray:
    """Superpone la máscara real (azul) y la detectada (naranja) sobre `base` ((H,W) u8 o (H,W,3)).

    style: "contour" | "fill" | "error" (verde = acierto, naranja = falso positivo, azul = falso negativo).
    `scale` amplía la imagen (vecino más próximo) ANTES de dibujar, para que los contornos queden finos."""
    rgb = gray_rgb(base) if base.ndim == 2 else base
    if scale > 1:
        rgb = np.repeat(np.repeat(rgb, scale, 0), scale, 1)
        truth = None if truth is None else np.repeat(np.repeat(truth, scale, 0), scale, 1)
        pred = None if pred is None else np.repeat(np.repeat(pred, scale, 0), scale, 1)
    out = rgb.astype(np.float32)

    def paint(mask, colour, alpha):
        out[mask] = (1 - alpha) * out[mask] + alpha * np.array(colour, np.float32)

    if style == "error":
        if truth is not None and pred is not None:
            paint(truth & ~pred, TRUE_RGB, opacity * 0.4)
            paint(pred & ~truth, PRED_RGB, opacity * 0.4)
            paint(pred & truth, TP_RGB, opacity * 0.4)
    elif style == "fill":
        for m, c in ((truth, TRUE_RGB), (pred, PRED_RGB)):
            if m is not None:
                paint(m, c, opacity * 0.45)
    else:
        for m, c in ((truth, TRUE_RGB), (pred, PRED_RGB)):
            if m is not None and m.any():
                paint(edge(m, thick), c, opacity)
    return out.astype(np.uint8)


# ---------------------------------------------------------------------------
# Reproductor
# ---------------------------------------------------------------------------
def build_payload(views: dict, truth, pred, fps: float, stride: int, leak_on_s: float, learn_s: float) -> dict:
    """`views` = {nombre: array (T,H,W) u8 o (T,H,W,3) u8}; las máscaras son (T,H,W) bool o None."""
    idx = list(range(0, len(next(iter(views.values()))), stride))
    first = next(iter(views.values()))
    h, w = first.shape[1:3]

    def mask_uris(m):
        return None if m is None else [png_uri((m[i] * 255).astype(np.uint8)) for i in idx]

    return dict(w=int(w), h=int(h), n=len(idx), fps=float(fps), stride=int(stride), idx=idx,
                leakOn=float(leak_on_s), learn=float(learn_s),
                views=[dict(name=name, frames=[png_uri(arr[i]) for i in idx]) for name, arr in views.items()],
                truth=mask_uris(truth), pred=mask_uris(pred))


def player_height(payload: dict, page_width: int = 1100) -> int:
    """Altura (px) que debe reservar el iframe para el reproductor."""
    n = max(1, len(payload["views"]))
    view_w = min(payload["w"] * 3, (page_width - 14 * (n - 1)) // n)
    return int(view_w * payload["h"] / payload["w"]) + 330


def player_html(payload: dict, zoom: int = 520) -> str:
    data = json.dumps(payload).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", data).replace("__ZOOM__", str(int(zoom)))


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
:root{--fg:#1f2937;--muted:#6b7280;--line:#d1d5db;--panel:#f3f4f6;--accent:#ff4b4b}
@media (prefers-color-scheme: dark){:root{--fg:#e5e7eb;--muted:#9ca3af;--line:#374151;--panel:#1f2937}}
html,body{margin:0;color:var(--fg);font:14px/1.4 "Source Sans Pro",system-ui,sans-serif;background:transparent}
#views{display:flex;gap:14px;flex-wrap:wrap;justify-content:center;margin-top:4px}
.view{text-align:center}.view .t{font-weight:600;margin-bottom:4px}
.view canvas{image-rendering:pixelated;border:1px solid var(--line);border-radius:4px;background:#000;display:block}
.bar{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;justify-content:center;margin-top:10px}
.panel{background:var(--panel);border-radius:8px;padding:8px 12px}
button,select,input[type=range]{font:inherit}
button{border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}button.main{background:var(--accent);color:#fff;border-color:var(--accent)}
select{border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;padding:3px 6px}
label{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px}
#stats{text-align:center;margin-top:8px;color:var(--muted);font-variant-numeric:tabular-nums}
#slider{width:min(560px,90vw)}
</style></head><body>
<div id="msg" style="text-align:center;padding:30px;color:var(--muted)">Cargando fotogramas…</div>
<div id="views"></div><div id="stats"></div><div id="ui" style="display:none">
<div class="bar"><button id="first" title="Primer fotograma">⏮</button><button id="prev" title="Anterior (←)">◀</button>
<button id="play" class="main" title="Reproducir / pausar (espacio)">▶ Reproducir</button><button id="next" title="Siguiente (→)">▶</button>
<button id="last" title="Último fotograma">⏭</button><input id="slider" type="range" min="0" value="0">
<label>Velocidad <select id="speed"><option>0.25</option><option>0.5</option><option selected>1</option><option>2</option><option>4</option></select>×</label>
<label><input id="loop" type="checkbox" checked>Bucle</label>
<label>Tamaño <input id="zoom" type="range" min="200" max="900" step="10" value="__ZOOM__" style="width:110px"></label></div>
<div class="bar panel" style="margin-left:auto;margin-right:auto;width:fit-content">
<label><input id="showT" type="checkbox"><span class="sw" style="background:#2a78d6"></span>Máscara real</label>
<label><input id="showP" type="checkbox"><span class="sw" style="background:#eb6834"></span>Máscara detectada</label>
<label>Estilo <select id="style"><option value="contour">Contorno</option><option value="fill">Relleno</option><option value="error">Acierto / error</option></select></label>
<label>Opacidad <input id="opacity" type="range" min="0.1" max="1" step="0.05" value="0.9" style="width:90px"></label>
<label>Grosor <select id="thick"><option>1</option><option>2</option><option>3</option></select></label>
<label><span class="sw" id="tpsw" style="background:#2ea043"></span><span id="errhint" style="color:var(--muted)">verde acierto · naranja falso positivo · azul falso negativo</span></label></div>
<div class="bar"><label>Escala de exportación <select id="escale"><option>1</option><option>2</option><option selected>3</option><option>4</option></select>×</label>
<button id="save">⬇ Descargar fotograma (PNG)</button></div></div>
<script>
const D=__DATA__, W=D.w, H=D.h, N=D.n, PX=W*H;
const $=id=>document.getElementById(id);
const S={i:0,playing:false,last:0,showT:false,showP:false,style:'contour',opacity:0.9,thick:1,speed:1,loop:true};
let views=[],maskT=null,maskP=null,ctxs=[],ov=document.createElement('canvas'),octx,ovKey='',edgeCache=new Map();
ov.width=W;ov.height=H;octx=ov.getContext('2d');
const COL={T:[42,120,214],P:[235,104,52],TP:[46,160,67]};
function load(src){return new Promise((ok,ko)=>{const im=new Image();im.onload=()=>ok(im);im.onerror=ko;im.src=src;});}
async function masks(urls){
  const cv=document.createElement('canvas');cv.width=W;cv.height=H;const cx=cv.getContext('2d',{willReadFrequently:true});const out=[];
  for(const u of urls){const im=await load(u);cx.clearRect(0,0,W,H);cx.drawImage(im,0,0);
    const d=cx.getImageData(0,0,W,H).data,m=new Uint8Array(PX);for(let k=0;k<PX;k++)m[k]=d[4*k]>127?1:0;out.push(m);}
  return out;}
function edges(m,key,t){
  const ck=key+'|'+t;if(edgeCache.has(ck))return edgeCache.get(ck);
  let e=new Uint8Array(PX);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){const k=y*W+x;if(!m[k])continue;
    if(x===0||y===0||x===W-1||y===H-1||!m[k-1]||!m[k+1]||!m[k-W]||!m[k+W])e[k]=1;}
  for(let r=1;r<t;r++){const e2=e.slice();for(let y=0;y<H;y++)for(let x=0;x<W;x++){const k=y*W+x;if(!e[k])continue;
    if(x>0)e2[k-1]=1;if(x<W-1)e2[k+1]=1;if(y>0)e2[k-W]=1;if(y<H-1)e2[k+W]=1;}e=e2;}
  if(edgeCache.size>400)edgeCache.clear();edgeCache.set(ck,e);return e;}
function overlay(i){
  const key=[i,S.style,S.showT,S.showP,S.opacity,S.thick].join('|');if(key===ovKey)return;ovKey=key;
  const img=octx.createImageData(W,H),d=img.data,a=Math.round(255*S.opacity);
  const put=(k,c,al)=>{const o=4*k;d[o]=c[0];d[o+1]=c[1];d[o+2]=c[2];d[o+3]=al;};
  const T=maskT?maskT[i]:null,P=maskP?maskP[i]:null;
  if(S.style==='contour'){
    if(S.showT&&T){const e=edges(T,'T'+i,S.thick);for(let k=0;k<PX;k++)if(e[k])put(k,COL.T,a);}
    if(S.showP&&P){const e=edges(P,'P'+i,S.thick);for(let k=0;k<PX;k++)if(e[k])put(k,COL.P,a);}
  }else if(S.style==='fill'){
    const al=Math.round(a*0.45);
    if(S.showT&&T)for(let k=0;k<PX;k++)if(T[k])put(k,COL.T,al);
    if(S.showP&&P)for(let k=0;k<PX;k++)if(P[k])put(k,COL.P,al);
  }else if(T&&P){const al=Math.round(a*0.4);
    for(let k=0;k<PX;k++){if(T[k]&&P[k])put(k,COL.TP,al);else if(P[k])put(k,COL.P,al);else if(T[k])put(k,COL.T,al);}}
  octx.putImageData(img,0,0);}
function draw(){
  const i=S.i;overlay(i);
  views.forEach((v,j)=>{const c=ctxs[j];c.drawImage(v.imgs[i],0,0);c.drawImage(ov,0,0);});
  $('slider').value=i;
  const t=D.idx[i]/D.fps;let s=`Fotograma ${D.idx[i]+1}/${D.idx[N-1]+1} · t = ${t.toFixed(2)} s`+(t<D.learn?' · el detector aprende el fondo':(t<D.leakOn?' · sin gas':' · fuga abierta'));
  const T=maskT?maskT[i]:null,P=maskP?maskP[i]:null;
  if(T||P){let nt=0,np=0,tp=0;for(let k=0;k<PX;k++){const a=T&&T[k],b=P&&P[k];if(a)nt++;if(b)np++;if(a&&b)tp++;}
    s+=` · real: ${T?nt:'—'} px · detectada: ${P?np:'—'} px`+(T&&P?` · IoU ${(nt+np-tp>0?tp/(nt+np-tp):NaN).toFixed(2)}`:'');}
  $('stats').textContent=s;}
function step(d){S.i=(S.i+d+N)%N;draw();}
function setPlaying(p){S.playing=p;$('play').textContent=p?'⏸ Pausar':'▶ Reproducir';S.last=performance.now();if(p)requestAnimationFrame(tick);}
function tick(now){if(!S.playing)return;const dt=1000*D.stride/D.fps/S.speed;
  if(now-S.last>=dt){const k=Math.floor((now-S.last)/dt);S.last+=k*dt;
    if(S.i+k>=N){if(S.loop){S.i=(S.i+k)%N;}else{S.i=N-1;setPlaying(false);}}else S.i+=k;draw();}
  requestAnimationFrame(tick);}
function zoom(){const z=+$('zoom').value;ctxs.forEach(c=>{c.canvas.style.width=Math.min(z,Math.floor(((window.innerWidth||1000)-20)/Math.max(1,views.length)-14))+'px';c.canvas.style.height='auto';});}
function save(){
  const s=+$('escale').value,gap=12*s,th=26*s,fh=24*s,vw=W*s,vh=H*s,n=views.length;
  const lg=[];if(S.style==='error')lg.push('verde: acierto · naranja: falso positivo · azul: falso negativo');else{if(S.showT)lg.push('azul: máscara real');if(S.showP)lg.push('naranja: máscara detectada');}
  const txt=$('stats').textContent.split(' · real')[0]+(lg.length?'   |   '+lg.join(' · '):'');
  const inner=n*vw+(n+1)*gap,m=document.createElement('canvas').getContext('2d');m.font=`${12*s}px sans-serif`;
  const cw=Math.max(inner,Math.ceil(m.measureText(txt).width)+2*gap),off=(cw-inner)/2;
  const c=document.createElement('canvas');c.width=cw;c.height=th+vh+fh+gap;
  const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);x.imageSmoothingEnabled=false;
  x.fillStyle='#111';x.font=`600 ${14*s}px sans-serif`;x.textAlign='center';
  views.forEach((v,j)=>{const x0=off+gap+j*(vw+gap);x.fillText(v.name,x0+vw/2,th*0.72);x.drawImage(ctxs[j].canvas,x0,th,vw,vh);});
  x.font=`${12*s}px sans-serif`;x.textAlign='left';x.fillStyle='#444';
  x.fillText(txt,gap,th+vh+fh*0.85);
  c.toBlob(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=`fotograma_${String(D.idx[S.i]+1).padStart(4,'0')}.png`;
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);},'image/png');}
(async function(){
  try{
    for(const v of D.views)views.push({name:v.name,imgs:await Promise.all(v.frames.map(load))});
    if(D.truth)maskT=await masks(D.truth);if(D.pred)maskP=await masks(D.pred);
    const box=$('views');
    views.forEach(v=>{const d=document.createElement('div');d.className='view';d.innerHTML=`<div class="t">${v.name}</div>`;
      const c=document.createElement('canvas');c.width=W;c.height=H;d.appendChild(c);box.appendChild(d);ctxs.push(c.getContext('2d'));});
    $('slider').max=N-1;
    S.showT=!!maskT;S.showP=!!maskP;$('showT').checked=S.showT;$('showP').checked=S.showP;
    $('showT').disabled=!maskT;$('showP').disabled=!maskP;
    $('msg').remove();$('ui').style.display='block';zoom();draw();
    $('play').onclick=()=>setPlaying(!S.playing);$('prev').onclick=()=>{setPlaying(false);step(-1);};$('next').onclick=()=>{setPlaying(false);step(1);};
    $('first').onclick=()=>{setPlaying(false);S.i=0;draw();};$('last').onclick=()=>{setPlaying(false);S.i=N-1;draw();};
    $('slider').oninput=e=>{S.i=+e.target.value;draw();};
    $('speed').onchange=e=>S.speed=+e.target.value;$('loop').onchange=e=>S.loop=e.target.checked;
    $('showT').onchange=e=>{S.showT=e.target.checked;draw();};$('showP').onchange=e=>{S.showP=e.target.checked;draw();};
    $('style').onchange=e=>{S.style=e.target.value;draw();};$('opacity').oninput=e=>{S.opacity=+e.target.value;draw();};
    $('thick').onchange=e=>{S.thick=+e.target.value;draw();};$('zoom').oninput=zoom;$('save').onclick=save;
    window.addEventListener('resize',zoom);
    document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();setPlaying(!S.playing);}
      else if(e.code==='ArrowRight'){setPlaying(false);step(1);}else if(e.code==='ArrowLeft'){setPlaying(false);step(-1);}});
  }catch(err){document.getElementById('msg').textContent='No se pudo cargar el reproductor: '+err;}
})();
</script></body></html>"""
