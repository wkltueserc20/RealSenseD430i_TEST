/* ============================================================
 * demo.js — PAGE 模擬模式(無實體相機 / 無後端)
 *
 * 真實照片當背景 + canvas 疊偵測圖形,看起來像真的相機在偵測。
 *   📐 量測 → 物體照片 + 包圍盒 + 尺寸
 *   📦 品檢 → 零件照片 + 參考框 + OK/NG
 *   🙂 人臉 → 4 張真人輪播 + 名字框(含「未知」)
 *   🚧 障礙 → 走廊照片(遠=安全 / 近=警報)+ 範圍框
 *   👁 監看 → 站姿照片 + 骨架 / 手勢照片 + 手勢框
 *   🌈 深度 → 彩色深度(用畫的)
 * 仍是假相機+假後端 → 右側真實 UI 面板照常連動。
 * 自動導覽:閒置輪播(點真 UI 模式鈕);操作即暫停。
 *
 * 啟用：*.github.io、file://、或 ?demo=1；本機有後端 ?demo=0 關閉。
 * ============================================================ */
(function(){
  const q = new URLSearchParams(location.search).get("demo");
  const DEMO = q === "1" ||
    (q !== "0" && (/github\.io$/i.test(location.hostname) || location.protocol === "file:"));
  if (!DEMO) return;

  const W = 800;
  const feed = document.getElementById("feed");
  const cv = document.createElement("canvas"); cv.width = cv.height = W;
  const g = cv.getContext("2d");
  const now = () => performance.now();
  const S = { cfg:{}, regions:[], faces:{"小明":7,"Aki":6,"David":5}, msg:"", t:0 };

  const L = src => { const i=new Image(); i.src=src; return i; };
  // 每張照片附「主體在照片內的相對框 [fx0,fy0,fx1,fy1]」→ 疊圖才對得準
  const IMG = {
    faces:[
      {im:L("demo/face1.png"), name:"小明", box:[0.27,0.10,0.73,0.66]},
      {im:L("demo/face2.png"), name:"Aki",  box:[0.28,0.06,0.74,0.80]},
      {im:L("demo/face3.png"), name:"David",box:[0.22,0.10,0.78,0.66]},
      {im:L("demo/face4.png"), name:"未知", box:[0.22,0.10,0.80,0.66], unknown:true},
    ],
    measure:[
      {im:L("demo/m_box.png"),    box:[0.17,0.21,0.85,0.76], dims:[24.3,18.0,16.1]},
      {im:L("demo/m_bottle.png"), box:[0.39,0.16,0.61,0.86], dims:[6.5,6.5,21.5]},
      {im:L("demo/m_mug.png"),    box:[0.22,0.26,0.74,0.78], dims:[9.6,8.2,9.8]},
    ],
    inspect:[
      {im:L("demo/i_pcb.png"),  box:[0.09,0.10,0.93,0.92]},
      {im:L("demo/i_part.png"), box:[0.27,0.27,0.73,0.80]},
    ],
    obstacle:[
      {im:L("demo/o_safe.png"),  person:[0.46,0.44,0.54,0.64], near:false},
      {im:L("demo/o_alert.png"), person:[0.24,0.05,0.79,0.98], near:true},
    ],
    pose:[   // kp = 真實 MediaPipe 偵測座標(normalized) → 精準對齊
      {im:L("demo/s_stand.png"), kp:{head:[.505,.213],neck:[.505,.305],ls:[.662,.304],rs:[.347,.306],
        le:[.706,.417],re:[.294,.422],lw:[.732,.521],rw:[.263,.523],hc:[.501,.529],lh:[.589,.528],rh:[.413,.53],
        lk:[.587,.702],rk:[.393,.698],la:[.596,.858],ra:[.387,.856]}},
      {im:L("demo/s_gesture.png"), hand:[0.10,0.26,0.32,0.58], gesture:true},
      {im:L("demo/s_fall.png"), fallen:true, kp:{head:[.252,.326],neck:[.308,.380],ls:[.343,.325],rs:[.272,.434],
        le:[.434,.351],re:[.297,.533],lw:[.52,.333],rw:[.305,.655],hc:[.465,.469],lh:[.477,.435],rh:[.452,.502],
        lk:[.614,.449],rk:[.562,.65],la:[.757,.526],ra:[.689,.811]}},
    ],
  };

  const jet = t => { t=Math.max(0,Math.min(1,t));
    const r=Math.max(0,Math.min(1,1.5-Math.abs(4*t-3))),gg=Math.max(0,Math.min(1,1.5-Math.abs(4*t-2))),
          b=Math.max(0,Math.min(1,1.5-Math.abs(4*t-1))); return `rgb(${r*255|0},${gg*255|0},${b*255|0})`; };
  const line=(a,b,c,d)=>{ g.beginPath(); g.moveTo(a,b); g.lineTo(c,d); g.stroke(); };
  const pick = list => list[Math.floor(S.t/5)%list.length];
  function drawPhoto(im){
    g.fillStyle="#0c1014"; g.fillRect(0,0,W,W);
    if(!im.complete||!im.naturalWidth) return {x:0,y:0,w:W,h:W};
    const ar=im.naturalWidth/im.naturalHeight; let w=W,h=W/ar; if(h>W){h=W;w=W*ar;}
    const x=(W-w)/2,y=(W-h)/2; g.drawImage(im,x,y,w,h); return {x,y,w,h};
  }
  const fr=(r,a,b,c,d)=>[r.x+a*r.w, r.y+b*r.h, (c-a)*r.w, (d-b)*r.h];
  function tag(x,y,text,col,big){
    g.font=(big?"700 22px":"600 16px")+" 'Noto Sans TC',sans-serif";
    const w=g.measureText(text).width+18;
    g.fillStyle="rgba(8,12,18,0.82)"; g.fillRect(x,y-23,w,29);
    g.fillStyle=col; g.fillText(text,x+9,y-3);
  }

  // ---------- 合成數值(畫面與 /status 共用) ----------
  const distNow = () => +(0.55+0.06*Math.sin(S.t*0.8)).toFixed(2);
  const curMeasure = () => pick(IMG.measure);
  const measureVals = () => { const d=curMeasure().dims;
    return { length:d[0]/100, width:d[2]/100, height:d[1]/100, tilt:2, dist:distNow(), n_pts:2100 }; };
  const inspectOK = () => Math.floor(S.t/4.5)%2===0;
  const inspectVals = () => { const ok=inspectOK();
    return { verdict:ok?"OK":"NG", ng:!ok, score: ok?Math.round(95+2*Math.sin(S.t*3)):Math.round(48+5*Math.sin(S.t*3)), skew:ok?2:13, oob:!ok, reason:"" }; };
  const faceMatching = () => (S.t%5)<1.0;
  const curFace = () => pick(IMG.faces);
  const faceSim = () => +(0.69+0.06*Math.sin(S.t*2)).toFixed(2);
  const gestureNow = () => ({g:"張開手掌", n:5});
  const curObst = () => pick(IMG.obstacle);

  // ---------- 場景 ----------
  function sceneMeasure(){
    const e=curMeasure(), r=drawPhoto(e.im), b=fr(r,e.box[0],e.box[1],e.box[2],e.box[3]);
    const mp=S.t%5, scanning=mp<1.6;
    if(scanning){ const sy=b[1]+b[3]*(mp/1.6);
      g.strokeStyle="#27e0ff"; g.lineWidth=3; line(b[0]-8,sy,b[0]+b[2]+8,sy);
      g.fillStyle="rgba(39,224,255,.14)"; g.fillRect(b[0]-8,b[1],b[2]+16,sy-b[1]);
      return "📐 物體量測 · 掃描中… 用深度重建物體輪廓"; }
    g.setLineDash([7,5]); g.strokeStyle="#36d399"; g.lineWidth=2.5; g.strokeRect(b[0],b[1],b[2],b[3]); g.setLineDash([]);
    g.strokeStyle="#9fe8c0"; g.fillStyle="#9fe8c0"; g.lineWidth=1.5; g.font="600 15px 'IBM Plex Mono',monospace";
    line(b[0],b[1]+b[3]+16,b[0]+b[2],b[1]+b[3]+16); g.fillText(e.dims[0].toFixed(1)+" cm",b[0]+b[2]*0.32,b[1]+b[3]+36);
    line(b[0]+b[2]+16,b[1],b[0]+b[2]+16,b[1]+b[3]); g.save(); g.translate(b[0]+b[2]+34,b[1]+b[3]*0.55); g.rotate(-Math.PI/2); g.fillText(e.dims[1].toFixed(1)+" cm",0,0); g.restore();
    tag(b[0], b[1]-6, "距離 "+distNow().toFixed(2)+" m", "#27e0ff");
    return `📐 物體量測 · ${e.dims[0]}×${e.dims[2]}×${e.dims[1]} cm,距離 ${distNow().toFixed(2)} m`;
  }
  function sceneInspect(){
    const e=curInspect(), r=drawPhoto(e.im), b=fr(r,e.box[0],e.box[1],e.box[2],e.box[3]);
    const ok=inspectOK(), v=inspectVals(), sh=ok?0:b[2]*0.06;
    g.setLineDash([7,6]); g.lineWidth=2; g.strokeStyle="#88ffd0aa"; g.strokeRect(b[0],b[1],b[2],b[3]); g.setLineDash([]);
    g.lineWidth=4; g.strokeStyle=ok?"#33dd66":"#ff5a48"; g.strokeRect(b[0]-6+sh,b[1]-6+sh,b[2]+12,b[3]+12);
    tag(b[0]-6, b[1]-12, (ok?"✓ OK ":"✗ NG ")+v.score, ok?"#33dd66":"#ff7a6a", true);
    return ok?"📦 定位品檢 · 位置正確 → 合格(OK)":`📦 定位品檢 · 物體移位/歪斜 ${v.skew}° → 不合格(NG)`;
  }
  const curInspect = () => pick(IMG.inspect);
  function sceneFace(){
    const e=curFace(), r=drawPhoto(e.im), b=fr(r,e.box[0],e.box[1],e.box[2],e.box[3]);
    const matching=faceMatching(), known=!e.unknown && !matching;
    const col=matching?"#ffd23c":(e.unknown?"#ffaa3c":"#33dd66");
    g.lineWidth=3; g.strokeStyle=col; g.strokeRect(b[0],b[1],b[2],b[3]);
    tag(b[0], b[1]-6, matching?"比對中…":(e.unknown?"未知":`${e.name}  ${faceSim()}`), col, true);
    return matching?"🙂 人臉辨識 · 比對特徵中…":(e.unknown?"🙂 人臉辨識 · 陌生人 → 標記「未知」":`🙂 人臉辨識 · 認出「${e.name}」(相似度 ${faceSim()})`);
  }
  function sceneObstacle(){
    const e=curObst(), r=drawPhoto(e.im);
    const zone=[r.x+r.w*0.18, r.y+r.h*0.40, r.w*0.64, r.h*0.58];   // 偵測範圍(畫面下半中央)
    g.fillStyle=e.near?"rgba(255,90,72,.20)":"rgba(74,222,128,.14)";
    g.fillRect(zone[0],zone[1],zone[2],zone[3]);
    g.lineWidth=3; g.strokeStyle=e.near?"#ff5a48":"#36d399"; g.strokeRect(zone[0],zone[1],zone[2],zone[3]);
    g.fillStyle=e.near?"#ff8a7a":"#9fe8c0"; g.font="600 15px 'IBM Plex Mono',monospace"; g.fillText("檢測範圍",zone[0]+8,zone[1]+22);
    const p=fr(r,e.person[0],e.person[1],e.person[2],e.person[3]);
    g.lineWidth=3; g.strokeStyle=e.near?"#ff5a48":"#ffd23c"; g.strokeRect(p[0],p[1],p[2],p[3]);
    if(e.near) tag(p[0], p[1]-6, "⚠ 障礙 0.6 m", "#ff7a6a", true);
    return e.near?"🚧 障礙物檢測 · 物體進入範圍!0.6 m → 警報":"🚧 障礙物檢測 · 範圍內無障礙 · 安全";
  }
  const curPose = () => IMG.pose[Math.floor(S.t/5)%IMG.pose.length];
  function scenePose(depth){
    if(depth) return depthView();
    const e=curPose(), r=drawPhoto(e.im);
    if(e.gesture){ const h=fr(r,e.hand[0],e.hand[1],e.hand[2],e.hand[3]);
      g.lineWidth=3; g.strokeStyle="#ff7a1a"; g.strokeRect(h[0],h[1],h[2],h[3]);
      const s=gestureNow(); tag(h[0],h[1]-6,"✋ "+s.g+" · "+s.n+"指","#ff9a3c",true);
      return "👁 監看 · 手勢辨識(可控制機器人)"; }
    const P={}; for(const k in e.kp){ P[k]=[r.x+e.kp[k][0]*r.w, r.y+e.kp[k][1]*r.h]; }
    g.strokeStyle=e.fallen?"#ff5a48":"#3df58a"; g.lineWidth=4; g.lineCap="round";
    for(const[a,b] of [["head","neck"],["neck","ls"],["neck","rs"],["ls","le"],["le","lw"],["rs","re"],["re","rw"],
      ["neck","hc"],["hc","lh"],["hc","rh"],["lh","lk"],["lk","la"],["rh","rk"],["rk","ra"]]) line(P[a][0],P[a][1],P[b][0],P[b][1]);
    g.fillStyle="#ffd23c"; for(const k in P){ g.beginPath(); g.arc(P[k][0],P[k][1],5,0,7); g.fill(); }
    if(e.fallen){ tag(20,42,"⚠ 偵測到跌倒 / 躺下","#ff7a6a",true); return "👁 監看 · ⚠ 偵測到跌倒 → 觸發警報視窗"; }
    return "👁 監看 · 即時人體骨架追蹤(跌倒會自動警報)";
  }
  // 真實深度視覺化:把真實照片轉成 turbo 深度色圖(主體近=暖、背景遠=冷)
  const jetRGB = t => { t=Math.max(0,Math.min(1,t));
    return [Math.max(0,Math.min(1,1.5-Math.abs(4*t-3)))*255|0,
            Math.max(0,Math.min(1,1.5-Math.abs(4*t-2)))*255|0,
            Math.max(0,Math.min(1,1.5-Math.abs(4*t-1)))*255|0]; };
  const DEPTH=[IMG.obstacle[0].im, IMG.pose[0].im]; const depthCache={};
  function buildDepth(im,key){
    const sm=180, oc=document.createElement("canvas"); oc.width=oc.height=sm; const og=oc.getContext("2d");
    const ar=im.naturalWidth/im.naturalHeight; let w=sm,h=sm/ar; if(h>sm){h=sm;w=sm*ar;}
    const dx=(sm-w)/2, dy=(sm-h)/2;
    og.fillStyle="#000"; og.fillRect(0,0,sm,sm); og.drawImage(im,dx,dy,w,h);
    const id=og.getImageData(0,0,sm,sm), p=id.data;
    for(let j=0;j<sm;j++) for(let i=0;i<sm;i++){ const k=(j*sm+i)*4; let dep;
      if(i<dx||i>dx+w||j<dy||j>dy+h) dep=0.04;     // 補邊→遠
      else { const lum=(0.299*p[k]+0.587*p[k+1]+0.114*p[k+2])/255;
        const cx=(i-sm/2)/(sm/2), cy=(j-sm/2)/(sm/2), cen=1-Math.min(1,Math.hypot(cx,cy));
        dep=0.5*(1-lum)+0.32*cen+0.18*(j/sm); }
      const c=jetRGB(Math.max(0,Math.min(1,dep))); p[k]=c[0]; p[k+1]=c[1]; p[k+2]=c[2]; }
    og.putImageData(id,0,0); depthCache[key]=oc;
  }
  function depthView(){
    const idx=Math.floor(S.t/5)%DEPTH.length, im=DEPTH[idx];
    if(!depthCache[idx]){
      if(im.complete&&im.naturalWidth) buildDepth(im,idx);
      else { g.fillStyle="#06121f"; g.fillRect(0,0,W,W); return "🌈 深度圖 · 載入中…"; } }
    g.imageSmoothingEnabled=false; g.fillStyle="#000"; g.fillRect(0,0,W,W);
    g.drawImage(depthCache[idx],0,0,W,W); g.imageSmoothingEnabled=true;
    return "🌈 深度圖 · 近紅遠藍(真實深度視覺化,可做距離/活體偵測)";
  }
  function sceneIdle(){ g.fillStyle="#0e141b"; g.fillRect(0,0,W,W);
    g.fillStyle="#9fb0c4"; g.font="600 22px 'Noto Sans TC',sans-serif"; g.textAlign="center";
    g.fillText("← 點右側功能來體驗",W/2,W/2-10); g.fillText("或靜置看自動導覽",W/2,W/2+24); g.textAlign="left";
    return ""; }

  // ---------- 字幕 / 角標 ----------
  function captionBar(text){ if(!text) return;
    g.fillStyle="rgba(8,11,16,0.74)"; g.fillRect(0,W-46,W,46);
    g.fillStyle="#eaf1ff"; g.font="600 19px 'Noto Sans TC',sans-serif"; g.textBaseline="middle";
    g.fillText(text,18,W-23); g.textBaseline="alphabetic"; }
  function topTags(){
    g.fillStyle="rgba(255,210,150,0.92)"; g.font="600 18px 'Noto Sans TC',sans-serif"; g.fillText("◉ DEMO 模擬畫面 · 無實體相機",16,30);
    if(touring()){ g.fillStyle="rgba(120,200,255,0.95)"; g.font="600 16px 'Noto Sans TC',sans-serif"; g.fillText("▶ 自動導覽中(操作即暫停)",16,52); } }

  function render(){
    S.t+=0.016; const c=S.cfg; let cap;
    if(c.depth) cap=scenePose(true);
    else if(c.measure) cap=sceneMeasure();
    else if(c.inspect) cap=sceneInspect();
    else if(c.face) cap=sceneFace();
    else if(c.obstacle) cap=sceneObstacle();
    else if(c.pose||c.hands) cap=scenePose(false);
    else cap=sceneIdle();
    topTags(); captionBar(cap);
    feed.src=cv.toDataURL("image/jpeg",0.75);
    setTimeout(render,100);
  }

  // ---------- 合成 /status ----------
  function buildStatus(){
    const c=S.cfg, dist=distNow();
    const st={ person:true, dist, fingers:0, gesture:"-", finger_states:[0,0,0,0,0].map(Boolean),
      fallen:false, tilt:0, roll:+(Math.sin(S.t*0.5)*2).toFixed(1), measure:null, picked:false,
      has_template:false, detect:false, n_views:0, matched:false, match_via:"", inspect:null, msg:S.msg };
    if(c.hands){ const s=gestureNow(); st.gesture=s.g; st.fingers=s.n; }
    if((c.pose||c.hands) && curPose().fallen && !touring()){ st.fallen=true; st.tilt=78; }  // 跌倒→警報(導覽時不彈窗以免擋畫面)
    if(c.measure) st.measure=measureVals();
    if(c.inspect) st.inspect=inspectVals();
    const names=Object.keys(S.faces); st.faces_db=names.map(n=>({name:n,samples:S.faces[n]}));
    if(c.face){ const e=curFace(), matching=faceMatching();
      st.faces=[{name: matching?"未知":(e.unknown?"未知":e.name), score: matching||e.unknown?0.12:faceSim(), box:[0,0,0,0]}]; } else st.faces=[];
    st.face_engine=c.face_engine||"lbph"; st.arc_available=true;
    const thr=c.obstacle_dist||1.0;
    if(c.obstacle){ if(S.regions.length){ st.obstacle={on:true,hit:false,dist:2.5,thr,regions:S.regions,per:S.regions.map(()=>({hit:false,dist:2.5}))}; }
      else { const e=curObst(); st.obstacle={on:true,hit:e.near,dist:e.near?0.6:2.4,thr,regions:[],per:[]}; } }
    else st.obstacle={on:false,hit:false,dist:0,thr,regions:S.regions,per:[]};
    return st;
  }
  function evalResp(){ const nm=Object.keys(S.faces);
    const intra=nm.map(n=>({name:n,n:Math.max(0,(S.faces[n]*(S.faces[n]-1))/2|0),min:0.55,mean:0.7,max:0.83}));
    const inter=[]; for(let i=0;i<nm.length;i++) for(let j=i+1;j<nm.length;j++) inter.push({a:nm[i],b:nm[j],mean:0.09,max:0.17});
    const sep=nm.length>=2?{intra_min:0.55,inter_max:0.17,gap:0.38,separable:true,suggested:0.35,acc:1.0}:null;
    return {available:true,default_thr:0.35,people:nm.map(n=>({name:n,samples:S.faces[n]})),intra,
      intra_all:nm.length?{n:1,min:0.55,mean:0.7,max:0.83}:null,inter,inter_all:inter.length?{n:1,min:0.04,mean:0.09,max:0.17}:null,separation:sep}; }

  // ---------- 攔截 fetch ----------
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  const jsonResp = o => new Response(JSON.stringify(o),{status:200,headers:{"Content-Type":"application/json"}});
  window.fetch = function(url,opt){
    try{
      const u=(typeof url==="string")?url:(url&&url.url)||"";
      const path=u.replace(location.origin,"").split("?")[0], m=((opt&&opt.method)||"GET").toUpperCase();
      if(path==="/status")  return Promise.resolve(jsonResp(buildStatus()));
      if(path==="/measure") return Promise.resolve(jsonResp(S.cfg.measure?measureVals():{}));
      if(path==="/face/eval") return Promise.resolve(jsonResp(evalResp()));
      if(path==="/pointcloud.bin") return Promise.resolve(new Response(new Uint8Array(4).buffer,{status:200}));
      if(m==="POST"){ let b={}; try{ b=JSON.parse((opt&&opt.body)||"{}"); }catch(e){}
        if(path==="/config") Object.assign(S.cfg,b);
        else if(path==="/obstacle/regions") S.regions=b.regions||[];
        else if(path==="/obstacle/clear") S.regions=[];
        else if(path==="/face/enroll"){ const n=(b.name||"").trim(); if(n){ S.cfg.face=true; S.faces[n]=(S.faces[n]||0)+1; S.msg=`已擷取「${n}」第 ${S.faces[n]} 張樣本（DEMO）`; } }
        else if(path==="/face/delete") delete S.faces[(b.name||"").trim()];
        else if(path==="/face/clear") S.faces={};
        else if(path==="/face/rename"){ const o=(b.old||"").trim(),n=(b.new||"").trim(); if(o&&n&&S.faces[o]!=null){ S.faces[n]=(S.faces[n]||0)+S.faces[o]; delete S.faces[o]; S.msg=`已改名為「${n}」（DEMO）`; } }
        return Promise.resolve(jsonResp({ok:true})); }
      return Promise.resolve(jsonResp({ok:true}));
    }catch(e){ return realFetch?realFetch(url,opt):Promise.reject(e); }
  };

  // ---------- 自動導覽 ----------
  let lastUser=now(), step=0;
  ["pointerdown","keydown","wheel","touchstart"].forEach(ev=>document.addEventListener(ev,()=>{lastUser=now();},{passive:true,capture:true}));
  const touring=()=>now()-lastUser>12000;
  const clickMode=mm=>{ const el=document.querySelector('.mode[data-mode="'+mm+'"]'); if(el) el.click(); };
  const setDepth=w=>{ const sw=document.querySelector('.sw[data-k="depth"]'); if(sw&&sw.classList.contains("on")!==w) sw.click(); };
  const STEPS=[{mode:"measure",depth:false},{mode:"inspect",depth:false},{mode:"obstacle",depth:false},
               {mode:"face",depth:false},{mode:"watch",depth:false},{mode:"watch",depth:true}];
  setInterval(()=>{ if(!touring()) return; const s=STEPS[step++%STEPS.length]; setDepth(s.depth); clickMode(s.mode); }, 3000);
  // 跌倒警報視窗演示後自動關閉(避免擋住導覽)
  setInterval(()=>{ const f=document.getElementById("fall"); if(!f) return;
    if(f.classList.contains("show")){ if(!f._since) f._since=now(); if(now()-f._since>4500){ f.classList.remove("show"); f._since=0; } }
    else f._since=0; }, 500);

  const badge=document.createElement("div");
  badge.textContent="● DEMO 模擬資料（無後端）";
  badge.style.cssText="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:400;font:600 12px 'Noto Sans TC',sans-serif;color:#140f08;background:#ff9a3c;padding:5px 14px;border-radius:999px;box-shadow:0 4px 16px #0006;letter-spacing:.04em";
  document.body.appendChild(badge);

  render();
  console.log("[demo] 真實照片 + 疊圖場景 + 自動導覽");
})();
