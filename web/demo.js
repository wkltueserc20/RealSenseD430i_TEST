/* ============================================================
 * demo.js — PAGE 上的「模擬模式」(無實體相機 / 無後端)
 *
 * 目的：讓人在 GitHub Pages 上直接 DEMO 整套介面。
 *   - 用一張動畫 canvas 假裝相機畫面(餵給 <img id="feed">)
 *   - 攔截 fetch：/status 回傳合成狀態；/config /obstacle/* /face/* 等
 *     POST 一律 {ok:true}，並把使用者的設定(開關、畫的範圍、註冊的名字)
 *     記在本地，讓「障礙偵測」「人臉辨識」等新功能也能互動式演示。
 *
 * 啟用條件：網址在 *.github.io、用 file:// 開、或帶 ?demo=1。
 *   想在本機(有後端時)強制關閉：?demo=0
 * ============================================================ */
(function(){
  const q = new URLSearchParams(location.search).get("demo");
  const DEMO = q === "1" ||
    (q !== "0" && (/github\.io$/i.test(location.hostname) || location.protocol === "file:"));
  if (!DEMO) return;

  const W = 800;                       // 與預設畫布 disp 一致 → 座標對得上
  const feed = document.getElementById("feed");
  const cv = document.createElement("canvas"); cv.width = cv.height = W;
  const g = cv.getContext("2d");

  // 本地狀態：由攔截到的 POST 累積
  const S = { cfg:{}, regions:[], faces:{}, msg:"", t:0 };

  // ---- 角色幾何(走動的人) ----
  function figure(){
    const cx = W*0.5 + Math.sin(S.t*0.6)*W*0.26;
    const cy = W*0.50 + Math.sin(S.t*2.4)*4;
    const R  = W*0.065;
    return { cx, cy, R,
      headY: cy - W*0.15, neckY: cy - W*0.085,
      shoY: cy - W*0.05, sh: W*0.085,
      hipY: cy + W*0.11, feetY: cy + W*0.27,
      swing: Math.sin(S.t*3.0) };
  }
  function headBox(f){ return [Math.round(f.cx-f.R), Math.round(f.headY-f.R),
                               Math.round(f.R*2), Math.round(f.R*2)]; }
  function pointIn(poly, x, y){
    let inside=false;
    for(let i=0,j=poly.length-1;i<poly.length;j=i++){
      const xi=poly[i][0],yi=poly[i][1],xj=poly[j][0],yj=poly[j][1];
      if(((yi>y)!==(yj>y)) && (x < (xj-xi)*(y-yi)/((yj-yi)||1e-6)+xi)) inside=!inside;
    }
    return inside;
  }

  // ---- 畫合成畫面 ----
  function line(a,b,c,d){ g.beginPath(); g.moveTo(a,b); g.lineTo(c,d); g.stroke(); }
  function render(){
    S.t += 0.016;
    const c = S.cfg, f = figure();
    // 背景
    const bg = g.createLinearGradient(0,0,0,W);
    bg.addColorStop(0,"#120d07"); bg.addColorStop(1,"#1c1510");
    g.fillStyle=bg; g.fillRect(0,0,W,W);
    g.strokeStyle="rgba(255,122,26,0.06)"; g.lineWidth=1;
    for(let x=0;x<W;x+=48) line(x,0,x,W);
    for(let y=0;y<W;y+=48) line(0,y,W,y);
    g.fillStyle="rgba(255,255,255,0.05)"; g.fillRect(0,W*0.78,W,W*0.22); // 地板

    // 人(火柴人)
    const acc = c.pose ? "#ffb000" : "#cdd6e6";
    g.strokeStyle=acc; g.lineWidth=W*0.012; g.lineCap="round";
    g.beginPath(); g.arc(f.cx,f.headY,f.R,0,7); g.stroke();           // 頭
    line(f.cx,f.neckY,f.cx,f.hipY);                                   // 軀幹
    line(f.cx,f.shoY,f.cx-f.sh,f.shoY+W*0.05+f.swing*16);             // 左臂
    line(f.cx,f.shoY,f.cx+f.sh,f.shoY+W*0.05-f.swing*16);             // 右臂
    line(f.cx,f.hipY,f.cx-W*0.05,f.feetY+f.swing*14);                 // 左腿
    line(f.cx,f.hipY,f.cx+W*0.05,f.feetY-f.swing*14);                 // 右腿
    if(c.pose){ g.fillStyle="#4ea1ff";                                // 關節點
      for(const p of [[f.cx,f.headY],[f.cx,f.neckY],[f.cx-f.sh,f.shoY],[f.cx+f.sh,f.shoY],
                      [f.cx,f.hipY],[f.cx-W*0.05,f.feetY],[f.cx+W*0.05,f.feetY]]){
        g.beginPath(); g.arc(p[0],p[1],W*0.012,0,7); g.fill(); } }

    // 距離標籤
    const dist = +(1.4 + Math.sin(S.t*0.6)*0.6).toFixed(2);
    g.font="600 22px 'IBM Plex Mono',monospace"; g.fillStyle="#9fe8c0";
    g.fillText(dist.toFixed(2)+" m", f.cx+f.R+10, f.headY);

    // 人臉框 + 名字
    if(c.face){
      const names=Object.keys(S.faces), nm=names.length?names[names.length-1]:null;
      const b=headBox(f); g.lineWidth=3; g.strokeStyle=nm?"#33dd66":"#ffaa3c";
      g.strokeRect(b[0],b[1],b[2],b[3]);
      g.font="700 26px 'Noto Sans TC',sans-serif"; g.fillStyle=nm?"#33dd66":"#ffaa3c";
      g.fillText(nm||"未知", b[0], b[1]-10);
    }
    // 手勢
    if(c.hands){
      const seq=[["PALM",5],["FIST",0],["ONE",1],["TWO",2],["OK",3]];
      const s=seq[Math.floor(S.t*0.6)%seq.length];
      g.font="600 22px 'IBM Plex Mono',monospace"; g.fillStyle="#ff7a1a";
      g.fillText("✋ "+s[0]+"  "+s[1]+"指", 20, W-26);
    }

    // DEMO 浮水印
    g.font="600 20px 'Noto Sans TC',sans-serif"; g.fillStyle="rgba(255,200,140,0.85)";
    g.fillText("◉ DEMO 模擬畫面 · 無實體相機", 18, 34);
    if(!c.pose&&!c.hands&&!c.face&&!c.obstacle){
      g.font="500 18px 'Noto Sans TC',sans-serif"; g.fillStyle="rgba(230,210,180,0.7)";
      g.fillText("← 在右側切換模式 / 開關來體驗各功能", 18, 64);
    }

    feed.src = cv.toDataURL("image/jpeg", 0.72);
    setTimeout(render, 110);
  }

  // ---- 合成 /status ----
  function buildStatus(){
    const c=S.cfg, f=figure();
    const dist=+(1.4 + Math.sin(S.t*0.6)*0.6).toFixed(2);
    const st={ person:true, dist, fingers:0, gesture:"-", finger_states:[0,0,0,0,0].map(Boolean),
      fallen:false, tilt:0.0, roll:+(Math.sin(S.t*0.5)*3).toFixed(1),
      measure:null, picked:false, has_template:false, detect:false, n_views:0,
      matched:false, match_via:"", inspect:null, msg:S.msg };
    if(c.hands){ const seq=[["PALM",5],["FIST",0],["ONE",1],["TWO",2],["OK",3]];
      const s=seq[Math.floor(S.t*0.6)%seq.length]; st.gesture=s[0]; st.fingers=s[1]; }
    // 人臉
    const names=Object.keys(S.faces);
    st.faces_db = names.map(n=>({name:n, samples:S.faces[n]}));
    if(c.face){ const nm=names.length?names[names.length-1]:null;
      st.faces=[{name:nm||"未知", score:nm?0.6:0.1, box:headBox(f)}]; }
    else st.faces=[];
    st.face_engine = c.face_engine||"lbph";   // DEMO 兩種引擎都當可用
    st.arc_available = true;
    // 障礙
    const thr=c.obstacle_dist||1.0;
    if(c.obstacle){
      const polys = S.regions.length?S.regions:[[[0,0],[W,0],[W,W],[0,W]]];
      let per=[], hit=false, mn=0;
      polys.forEach(poly=>{ const inside=pointIn(poly,f.cx,f.cy);
        const d=inside?0.6:2.6; const h=d<=thr; per.push({hit:h,dist:d});
        hit=hit||h; if(mn===0||d<mn)mn=d; });
      st.obstacle={on:true, hit, dist:mn, thr, regions:S.regions, per:S.regions.length?per:per};
    } else st.obstacle={on:false, hit:false, dist:0, thr, regions:S.regions, per:[]};
    return st;
  }

  // ---- 攔截 fetch ----
  const realFetch = window.fetch ? window.fetch.bind(window) : null;
  const jsonResp = o => new Response(JSON.stringify(o),
    {status:200, headers:{"Content-Type":"application/json"}});
  window.fetch = function(url, opt){
    try{
      const u = (typeof url==="string") ? url : (url && url.url) || "";
      const path = u.replace(location.origin,"").split("?")[0];
      const m = ((opt&&opt.method)||"GET").toUpperCase();
      if(path==="/status")  return Promise.resolve(jsonResp(buildStatus()));
      if(path==="/measure") return Promise.resolve(jsonResp({}));
      if(path==="/pointcloud.bin")
        return Promise.resolve(new Response(new Uint8Array(4).buffer,{status:200}));
      if(m==="POST"){
        let b={}; try{ b=JSON.parse((opt&&opt.body)||"{}"); }catch(e){}
        if(path==="/config") Object.assign(S.cfg, b);
        else if(path==="/obstacle/regions") S.regions = b.regions||[];
        else if(path==="/obstacle/clear")  S.regions = [];
        else if(path==="/face/enroll"){ const n=(b.name||"").trim();
          if(n){ S.cfg.face=true; S.faces[n]=(S.faces[n]||0)+1;
                 S.msg=`已擷取「${n}」第 ${S.faces[n]} 張樣本（DEMO）`; } }
        else if(path==="/face/delete"){ delete S.faces[(b.name||"").trim()]; }
        else if(path==="/face/clear"){ S.faces={}; }
        else if(path==="/face/rename"){ const o=(b.old||"").trim(), n=(b.new||"").trim();
          if(o&&n&&S.faces[o]!=null){ S.faces[n]=(S.faces[n]||0)+S.faces[o]; delete S.faces[o];
            S.msg=`已改名為「${n}」（DEMO）`; } }
        return Promise.resolve(jsonResp({ok:true}));
      }
      return Promise.resolve(jsonResp({ok:true}));
    }catch(e){ return realFetch ? realFetch(url,opt) : Promise.reject(e); }
  };

  // ---- DEMO 角標 ----
  const badge=document.createElement("div");
  badge.textContent="● DEMO 模擬資料（無後端）";
  badge.style.cssText="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:400;"+
    "font:600 12px 'Noto Sans TC',sans-serif;color:#140f08;background:#ff9a3c;"+
    "padding:5px 14px;border-radius:999px;box-shadow:0 4px 16px #0006;letter-spacing:.04em";
  document.body.appendChild(badge);

  render();                            // 先畫第一幀(設好 feed.src，蓋掉真 /stream)
  console.log("[demo] 模擬模式啟用");
})();
