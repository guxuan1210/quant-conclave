(function(){
"use strict";
var cp = document.getElementById("calibration-panel");
if(!cp) return;

function loadPanel(){
  // Latest run
  fetch("/api/calibration/runs/latest")
  .then(function(r){return r.json()})
  .then(function(d){
    if(!d||!d.id){
      document.getElementById("cal-last-run").textContent="No runs yet. Click Run Calibration to start.";
      return;
    }
    var s=JSON.parse(d.stats||"{}");
    var h="";
    h+="Last run: "+(d.run_date||"").slice(0,10);
    h+=" | R: "+d.resolved_count+"/"+d.total_pending;
    document.getElementById("cal-last-run").textContent=h;
    // Proposals
    var p=JSON.parse(d.proposals||"[]");
    var ph="";
    for(var i=0;i<p.length;i++){
      var r=p[i];
      ph+="<div style='padding:3px 0;font-size:10px;border-bottom:1px solid var(--border);'>";
      ph+="<span>"+r.rule+": "+r.from+" -> "+r.to+"</span>";
      ph+=' <button class="cal-apply-btn" data-rule="'+r.rule+'" data-field="'+r.field+'" style="float:right;font-size:9px;padding:1px 5px;cursor:pointer;background:var(--accent);color:#fff;border:none;border-radius:3px;">Apply</button>';
      ph+="</div>";
    }
    document.getElementById("cal-proposals").innerHTML=ph;
  }).catch(function(){
    document.getElementById("cal-last-run").textContent="No runs yet.";
  });
  // Timeline
  fetch("/api/calibration/runs")
  .then(function(r){return r.json()})
  .then(function(runs){
    var h="";
    for(var i=0;i<Math.min(runs.length,12);i++){
      var rn=runs[i];
      var s=JSON.parse(rn.stats||"{}");
      h+='<div class="cal-timeline-item" data-id="'+rn.id+'" style="padding:2px 4px;font-size:9px;cursor:pointer;border-bottom:1px solid var(--border);border-radius:2px;">';
      h+="<span style='font-weight:600;'>"+(rn.run_date||"").slice(0,10)+"</span>";
      h+=" <span style='color:var(--text-muted);float:right;'>R:"+rn.resolved_count+" P:"+s.meta_proposals+"</span>";
      h+="</div>";
    }
    document.getElementById("cal-timeline-list").innerHTML=h;
  });
}

// Event delegation
document.addEventListener("click",function(e){
  var t=e.target;
  // Proposal apply
  if(t.classList.contains("cal-apply-btn")){
    fetch("/api/calibration/proposal/approve",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({rule_name:t.getAttribute("data-rule"),field_name:t.getAttribute("data-field")})
    }).then(function(){loadPanel();});
  }
  // Timeline item click
  if(t.classList.contains("cal-timeline-item")){
    var id=t.getAttribute("data-id");
    fetch("/api/calibration/runs/"+id)
    .then(function(r){return r.json()})
    .then(function(d){
      var s=JSON.parse(d.stats||"{}");
      var props=JSON.parse(d.proposals||"[]");
      var h="<div style='font-size:10px;padding:4px;background:var(--surface);border:1px solid var(--border);border-radius:4px;'>";
      h+="<div><span style='font-weight:600;'>"+d.run_date.slice(0,16)+"</span></div>";
      h+="<div>Trigger: "+d.trigger+"</div>";
      h+="<div>Resolved: "+d.resolved_count+"/"+d.total_pending+"</div>";
      h+="<div>Proposals: "+s.meta_proposals+"</div>";
      h+="<div>Experiences: "+s.experiences+"</div>";
      if(props.length>0){
        h+="<div style='margin-top:4px;'><span style='font-weight:600;'>Proposals:</span>";
        for(var i=0;i<props.length;i++){
          h+="<div style='font-size:9px;'>"+props[i].rule+": "+props[i].from+" -> "+props[i].to+"</div>";
        }
        h+="</div>";
      }
      h+="</div>";
      document.getElementById("cal-timeline-list").innerHTML=h;
    });
  }
});

window.triggerCalibration=function(){
  var btn=document.querySelector("#cal-actions button:first-child");
  btn.textContent="Running...";btn.disabled=true;
  fetch("/api/calibration/run",{method:"POST"})
  .then(function(r){return r.json()}).then(function(){
    loadPanel();btn.textContent="Run Calibration";btn.disabled=false;
  });
};

window.extractExperiences=function(){
  var btn=document.querySelector("#cal-actions button:nth-child(2)");
  btn.textContent="Extracting...";btn.disabled=true;
  fetch("/api/advisory/experiences/extract",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({})
  })
  .then(function(r){return r.json()}).then(function(d){
    alert("Extracted "+d.proposals+" experience pattern(s). Check Experience Library.");
    loadPanel();btn.textContent="Extract Experience";btn.disabled=false;
  }).catch(function(){
    alert("Extraction failed.");btn.textContent="Extract Experience";btn.disabled=false;
  });
};

if(document.readyState==="loading"){
  document.addEventListener("DOMContentLoaded",loadPanel);
}else{loadPanel();}
})();
