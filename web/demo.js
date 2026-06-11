/* ============================================================
 * demo.js — PAGE 模擬模式(無實體相機 / 無後端)
 *
 * 重新規劃：每個功能畫「它該有的主角」+ 放慢 + 把結果標清楚 + 底部字幕。
 *   📐 量測  → 桌上箱子，掃描後標長寬高/距離
 *   📦 品檢  → 零件 + 參考框，輪流演 OK / NG
 *   🙂 人臉  → 置中臉孔，輪流換人 → 標名字 / 未知
 *   🚧 障礙  → 固定範圍 + 物體滑入 → 進入即紅+警報
 *   👁 監看  → 人 + 骨架 + 手勢(這裡動才合理)
 *   🌈 深度  → 彩色深度(近紅遠藍)
 * 仍是「假相機畫面 + 假後端」→ 右側真實 UI 面板照常連動。
 * 自動導覽：閒置時輪播(點真 UI 模式鈕);操作即暫停。直接示範 + 字幕,無標題卡。
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
  const S = { cfg:{}, regions:[], faces:{"小明":6, "Aki":5}, msg:"", t:0 };

  const LOOKS = [
    {skin:"#e7b48b", hair:"#2f2018", shirt:"#3f6fb0", style:"short"},
    {skin:"#caa06e", hair:"#141414", shirt:"#b04f7a", style:"bun"},
    {skin:"#f1cba2", hair:"#6b4a28", shirt:"#3fae84", style:"long"},
    {skin:"#a87a55", hair:"#101010", shirt:"#c0843c", style:"cap"},
  ];
  // 真人示範頭像(同源圖片 → canvas 不會被污染);路徑相對於 web/index.html
  const PHOTOS = [["小明","demo/face1.png"],["Aki","demo/face2.png"]].map(([name,src])=>{
    const im=new Image(); im.src=src; return {name, im}; });
  function facePick(){
    const p=PHOTOS[Math.floor(S.t/5)%PHOTOS.length], matching=(S.t%5)<1.2;
    const sz=W*0.5, x=(W-sz)/2, y=W*0.15;
    const box=[Math.round(x+sz*0.22),Math.round(y+sz*0.1),Math.round(sz*0.56),Math.round(sz*0.72)];
    return {p, matching, sim:+(0.68+0.06*Math.sin(S.t*2)).toFixed(2), box, x, y, sz};
  }
  const shade=(hex,a)=>{ const n=parseInt(hex.slice(1),16);
    const r=Math.max(0,Math.min(255,(n>>16)+a)), gc=Math.max(0,Math.min(255,((n>>8)&255)+a)),
          b=Math.max(0,Math.min(255,(n&255)+a)); return `rgb(${r},${gc},${b})`; };
  const jet = t => { t=Math.max(0,Math.min(1,t));
    const r=Math.max(0,Math.min(1,1.5-Math.abs(4*t-3))), gg=Math.max(0,Math.min(1,1.5-Math.abs(4*t-2))),
          b=Math.max(0,Math.min(1,1.5-Math.abs(4*t-1))); return `rgb(${r*255|0},${gg*255|0},${b*255|0})`; };
  const line=(a,b,c,d)=>{ g.beginPath(); g.moveTo(a,b); g.lineTo(c,d); g.stroke(); };
  const pointIn=(poly,x,y)=>{ let ins=false; for(let i=0,j=poly.length-1;i<poly.length;j=i++){
    const xi=poly[i][0],yi=poly[i][1],xj=poly[j][0],yj=poly[j][1];
    if(((yi>y)!==(yj>y))&&(x<(xj-xi)*(y-yi)/((yj-yi)||1e-6)+xi)) ins=!ins;} return ins; };

  // ---------- 共用背景 ----------
  function roomBg(){
    const bg=g.createLinearGradient(0,0,0,W);
    bg.addColorStop(0,"#1a232e"); bg.addColorStop(0.74,"#10161d"); bg.addColorStop(1,"#0c1014");
    g.fillStyle=bg; g.fillRect(0,0,W,W);
    g.strokeStyle="rgba(120,160,210,0.06)"; g.lineWidth=1;
    for(let x=0;x<W;x+=50) line(x,0,x,W);
    for(let y=0;y<W;y+=50) line(0,y,W,y);
    const fl=g.createLinearGradient(0,W*0.72,0,W);
    fl.addColorStop(0,"#26323f"); fl.addColorStop(1,"#141b22");
    g.fillStyle=fl; g.fillRect(0,W*0.72,W,W*0.28);
  }
  function tag(x,y,text,col,big){       // 標註小牌
    g.font=(big?"700 22px":"600 16px")+" 'Noto Sans TC',sans-serif";
    const w=g.measureText(text).width+18;
    g.fillStyle="rgba(8,12,18,0.8)"; g.fillRect(x,y-22,w,28);
    g.fillStyle=col; g.fillText(text,x+9,y);
  }

  // ---------- 人物(上色臉孔) ----------
  function fig(cx,cy,R,sw){ return {cx,cy,R, headY:cy-R*1.88, neckY:cy-R*1.0, shoY:cy-R*0.47,
    sh:R*1.3, hipY:cy+R*1.4, feetY:cy+R*3.5, swing:sw}; }
  const headBox = f => [Math.round(f.cx-f.R), Math.round(f.headY-f.R*1.15),
                        Math.round(f.R*2), Math.round(f.R*2.3)];
  function avatar(f, look, depth){
    const cx=f.cx, hy=f.headY, R=f.R;
    g.fillStyle = depth ? jet(0.8) : look.shirt;
    g.beginPath(); g.moveTo(cx-R*1.7,f.feetY); g.quadraticCurveTo(cx-R*1.85,hy+R*1.5,cx-R*0.75,hy+R*1.15);
    g.lineTo(cx+R*0.75,hy+R*1.15); g.quadraticCurveTo(cx+R*1.85,hy+R*1.5,cx+R*1.7,f.feetY); g.closePath(); g.fill();
    g.fillStyle = depth ? jet(0.83) : shade(look.skin,-22); g.fillRect(cx-R*0.33,hy+R*0.55,R*0.66,R*0.7);
    if(depth){ g.fillStyle=jet(0.9); g.beginPath(); g.ellipse(cx,hy,R*0.95,R*1.12,0,0,7); g.fill(); return; }
    g.fillStyle=shade(look.skin,-12);
    g.beginPath(); g.ellipse(cx-R*0.92,hy+R*0.05,R*0.17,R*0.27,0,0,7); g.fill();
    g.beginPath(); g.ellipse(cx+R*0.92,hy+R*0.05,R*0.17,R*0.27,0,0,7); g.fill();
    const grd=g.createRadialGradient(cx-R*0.3,hy-R*0.35,R*0.2,cx,hy,R*1.25);
    grd.addColorStop(0,shade(look.skin,22)); grd.addColorStop(1,shade(look.skin,-22));
    g.fillStyle=grd; g.beginPath(); g.ellipse(cx,hy,R*0.92,R*1.1,0,0,7); g.fill();
    const eo=R*0.4, ey=hy-R*0.05, ew=R*0.19, eh=R*0.12, blink=(S.t%3.2)<0.13;
    g.strokeStyle=look.hair; g.lineWidth=R*0.08; g.lineCap="round";
    line(cx-eo-ew*0.7,hy-R*0.32,cx-eo+ew*0.7,hy-R*0.37); line(cx+eo-ew*0.7,hy-R*0.37,cx+eo+ew*0.7,hy-R*0.32);
    for(const sx of [-1,1]){ const ex=cx+sx*eo;
      if(blink){ g.strokeStyle="#5a4636"; g.lineWidth=R*0.05; line(ex-ew,ey,ex+ew,ey); }
      else{ g.fillStyle="#fff"; g.beginPath(); g.ellipse(ex,ey,ew,eh,0,0,7); g.fill();
        g.fillStyle="#3a2a1a"; g.beginPath(); g.arc(ex+sx*R*0.02,ey,eh*0.72,0,7); g.fill();
        g.fillStyle="#000"; g.beginPath(); g.arc(ex+sx*R*0.02,ey,eh*0.34,0,7); g.fill(); } }
    g.strokeStyle=shade(look.skin,-32); g.lineWidth=R*0.05;
    g.beginPath(); g.moveTo(cx,hy-R*0.02); g.lineTo(cx-R*0.1,hy+R*0.26); g.quadraticCurveTo(cx,hy+R*0.34,cx+R*0.08,hy+R*0.26); g.stroke();
    g.strokeStyle="#a4584a"; g.lineWidth=R*0.09; g.beginPath(); g.arc(cx,hy+R*0.42,R*0.34,0.16*Math.PI,0.84*Math.PI); g.stroke();
    g.fillStyle=look.hair;
    if(look.style==="cap"){ g.beginPath(); g.ellipse(cx,hy-R*0.45,R*1.0,R*0.65,0,Math.PI,2*Math.PI); g.fill(); g.fillRect(cx-R,hy-R*0.5,R*2,R*0.14); }
    else{ g.beginPath(); g.ellipse(cx,hy-R*0.5,R*0.98,R*0.7,0,Math.PI,2*Math.PI); g.fill();
      if(look.style==="long"){ g.fillRect(cx-R*0.98,hy-R*0.5,R*0.26,R*1.35); g.fillRect(cx+R*0.72,hy-R*0.5,R*0.26,R*1.35); }
      if(look.style==="bun"){ g.beginPath(); g.arc(cx,hy-R*1.05,R*0.3,0,7); g.fill(); } }
  }
  function skeleton(f){
    const J={head:[f.cx,f.headY],neck:[f.cx,f.neckY],ls:[f.cx-f.sh,f.shoY],rs:[f.cx+f.sh,f.shoY],
      hip:[f.cx,f.hipY],lf:[f.cx-f.R*0.6,f.feetY],rf:[f.cx+f.R*0.6,f.feetY],
      le:[f.cx-f.sh*1.15,f.shoY+f.R*1.0+f.swing*16], re:[f.cx+f.sh*1.15,f.shoY+f.R*1.0-f.swing*16]};
    g.strokeStyle="#3df58a"; g.lineWidth=4; g.lineCap="round";
    for(const[a,b] of [["head","neck"],["neck","ls"],["neck","rs"],["ls","le"],["rs","re"],["neck","hip"],["hip","lf"],["hip","rf"]])
      line(J[a][0],J[a][1],J[b][0],J[b][1]);
    g.fillStyle="#ff4e6a"; for(const k in J){ g.beginPath(); g.arc(J[k][0],J[k][1],5,0,7); g.fill(); }
  }

  // ---------- 合成數值(畫面與 /status 共用) ----------
  const distNow = () => +(0.55+0.07*Math.sin(S.t*0.8)).toFixed(2);
  const measureVals = () => ({ length:0.243, width:0.161, height:0.098, tilt:3, dist:distNow(), n_pts:2100 });
  const inspectOK = () => Math.floor(S.t/4.5)%2===0;
  function inspectVals(){ const ok=inspectOK();
    return { verdict:ok?"OK":"NG", ng:!ok, score: ok?Math.round(94+3*Math.sin(S.t*3)):Math.round(46+5*Math.sin(S.t*3)),
      skew: ok?2:13, oob:!ok, reason:"" }; }
  function gestureNow(){ const seq=[["PALM",5],["FIST",0],["ONE",1],["TWO",2],["OK",3]];
    const s=seq[Math.floor(S.t*0.5)%seq.length]; return {g:s[0], n:s[1]}; }
  const OZONE=[[W*0.30,W*0.16],[W*0.72,W*0.16],[W*0.72,W*0.60],[W*0.30,W*0.60]];
  function obstacleSim(){ const objX=W*0.5+Math.sin(S.t*0.55)*W*0.34, objY=W*0.38;
    const inZone=pointIn(OZONE,objX,objY); return {objX,objY,inZone,dist:inZone?0.6:2.4,r:W*0.06}; }

  // ---------- 場景 ----------
  function box3d(x,y,w,h,d,col){
    g.fillStyle=col; g.fillRect(x,y,w,h);
    g.fillStyle=shade(col,28); g.beginPath(); g.moveTo(x,y); g.lineTo(x+d,y-d); g.lineTo(x+w+d,y-d); g.lineTo(x+w,y); g.closePath(); g.fill();
    g.fillStyle=shade(col,-32); g.beginPath(); g.moveTo(x+w,y); g.lineTo(x+w+d,y-d); g.lineTo(x+w+d,y+h-d); g.lineTo(x+w,y+h); g.closePath(); g.fill();
    g.strokeStyle="rgba(0,0,0,.25)"; g.lineWidth=1; g.strokeRect(x,y,w,h);
  }
  function sceneMeasure(){
    roomBg();
    const x=W*0.34,y=W*0.46,w=W*0.26,h=W*0.2,d=W*0.07,m=measureVals();
    box3d(x,y,w,h,d,"#c79a5e");
    const mp=S.t%5, scanning=mp<1.8;
    if(scanning){
      const sy=y-d+(h+d)*(mp/1.8);
      g.strokeStyle="#27e0ff"; g.lineWidth=3; line(x-10,sy,x+w+d+10,sy);
      g.fillStyle="#27e0ff22"; g.fillRect(x-10,y-d,w+d+20,sy-(y-d));
      return "📐 物體量測 · 掃描中… 用深度重建物體";
    }
    g.setLineDash([6,5]); g.strokeStyle="#36d399"; g.lineWidth=2; g.strokeRect(x-12,y-d-12,w+d+24,h+d+24); g.setLineDash([]);
    g.strokeStyle="#9fe8c0"; g.lineWidth=1.5; g.fillStyle="#9fe8c0"; g.font="600 15px 'IBM Plex Mono',monospace";
    line(x,y+h+22,x+w,y+h+22); g.fillText((m.width*100).toFixed(1)+" cm",x+w*0.3,y+h+40);     // 寬
    line(x-22,y,x-22,y+h); g.save(); g.translate(x-30,y+h*0.6); g.rotate(-Math.PI/2); g.fillText((m.height*100).toFixed(1)+" cm",0,0); g.restore(); // 高
    tag(x+w+d+8,y-d+18,(m.length*100).toFixed(1)+" cm 長",  "#9fe8c0");
    tag(x, y-d-22, "距離 "+m.dist.toFixed(2)+" m", "#27e0ff");
    return `📐 物體量測 · ${ (m.length*100).toFixed(0) }×${(m.width*100).toFixed(0)}×${(m.height*100).toFixed(0)} cm,距離 ${m.dist.toFixed(2)} m`;
  }
  function sceneInspect(){
    roomBg();
    const ok=inspectOK(), v=inspectVals();
    const cx=W*0.5, cy=W*0.46, s=W*0.26;
    g.setLineDash([7,6]); g.lineWidth=2; g.strokeStyle="#88ffd0aa"; g.strokeRect(cx-s/2,cy-s/2,s,s); g.setLineDash([]); // 參考框
    g.save(); g.translate(cx,cy);
    if(!ok){ g.translate(W*0.04,W*0.02); g.rotate(0.18); }           // NG：移位+歪斜
    g.fillStyle="#5b6b86"; g.fillRect(-s*0.34,-s*0.28,s*0.68,s*0.56);
    g.fillStyle="#8fa3c2"; g.fillRect(-s*0.24,-s*0.18,s*0.3,s*0.16);
    g.fillStyle="#c0d0e8"; g.fillRect(s*0.02,-s*0.05,s*0.18,s*0.2);
    g.restore();
    g.lineWidth=4; g.strokeStyle=ok?"#33dd66":"#ff5a48"; g.strokeRect(cx-s/2-6,cy-s/2-6,s+12,s+12);
    tag(cx-s/2-6, cy-s/2-14, (ok?"✓ OK ":"✗ NG ")+v.score, ok?"#33dd66":"#ff7a6a", true);
    return ok ? "📦 定位品檢 · 位置正確 → 合格(OK)"
              : `📦 定位品檢 · 物體移位/歪斜 ${v.skew}° → 不合格(NG)`;
  }
  function sceneFace(){
    roomBg();
    const fp=facePick(), p=fp.p;
    if(p.im.complete && p.im.naturalWidth) g.drawImage(p.im, fp.x, fp.y, fp.sz, fp.sz);
    else { const f=fig(W*0.5,W*0.46,W*0.13,0); avatar(f,LOOKS[0],false); }   // 圖未載入 → 退回卡通
    const col=fp.matching?"#ffd23c":"#33dd66";
    g.lineWidth=3; g.strokeStyle=col; g.strokeRect(fp.box[0],fp.box[1],fp.box[2],fp.box[3]);
    tag(fp.box[0], fp.box[1]-10, fp.matching?"比對中…":`${p.name}  ${fp.sim}`, col, true);
    return fp.matching ? "🙂 人臉辨識 · 比對特徵中…"
                       : `🙂 人臉辨識 · 認出「${p.name}」(相似度 ${fp.sim})`;
  }
  function sceneObstacle(){
    roomBg();
    const sim=obstacleSim();
    g.fillStyle=sim.inZone?"rgba(255,90,72,.18)":"rgba(74,222,128,.12)";
    g.beginPath(); g.moveTo(OZONE[0][0],OZONE[0][1]); OZONE.forEach(p=>g.lineTo(p[0],p[1])); g.closePath(); g.fill();
    g.lineWidth=3; g.strokeStyle=sim.inZone?"#ff5a48":"#36d399"; g.stroke();
    g.fillStyle=sim.inZone?"#ff8a7a":"#9fe8c0"; g.font="600 15px 'IBM Plex Mono',monospace";
    g.fillText("檢測範圍", OZONE[0][0]+8, OZONE[0][1]+22);
    g.fillStyle="#d8a55a"; g.beginPath(); g.arc(sim.objX,sim.objY,sim.r,0,7); g.fill();   // 物體
    g.fillStyle="#7a5a2a"; g.beginPath(); g.arc(sim.objX-sim.r*0.3,sim.objY-sim.r*0.3,sim.r*0.5,0,7); g.fill();
    if(sim.inZone) tag(sim.objX-30, sim.objY-sim.r-10, "⚠ "+sim.dist.toFixed(2)+" m", "#ff7a6a", true);
    return sim.inZone ? `🚧 障礙物檢測 · 物體進入範圍!距離 ${sim.dist.toFixed(2)} m → 警報`
                      : "🚧 障礙物檢測 · 範圍內無障礙 · 安全";
  }
  function scenePose(depth){
    if(depth) depthBg(); else roomBg();
    const f=fig(W*0.5+Math.sin(S.t*0.5)*W*0.14, W*0.46, W*0.1, Math.sin(S.t*2.6));
    avatar(f, LOOKS[0], depth);
    if(!depth){ skeleton(f); const s=gestureNow();
      tag(20,40,"✋ 手勢:"+s.g+"  "+s.n+"指","#ff7a1a");
      return "👁 監看 · 人體骨架追蹤 + 手勢辨識（跌倒也會警報）"; }
    return "🌈 深度圖 · 近紅遠藍,越凸越近(可做活體/距離)";
  }
  function depthBg(){ for(let y=0;y<W;y+=6){ g.fillStyle=jet(0.12+0.55*(y/W)); g.fillRect(0,y,W,6); } }
  function sceneIdle(){ roomBg();
    const f=fig(W*0.5, W*0.5, W*0.1, 0); avatar(f, LOOKS[0], false);
    return "← 點右側功能來體驗,或靜置看自動導覽";
  }

  // ---------- 字幕 / 角標 ----------
  function captionBar(text){
    if(!text) return;
    g.fillStyle="rgba(8,11,16,0.72)"; g.fillRect(0,W-46,W,46);
    g.fillStyle="#eaf1ff"; g.font="600 19px 'Noto Sans TC',sans-serif"; g.textBaseline="middle";
    g.fillText(text, 18, W-23); g.textBaseline="alphabetic";
  }
  function topTags(){
    g.fillStyle="rgba(255,210,150,0.92)"; g.font="600 18px 'Noto Sans TC',sans-serif";
    g.fillText("◉ DEMO 模擬畫面 · 無實體相機", 16, 30);
    if(touring()){ g.fillStyle="rgba(120,200,255,0.95)"; g.font="600 16px 'Noto Sans TC',sans-serif";
      g.fillText("▶ 自動導覽中(操作即暫停)", 16, 52); }
  }

  // ---------- 主繪製 ----------
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
    feed.src=cv.toDataURL("image/jpeg",0.74);
    setTimeout(render,100);
  }

  // ---------- 合成 /status ----------
  function buildStatus(){
    const c=S.cfg, dist=distNow();
    const st={ person:true, dist, fingers:0, gesture:"-", finger_states:[0,0,0,0,0].map(Boolean),
      fallen:false, tilt:0, roll:+(Math.sin(S.t*0.5)*2).toFixed(1), measure:null, picked:false,
      has_template:false, detect:false, n_views:0, matched:false, match_via:"", inspect:null, msg:S.msg };
    if(c.hands){ const s=gestureNow(); st.gesture=s.g; st.fingers=s.n; }
    if(c.measure) st.measure=measureVals();
    if(c.inspect) st.inspect=inspectVals();
    const names=Object.keys(S.faces); st.faces_db=names.map(n=>({name:n,samples:S.faces[n]}));
    if(c.face){ const fp=facePick(); st.faces=[{name:fp.matching?"未知":fp.p.name, score:fp.matching?0.1:fp.sim, box:fp.box}]; } else st.faces=[];
    st.face_engine=c.face_engine||"lbph"; st.arc_available=true;
    const thr=c.obstacle_dist||1.0;
    if(c.obstacle){ if(S.regions.length){
        let per=[],hit=false,mn=0; const fx=W*0.5+Math.sin(S.t*0.55)*W*0.34, fy=W*0.38;
        S.regions.forEach(poly=>{ const ins=pointIn(poly,fx,fy), d=ins?0.6:2.6, h=d<=thr; per.push({hit:h,dist:d}); hit=hit||h; if(mn===0||d<mn)mn=d; });
        st.obstacle={on:true,hit,dist:mn,thr,regions:S.regions,per};
      } else { const sim=obstacleSim(); st.obstacle={on:true,hit:sim.inZone,dist:sim.dist,thr,regions:[],per:[]}; }
    } else st.obstacle={on:false,hit:false,dist:0,thr,regions:S.regions,per:[]};
    return st;
  }
  function evalResp(){ const nm=Object.keys(S.faces);
    const intra=nm.map(n=>({name:n,n:Math.max(0,(S.faces[n]*(S.faces[n]-1))/2|0),min:0.55,mean:0.69,max:0.82}));
    const inter=[]; for(let i=0;i<nm.length;i++) for(let j=i+1;j<nm.length;j++) inter.push({a:nm[i],b:nm[j],mean:0.09,max:0.17});
    const sep=nm.length>=2?{intra_min:0.55,inter_max:0.17,gap:0.38,separable:true,suggested:0.35,acc:1.0}:null;
    return {available:true,default_thr:0.35,people:nm.map(n=>({name:n,samples:S.faces[n]})),
      intra,intra_all:nm.length?{n:1,min:0.55,mean:0.69,max:0.82}:null,inter,
      inter_all:inter.length?{n:1,min:0.04,mean:0.09,max:0.17}:null,separation:sep}; }

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
  const clickMode=m=>{ const el=document.querySelector('.mode[data-mode="'+m+'"]'); if(el) el.click(); };
  const setDepth=w=>{ const sw=document.querySelector('.sw[data-k="depth"]'); if(sw&&sw.classList.contains("on")!==w) sw.click(); };
  const STEPS=[{mode:"measure",depth:false},{mode:"inspect",depth:false},{mode:"obstacle",depth:false},
               {mode:"face",depth:false},{mode:"watch",depth:false},{mode:"watch",depth:true}];
  setInterval(()=>{ if(!touring()) return; const s=STEPS[step++%STEPS.length]; setDepth(s.depth); clickMode(s.mode); }, 8500);

  // ---------- 角標元素 ----------
  const badge=document.createElement("div");
  badge.textContent="● DEMO 模擬資料（無後端）";
  badge.style.cssText="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:400;"+
    "font:600 12px 'Noto Sans TC',sans-serif;color:#140f08;background:#ff9a3c;padding:5px 14px;border-radius:999px;box-shadow:0 4px 16px #0006;letter-spacing:.04em";
  document.body.appendChild(badge);

  render();
  console.log("[demo] 模擬模式：場景對應功能 + 字幕 + 自動導覽");
})();
