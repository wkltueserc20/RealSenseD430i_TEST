/* ============================================================
 * demo.js — PAGE 模擬模式(無實體相機 / 無後端)
 *
 * 讓人在 GitHub Pages 上完整 DEMO：動畫假畫面 + 攔截 API 回合成資料。
 * 特色：
 *   • 每個模式都有戲：量測/品檢/監看/障礙/人臉都會即時呈現
 *   • 深度視覺化：開「深度圖」時畫面變成彩色深度(近紅遠藍)
 *   • 人臉示範：預載示範人臉，辨識結果在「已知↔未知」輪播
 *   • 自動導覽：閒置時自動輪流展示各功能(用「點選模式鈕」驅動真 UI)
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

  // ---------- 幾何 ----------
  function figure(){
    const cx = W*0.5 + Math.sin(S.t*0.55)*W*0.24;
    const cy = W*0.50 + Math.sin(S.t*2.2)*4;
    const R  = W*0.07;
    return { cx, cy, R, headY: cy-W*0.15, neckY: cy-W*0.085, shoY: cy-W*0.05,
             sh: W*0.085, hipY: cy+W*0.11, feetY: cy+W*0.27, swing: Math.sin(S.t*3) };
  }
  const headBox = f => [Math.round(f.cx-f.R), Math.round(f.headY-f.R),
                        Math.round(f.R*2), Math.round(f.R*2)];
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
    bg.addColorStop(0,"#120d07"); bg.addColorStop(1,"#1c1510");
    g.fillStyle=bg; g.fillRect(0,0,W,W);
    g.strokeStyle="rgba(255,122,26,0.06)"; g.lineWidth=1;
    for(let x=0;x<W;x+=48) line(x,0,x,W);
    for(let y=0;y<W;y+=48) line(0,y,W,y);
    g.fillStyle="rgba(255,255,255,0.05)"; g.fillRect(0,W*0.78,W,W*0.22);
  }
  function depthBg(){                       // 彩色深度：上遠(藍) → 下近(暖)
    for(let y=0;y<W;y+=8){ g.fillStyle=jet(0.15+0.5*(y/W)); g.fillRect(0,y,W,8); }
    g.fillStyle="rgba(255,255,255,0.05)"; g.font="600 18px 'IBM Plex Mono',monospace";
    g.fillText("DEPTH 0.3 – 4.0 m", 18, W-20);
  }

  // ---------- 人 ----------
  function person(f,depth){
    if(depth){                              // 深度模式：暖色(近)填實剪影
      g.fillStyle=jet(0.85);
      g.beginPath(); g.arc(f.cx,f.headY,f.R,0,7); g.fill();
      g.lineWidth=f.R*1.4; g.strokeStyle=jet(0.8); g.lineCap="round";
      line(f.cx,f.neckY,f.cx,f.hipY);
      g.lineWidth=f.R*0.7;
      line(f.cx,f.hipY,f.cx-W*0.05,f.feetY); line(f.cx,f.hipY,f.cx+W*0.05,f.feetY);
      return;
    }
    const acc=S.cfg.pose?"#ffb000":"#cdd6e6";
    g.strokeStyle=acc; g.lineWidth=W*0.012; g.lineCap="round";
    g.beginPath(); g.arc(f.cx,f.headY,f.R,0,7); g.stroke();
    line(f.cx,f.neckY,f.cx,f.hipY);
    line(f.cx,f.shoY,f.cx-f.sh,f.shoY+W*0.05+f.swing*16);
    line(f.cx,f.shoY,f.cx+f.sh,f.shoY+W*0.05-f.swing*16);
    line(f.cx,f.hipY,f.cx-W*0.05,f.feetY+f.swing*14);
    line(f.cx,f.hipY,f.cx+W*0.05,f.feetY-f.swing*14);
    if(S.cfg.pose){ g.fillStyle="#4ea1ff";
      for(const p of [[f.cx,f.headY],[f.cx,f.neckY],[f.cx-f.sh,f.shoY],[f.cx+f.sh,f.shoY],
                      [f.cx,f.hipY],[f.cx-W*0.05,f.feetY],[f.cx+W*0.05,f.feetY]]){
        g.beginPath(); g.arc(p[0],p[1],W*0.012,0,7); g.fill(); } }
  }

  // ---------- 各模式疊圖 ----------
  function drawMeasure(){
    const x=W*0.16,y=W*0.5,bw=W*0.2,bh=W*0.16, m=measureVals();
    g.strokeStyle="#36d399"; g.lineWidth=3; g.strokeRect(x,y,bw,bh);
    g.setLineDash([4,4]); g.strokeStyle="#36d39988";
    g.strokeRect(x-6,y-6,bw+12,bh+12); g.setLineDash([]);
    g.fillStyle="#9fe8c0"; g.font="600 17px 'IBM Plex Mono',monospace";
    g.fillText(`${(m.length*100).toFixed(1)}×${(m.width*100).toFixed(1)}×${(m.height*100).toFixed(1)} cm`, x-4, y-16);
  }
  function drawInspect(){
    const ins=inspectVals(); const x=W*0.62,y=W*0.46,s=W*0.22;
    g.strokeStyle=ins.ng?"#ff5a48":"#36d399"; g.lineWidth=3; g.strokeRect(x,y,s,s);
    for(const[dx,dy] of [[0,0],[s,0],[0,s],[s,s]]){ g.beginPath();
      g.arc(x+dx,y+dy,6,0,7); g.fillStyle=g.strokeStyle; g.fill(); }
    g.font="700 20px 'Noto Sans TC',sans-serif"; g.fillStyle=g.strokeStyle;
    g.fillText(`${ins.verdict} ${ins.score}`, x, y-12);
  }
  function drawFace(f){
    const fc=faceNow(); const b=headBox(f);
    g.lineWidth=3; g.strokeStyle=fc.known?"#33dd66":"#ffaa3c";
    g.strokeRect(b[0],b[1],b[2],b[3]);
    g.font="700 26px 'Noto Sans TC',sans-serif"; g.fillStyle=g.strokeStyle;
    g.fillText(fc.name, b[0], b[1]-10);
  }
  function drawGesture(){
    const s=gestureNow();
    g.font="600 22px 'IBM Plex Mono',monospace"; g.fillStyle="#ff7a1a";
    g.fillText("✋ "+s.g+"  "+s.n+"指", 20, W-26);
  }

  // ---------- 合成數值(畫面與 /status 共用，確保一致) ----------
  const distNow = () => +(1.4+Math.sin(S.t*0.6)*0.6).toFixed(2);
  const measureVals = () => ({ length:0.12+0.02*Math.sin(S.t), width:0.08+0.015*Math.sin(S.t*1.3),
    height:0.05+0.01*Math.sin(S.t*0.7), tilt:Math.round(Math.sin(S.t)*8), dist:distNow(),
    n_pts:1800+Math.floor(500*Math.abs(Math.sin(S.t))) });
  function inspectVals(){ const score=Math.round(62+30*Math.sin(S.t*0.45));
    const thr=S.cfg.inspect_thr||60; const ng=score<thr;
    return { verdict:ng?"NG":"OK", ng, score, reason:"", skew:Math.round(Math.abs(Math.sin(S.t*0.8))*12), oob:false }; }
  function gestureNow(){ const seq=[["PALM",5],["FIST",0],["ONE",1],["TWO",2],["OK",3]];
    const s=seq[Math.floor(S.t*0.6)%seq.length]; return {g:s[0], n:s[1]}; }
  function faceNow(){ const names=Object.keys(S.faces);
    const pool=names.length?names.concat(["未知"]):["未知"];
    const name=pool[Math.floor(S.t*0.35)%pool.length]; return {name, known:name!=="未知"}; }

  // ---------- 主繪製 ----------
  function render(){
    S.t+=0.016; const c=S.cfg, f=figure();
    if(c.depth) depthBg(); else colorBg();
    person(f,!!c.depth);
    if(c.measure) drawMeasure();
    if(c.inspect) drawInspect();
    if(c.face) drawFace(f);
    if(c.hands && !c.depth) drawGesture();
    // 距離標籤
    g.font="600 22px 'IBM Plex Mono',monospace"; g.fillStyle=c.depth?"#fff":"#9fe8c0";
    g.fillText(distNow().toFixed(2)+" m", f.cx+f.R+10, f.headY);
    // 角標 / 提示
    g.font="600 20px 'Noto Sans TC',sans-serif"; g.fillStyle="rgba(255,210,150,0.9)";
    g.fillText("◉ DEMO 模擬畫面 · 無實體相機", 18, 34);
    if(touring()){ g.fillStyle="rgba(120,200,255,0.95)";
      g.fillText("▶ 自動導覽中(操作即暫停)", 18, 60); }
    else if(!c.pose&&!c.hands&&!c.face&&!c.obstacle&&!c.measure&&!c.inspect){
      g.font="500 18px 'Noto Sans TC',sans-serif"; g.fillStyle="rgba(230,210,180,0.7)";
      g.fillText("← 切換右側模式 / 開關來體驗,或靜置看自動導覽", 18, 60); }
    feed.src=cv.toDataURL("image/jpeg",0.72);
    setTimeout(render,110);
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
          const d=inside?0.6:2.6, h=d<=thr; per.push({hit:h,dist:d});
          hit=hit||h; if(mn===0||d<mn)mn=d; });
        st.obstacle={on:true,hit,dist:mn,thr,regions:S.regions,per};
      } else { const hit=dist<=thr;
        st.obstacle={on:true,hit,dist,thr,regions:[],per:[]}; }
    } else st.obstacle={on:false,hit:false,dist:0,thr,regions:S.regions,per:[]};
    return st;
  }

  function evalResp(){       // /face/eval 用註冊名單編一份漂亮分離度
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

  // ---------- 自動導覽(閒置時點真 UI 的模式鈕) ----------
  let lastUser=now(), step=0;
  ["pointerdown","keydown","wheel","touchstart"].forEach(ev=>
    document.addEventListener(ev,()=>{ lastUser=now(); },{passive:true,capture:true}));
  const touring = () => now()-lastUser > 12000;
  function clickMode(mode){ const el=document.querySelector('.mode[data-mode="'+mode+'"]'); if(el) el.click(); }
  function setDepth(want){ const sw=document.querySelector('.sw[data-k="depth"]');
    if(sw && sw.classList.contains("on")!==want) sw.click(); }
  const STEPS=[
    {mode:"measure", depth:false}, {mode:"inspect", depth:false},
    {mode:"obstacle",depth:false}, {mode:"face",    depth:false},
    {mode:"watch",   depth:true},
  ];
  setInterval(()=>{ if(!touring()) return;       // 有人操作就不導覽
    const s=STEPS[step++ % STEPS.length]; setDepth(s.depth); clickMode(s.mode);
  }, 6500);

  // ---------- DEMO 角標 ----------
  const badge=document.createElement("div");
  badge.textContent="● DEMO 模擬資料（無後端）";
  badge.style.cssText="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:400;"+
    "font:600 12px 'Noto Sans TC',sans-serif;color:#140f08;background:#ff9a3c;"+
    "padding:5px 14px;border-radius:999px;box-shadow:0 4px 16px #0006;letter-spacing:.04em";
  document.body.appendChild(badge);

  render();
  console.log("[demo] 模擬模式啟用(含自動導覽 / 深度視覺化)");
})();
