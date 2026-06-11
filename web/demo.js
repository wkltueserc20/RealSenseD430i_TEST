/* ============================================================
 * demo.js — PAGE 模擬模式(無實體相機 / 無後端)
 *
 * 在 GitHub Pages 上完整 DEMO：動畫假畫面 + 攔截 API 回合成資料。
 *   • 逼真一點的「人物」：上色臉孔(膚色/髮型/衣服皆不同 → 多人辨識)
 *   • 每個模式都有戲：量測/品檢/監看(骨架疊圖)/障礙/人臉
 *   • 深度視覺化：開「深度圖」→ 彩色深度(近紅遠藍)
 *   • 字幕：底部說明目前展示的功能
 *   • 自動導覽：閒置時自動輪播(點真 UI 的模式鈕驅動);操作即暫停
 *
 * 啟用：*.github.io、file://、或 ?demo=1；本機有後端時 ?demo=0 關閉。
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

  const S = { cfg:{}, regions:[], faces:{"小明":6, "Aki":5}, msg:"", t:0 };

  // 不同長相(膚色/髮色/髮型/衣服) → 多人辨識看得出差異
  const LOOKS = [
    {skin:"#e7b48b", hair:"#2f2018", shirt:"#3f6fb0", style:"short"},
    {skin:"#caa06e", hair:"#141414", shirt:"#b04f7a", style:"bun"},
    {skin:"#f1cba2", hair:"#6b4a28", shirt:"#3fae84", style:"long"},
    {skin:"#a87a55", hair:"#101010", shirt:"#c0843c", style:"cap"},   // 「未知」用
  ];
  const shade=(hex,a)=>{ const n=parseInt(hex.slice(1),16);
    const r=Math.max(0,Math.min(255,(n>>16)+a)), gc=Math.max(0,Math.min(255,((n>>8)&255)+a)),
          b=Math.max(0,Math.min(255,(n&255)+a)); return `rgb(${r},${gc},${b})`; };

  // ---------- 幾何 ----------
  function figure(){
    const cx = W*0.5 + Math.sin(S.t*0.5)*W*0.2;
    const cy = W*0.5 + Math.sin(S.t*2.0)*3;
    const R  = W*0.085;
    return { cx, cy, R, headY: cy-W*0.16, neckY: cy-W*0.085, shoY: cy-W*0.04,
             sh: W*0.11, hipY: cy+W*0.12, feetY: cy+W*0.3, swing: Math.sin(S.t*2.6) };
  }
  const headBox = f => [Math.round(f.cx-f.R), Math.round(f.headY-f.R*1.15),
                        Math.round(f.R*2), Math.round(f.R*2.3)];
  function pointIn(poly,x,y){ let inside=false;
    for(let i=0,j=poly.length-1;i<poly.length;j=i++){
      const xi=poly[i][0],yi=poly[i][1],xj=poly[j][0],yj=poly[j][1];
      if(((yi>y)!==(yj>y)) && (x<(xj-xi)*(y-yi)/((yj-yi)||1e-6)+xi)) inside=!inside; }
    return inside; }
  const jet = t => { t=Math.max(0,Math.min(1,t));
    const r=Math.max(0,Math.min(1,1.5-Math.abs(4*t-3)));
    const gg=Math.max(0,Math.min(1,1.5-Math.abs(4*t-2)));
    const b=Math.max(0,Math.min(1,1.5-Math.abs(4*t-1)));
    return `rgb(${r*255|0},${gg*255|0},${b*255|0})`; };
  const line=(a,b,c,d)=>{ g.beginPath(); g.moveTo(a,b); g.lineTo(c,d); g.stroke(); };

  // ---------- 背景 ----------
  function colorBg(){
    const bg=g.createLinearGradient(0,0,0,W);
    bg.addColorStop(0,"#1a232e"); bg.addColorStop(0.78,"#10161d"); bg.addColorStop(1,"#0c1014");
    g.fillStyle=bg; g.fillRect(0,0,W,W);
    g.strokeStyle="rgba(120,160,210,0.07)"; g.lineWidth=1;
    for(let x=0;x<W;x+=50) line(x,0,x,W);
    for(let y=0;y<W;y+=50) line(0,y,W,y);
    const fl=g.createLinearGradient(0,W*0.78,0,W);
    fl.addColorStop(0,"#283544"); fl.addColorStop(1,"#161d25");
    g.fillStyle=fl; g.fillRect(0,W*0.78,W,W*0.22);
  }
  function depthBg(){
    for(let y=0;y<W;y+=6){ g.fillStyle=jet(0.12+0.55*(y/W)); g.fillRect(0,y,W,6); }
    g.fillStyle="rgba(255,255,255,0.85)"; g.font="600 16px 'IBM Plex Mono',monospace";
    g.fillText("DEPTH 0.3–4.0 m", 18, W-58);
  }

  // ---------- 人物(上色臉孔) ----------
  function avatar(f, look, depth){
    const cx=f.cx, hy=f.headY, R=f.R;
    // 身體(衣服)
    g.fillStyle = depth ? jet(0.8) : look.shirt;
    g.beginPath();
    g.moveTo(cx-R*1.7, f.feetY);
    g.quadraticCurveTo(cx-R*1.85, hy+R*1.5, cx-R*0.75, hy+R*1.15);
    g.lineTo(cx+R*0.75, hy+R*1.15);
    g.quadraticCurveTo(cx+R*1.85, hy+R*1.5, cx+R*1.7, f.feetY);
    g.closePath(); g.fill();
    // 脖子
    g.fillStyle = depth ? jet(0.83) : shade(look.skin,-22);
    g.fillRect(cx-R*0.33, hy+R*0.55, R*0.66, R*0.7);
    if(depth){                              // 深度：暖色實心頭(近)
      g.fillStyle=jet(0.9); g.beginPath(); g.ellipse(cx,hy,R*0.95,R*1.12,0,0,7); g.fill();
      return;
    }
    // 耳朵
    g.fillStyle=shade(look.skin,-12);
    g.beginPath(); g.ellipse(cx-R*0.92,hy+R*0.05,R*0.17,R*0.27,0,0,7); g.fill();
    g.beginPath(); g.ellipse(cx+R*0.92,hy+R*0.05,R*0.17,R*0.27,0,0,7); g.fill();
    // 臉(徑向漸層 → 立體)
    const grd=g.createRadialGradient(cx-R*0.3,hy-R*0.35,R*0.2, cx,hy,R*1.25);
    grd.addColorStop(0,shade(look.skin,22)); grd.addColorStop(1,shade(look.skin,-22));
    g.fillStyle=grd; g.beginPath(); g.ellipse(cx,hy,R*0.92,R*1.1,0,0,7); g.fill();
    features(cx,hy,R,look);
    hair(cx,hy,R,look);
  }
  function features(cx,hy,R,look){
    const eo=R*0.4, ey=hy-R*0.05, ew=R*0.19, eh=R*0.12;
    const blink=(S.t%3.2)<0.13;
    // 眉
    g.strokeStyle=look.hair; g.lineWidth=R*0.08; g.lineCap="round";
    line(cx-eo-ew*0.7,hy-R*0.32, cx-eo+ew*0.7,hy-R*0.37);
    line(cx+eo-ew*0.7,hy-R*0.37, cx+eo+ew*0.7,hy-R*0.32);
    // 眼
    for(const sx of [-1,1]){ const ex=cx+sx*eo;
      if(blink){ g.strokeStyle="#5a4636"; g.lineWidth=R*0.05; line(ex-ew,ey,ex+ew,ey); }
      else{
        g.fillStyle="#fff"; g.beginPath(); g.ellipse(ex,ey,ew,eh,0,0,7); g.fill();
        g.fillStyle="#3a2a1a"; g.beginPath(); g.arc(ex+sx*R*0.02,ey,eh*0.72,0,7); g.fill();
        g.fillStyle="#000"; g.beginPath(); g.arc(ex+sx*R*0.02,ey,eh*0.34,0,7); g.fill();
        g.fillStyle="rgba(255,255,255,.85)"; g.beginPath(); g.arc(ex+sx*R*0.02-eh*0.2,ey-eh*0.2,eh*0.14,0,7); g.fill();
      }
    }
    // 鼻
    g.strokeStyle=shade(look.skin,-32); g.lineWidth=R*0.05;
    g.beginPath(); g.moveTo(cx,hy-R*0.02); g.lineTo(cx-R*0.1,hy+R*0.26);
    g.quadraticCurveTo(cx,hy+R*0.34, cx+R*0.08,hy+R*0.26); g.stroke();
    // 嘴(微笑)
    g.strokeStyle="#a4584a"; g.lineWidth=R*0.09;
    g.beginPath(); g.arc(cx,hy+R*0.42,R*0.34,0.16*Math.PI,0.84*Math.PI); g.stroke();
  }
  function hair(cx,hy,R,look){
    g.fillStyle=look.hair;
    if(look.style==="cap"){
      g.beginPath(); g.ellipse(cx,hy-R*0.45,R*1.0,R*0.65,0,Math.PI,2*Math.PI); g.fill();
      g.fillRect(cx-R*1.0,hy-R*0.5,R*2.0,R*0.14); return;
    }
    g.beginPath(); g.ellipse(cx,hy-R*0.5,R*0.98,R*0.7,0,Math.PI,2*Math.PI); g.fill();
    g.beginPath(); g.ellipse(cx,hy-R*0.42,R*0.98,R*0.45,0,Math.PI*0.9,Math.PI*2.1); g.fill();
    if(look.style==="long"){ g.fillRect(cx-R*0.98,hy-R*0.5,R*0.26,R*1.35); g.fillRect(cx+R*0.72,hy-R*0.5,R*0.26,R*1.35); }
    if(look.style==="bun"){ g.beginPath(); g.arc(cx,hy-R*1.05,R*0.3,0,7); g.fill(); }
  }
  function skeleton(f){          // 監看：在人物上疊「偵測到的骨架」
    const J={head:[f.cx,f.headY],neck:[f.cx,f.neckY],ls:[f.cx-f.sh,f.shoY],rs:[f.cx+f.sh,f.shoY],
      hip:[f.cx,f.hipY],lf:[f.cx-W*0.05,f.feetY],rf:[f.cx+W*0.05,f.feetY],
      le:[f.cx-f.sh*1.15,f.shoY+W*0.08+f.swing*16], re:[f.cx+f.sh*1.15,f.shoY+W*0.08-f.swing*16]};
    g.strokeStyle="#3df58a"; g.lineWidth=4; g.lineCap="round";
    for(const[a,b] of [["head","neck"],["neck","ls"],["neck","rs"],["ls","le"],["rs","re"],
                       ["neck","hip"],["hip","lf"],["hip","rf"]]) line(J[a][0],J[a][1],J[b][0],J[b][1]);
    g.fillStyle="#ff4e6a"; for(const k in J){ g.beginPath(); g.arc(J[k][0],J[k][1],5,0,7); g.fill(); }
  }

  // ---------- 合成數值 ----------
  const distNow = () => +(1.4+Math.sin(S.t*0.6)*0.6).toFixed(2);
  const measureVals = () => ({ length:0.12+0.02*Math.sin(S.t), width:0.08+0.015*Math.sin(S.t*1.3),
    height:0.05+0.01*Math.sin(S.t*0.7), tilt:Math.round(Math.sin(S.t)*8), dist:distNow(),
    n_pts:1800+Math.floor(500*Math.abs(Math.sin(S.t))) });
  function inspectVals(){ const score=Math.round(62+30*Math.sin(S.t*0.45));
    const thr=S.cfg.inspect_thr||60, ng=score<thr;
    return { verdict:ng?"NG":"OK", ng, score, reason:"", skew:Math.round(Math.abs(Math.sin(S.t*0.8))*12), oob:false }; }
  function gestureNow(){ const seq=[["PALM",5],["FIST",0],["ONE",1],["TWO",2],["OK",3]];
    const s=seq[Math.floor(S.t*0.6)%seq.length]; return {g:s[0], n:s[1]}; }
  function faceNow(){ const names=Object.keys(S.faces);
    const pool=names.length?names.concat(["未知"]):["未知"];
    const idx=Math.floor(S.t*0.16)%pool.length, name=pool[idx];
    return {name, known:name!=="未知", look: name==="未知"?LOOKS[3]:LOOKS[idx%LOOKS.length]}; }

  // ---------- 模式疊圖 ----------
  function drawMeasure(){
    const x=W*0.14,y=W*0.52,bw=W*0.2,bh=W*0.15, m=measureVals();
    g.strokeStyle="#36d399"; g.lineWidth=3; g.strokeRect(x,y,bw,bh);
    g.setLineDash([4,4]); g.strokeStyle="#36d39988"; g.strokeRect(x-7,y-7,bw+14,bh+14); g.setLineDash([]);
    g.fillStyle="#9fe8c0"; g.font="600 16px 'IBM Plex Mono',monospace";
    g.fillText(`${(m.length*100).toFixed(1)}×${(m.width*100).toFixed(1)}×${(m.height*100).toFixed(1)}cm`, x-6, y-16);
  }
  function drawInspect(){
    const ins=inspectVals(), x=W*0.64,y=W*0.42,s=W*0.22;
    g.strokeStyle=ins.ng?"#ff5a48":"#36d399"; g.lineWidth=3; g.strokeRect(x,y,s,s);
    for(const[dx,dy] of [[0,0],[s,0],[0,s],[s,s]]){ g.beginPath(); g.arc(x+dx,y+dy,6,0,7); g.fillStyle=g.strokeStyle; g.fill(); }
    g.font="700 20px 'Noto Sans TC',sans-serif"; g.fillStyle=g.strokeStyle; g.fillText(`${ins.verdict} ${ins.score}`, x, y-12);
  }
  function drawFaceBox(f){
    const fc=faceNow(), b=headBox(f);
    g.lineWidth=3; g.strokeStyle=fc.known?"#33dd66":"#ffaa3c"; g.strokeRect(b[0],b[1],b[2],b[3]);
    g.font="700 26px 'Noto Sans TC',sans-serif"; g.fillStyle=g.strokeStyle; g.fillText(fc.name, b[0], b[1]-10);
  }
  function caption(c){
    let t="";
    if(c.measure) t="📐 物體量測 · 深度算出長寬高與距離";
    else if(c.inspect) t="📦 定位品檢 · 比對位置/歪斜 → OK／NG";
    else if(c.obstacle) t="🚧 障礙物檢測 · 物體進入範圍即警報";
    else if(c.face) t="🙂 人臉辨識 · 認出已註冊的人並標名字";
    else if(c.pose||c.hands) t="👁 監看 · 人體骨架 + 手勢辨識";
    if(c.depth) t=(t?t+"　|　":"")+"🌈 深度圖 · 近紅遠藍";
    if(!t) return;
    g.fillStyle="rgba(8,10,14,0.66)"; g.fillRect(0,W-40,W,40);
    g.fillStyle="#eaf1ff"; g.font="600 18px 'Noto Sans TC',sans-serif";
    g.textBaseline="middle"; g.fillText(t, 18, W-20); g.textBaseline="alphabetic";
  }

  // ---------- 主繪製 ----------
  function render(){
    S.t+=0.016; const c=S.cfg, f=figure();
    if(c.depth) depthBg(); else colorBg();
    const look = c.face ? faceNow().look : LOOKS[0];
    avatar(f, look, !!c.depth);
    if(c.pose && !c.depth) skeleton(f);
    if(c.measure) drawMeasure();
    if(c.inspect) drawInspect();
    if(c.face) drawFaceBox(f);
    if(c.hands && !c.depth){ const s=gestureNow();
      g.font="600 22px 'IBM Plex Mono',monospace"; g.fillStyle="#ff7a1a";
      g.fillText("✋ "+s.g+"  "+s.n+"指", 20, 64); }
    // 距離
    g.font="600 20px 'IBM Plex Mono',monospace"; g.fillStyle=c.depth?"#fff":"#9fe8c0";
    g.fillText(distNow().toFixed(2)+" m", f.cx+f.R+12, f.headY);
    // 角標 / 字幕
    g.fillStyle="rgba(255,210,150,0.92)"; g.font="600 19px 'Noto Sans TC',sans-serif";
    g.fillText("◉ DEMO 模擬畫面 · 無實體相機", 18, 32);
    if(touring()){ g.fillStyle="rgba(120,200,255,0.95)"; g.font="600 17px 'Noto Sans TC',sans-serif";
      g.fillText("▶ 自動導覽中(操作即暫停)", 18, 56); }
    else if(!c.measure&&!c.inspect&&!c.obstacle&&!c.face&&!c.pose&&!c.hands){
      g.fillStyle="rgba(220,225,235,0.7)"; g.font="500 17px 'Noto Sans TC',sans-serif";
      g.fillText("← 切換右側模式體驗,或靜置看自動導覽", 18, 56); }
    caption(c);
    feed.src=cv.toDataURL("image/jpeg",0.74);
    setTimeout(render,100);
  }

  // ---------- 合成 /status ----------
  function buildStatus(){
    const c=S.cfg, f=figure(), dist=distNow();
    const st={ person:true, dist, fingers:0, gesture:"-", finger_states:[0,0,0,0,0].map(Boolean),
      fallen:false, tilt:+(Math.sin(S.t*0.7)*6).toFixed(1), roll:+(Math.sin(S.t*0.5)*3).toFixed(1),
      measure:null, picked:false, has_template:false, detect:false, n_views:0,
      matched:false, match_via:"", inspect:null, msg:S.msg };
    if(c.hands){ const s=gestureNow(); st.gesture=s.g; st.fingers=s.n; }
    if(c.measure) st.measure=measureVals();
    if(c.inspect) st.inspect=inspectVals();
    const names=Object.keys(S.faces);
    st.faces_db=names.map(n=>({name:n,samples:S.faces[n]}));
    if(c.face){ const fc=faceNow(); st.faces=[{name:fc.name, score:fc.known?0.62:0.12, box:headBox(f)}]; }
    else st.faces=[];
    st.face_engine=c.face_engine||"lbph"; st.arc_available=true;
    const thr=c.obstacle_dist||1.0;
    if(c.obstacle){
      if(S.regions.length){
        let per=[],hit=false,mn=0;
        S.regions.forEach(poly=>{ const inside=pointIn(poly,f.cx,f.cy);
          const d=inside?0.6:2.6, h=d<=thr; per.push({hit:h,dist:d}); hit=hit||h; if(mn===0||d<mn)mn=d; });
        st.obstacle={on:true,hit,dist:mn,thr,regions:S.regions,per};
      } else { const hit=dist<=thr; st.obstacle={on:true,hit,dist,thr,regions:[],per:[]}; }
    } else st.obstacle={on:false,hit:false,dist:0,thr,regions:S.regions,per:[]};
    return st;
  }
  function evalResp(){
    const nm=Object.keys(S.faces);
    const intra=nm.map(n=>({name:n, n:Math.max(0,(S.faces[n]*(S.faces[n]-1))/2|0), min:0.55, mean:0.69, max:0.82}));
    const inter=[]; for(let i=0;i<nm.length;i++) for(let j=i+1;j<nm.length;j++) inter.push({a:nm[i],b:nm[j],mean:0.09,max:0.17});
    const sep=nm.length>=2?{intra_min:0.55,inter_max:0.17,gap:0.38,separable:true,suggested:0.35,acc:1.0}:null;
    return {available:true, default_thr:0.35, people:nm.map(n=>({name:n,samples:S.faces[n]})),
      intra, intra_all:nm.length?{n:1,min:0.55,mean:0.69,max:0.82}:null,
      inter, inter_all:inter.length?{n:1,min:0.04,mean:0.09,max:0.17}:null, separation:sep};
  }

  // ---------- 攔截 fetch ----------
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  const jsonResp = o => new Response(JSON.stringify(o),{status:200,headers:{"Content-Type":"application/json"}});
  window.fetch = function(url,opt){
    try{
      const u=(typeof url==="string")?url:(url&&url.url)||"";
      const path=u.replace(location.origin,"").split("?")[0];
      const m=((opt&&opt.method)||"GET").toUpperCase();
      if(path==="/status")  return Promise.resolve(jsonResp(buildStatus()));
      if(path==="/measure") return Promise.resolve(jsonResp(S.cfg.measure?measureVals():{}));
      if(path==="/face/eval") return Promise.resolve(jsonResp(evalResp()));
      if(path==="/pointcloud.bin") return Promise.resolve(new Response(new Uint8Array(4).buffer,{status:200}));
      if(m==="POST"){
        let b={}; try{ b=JSON.parse((opt&&opt.body)||"{}"); }catch(e){}
        if(path==="/config") Object.assign(S.cfg,b);
        else if(path==="/obstacle/regions") S.regions=b.regions||[];
        else if(path==="/obstacle/clear") S.regions=[];
        else if(path==="/face/enroll"){ const n=(b.name||"").trim();
          if(n){ S.cfg.face=true; S.faces[n]=(S.faces[n]||0)+1; S.msg=`已擷取「${n}」第 ${S.faces[n]} 張樣本（DEMO）`; } }
        else if(path==="/face/delete") delete S.faces[(b.name||"").trim()];
        else if(path==="/face/clear") S.faces={};
        else if(path==="/face/rename"){ const o=(b.old||"").trim(), n=(b.new||"").trim();
          if(o&&n&&S.faces[o]!=null){ S.faces[n]=(S.faces[n]||0)+S.faces[o]; delete S.faces[o]; S.msg=`已改名為「${n}」（DEMO）`; } }
        return Promise.resolve(jsonResp({ok:true}));
      }
      return Promise.resolve(jsonResp({ok:true}));
    }catch(e){ return realFetch?realFetch(url,opt):Promise.reject(e); }
  };

  // ---------- 自動導覽 ----------
  let lastUser=now(), step=0;
  ["pointerdown","keydown","wheel","touchstart"].forEach(ev=>
    document.addEventListener(ev,()=>{ lastUser=now(); },{passive:true,capture:true}));
  const touring = () => now()-lastUser > 12000;
  const clickMode = m => { const el=document.querySelector('.mode[data-mode="'+m+'"]'); if(el) el.click(); };
  const setDepth = w => { const sw=document.querySelector('.sw[data-k="depth"]');
    if(sw && sw.classList.contains("on")!==w) sw.click(); };
  const STEPS=[{mode:"measure",depth:false},{mode:"inspect",depth:false},{mode:"obstacle",depth:false},
               {mode:"face",depth:false},{mode:"watch",depth:true}];
  setInterval(()=>{ if(!touring()) return;
    const s=STEPS[step++ % STEPS.length]; setDepth(s.depth); clickMode(s.mode); }, 6500);

  // ---------- DEMO 角標 ----------
  const badge=document.createElement("div");
  badge.textContent="● DEMO 模擬資料（無後端）";
  badge.style.cssText="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:400;"+
    "font:600 12px 'Noto Sans TC',sans-serif;color:#140f08;background:#ff9a3c;"+
    "padding:5px 14px;border-radius:999px;box-shadow:0 4px 16px #0006;letter-spacing:.04em";
  document.body.appendChild(badge);

  render();
  console.log("[demo] 模擬模式啟用(逼真人物 / 字幕 / 自動導覽 / 深度)");
})();
