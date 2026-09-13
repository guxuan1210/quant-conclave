// 短线策略分析 — multi-section frontend
var _charts={};
function dc(){Object.keys(_charts).forEach(function(k){if(_charts[k]){_charts[k].destroy();_charts[k]=null;}})}
var G='#1a7f37',R='#cf222e',B='#0969da',P='#8250df',Y='#9a6700',M='#656d76';
function e(id){return document.getElementById(id)}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

// ── Load ──

function load(){
  var src=e('sa-src').value,days=e('sa-days').value;
  e('sa-status').textContent='加载中...';e('sa-go').disabled=true;e('sa-llm').disabled=true;
  fetch('/api/strategy/full?source='+encodeURIComponent(src)+'&days='+days)
    .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
    .then(function(d){
      if(d.error){e('sa-status').textContent=d.error;e('sa-go').disabled=false;e('sa-llm').disabled=false;return}
      window._d=d;dc();
      try{
        window._allRecords = d.records||[];
        window._allEval = d.evaluation||{};
        // Populate model filter dropdown
        var models=new Set();(d.records||[]).forEach(function(r){if(r.model_name)models.add(r.model_name)});
        var msel=e('sa-model-filter');msel.innerHTML='<option value="all">全部模型 ('+models.size+'个)</option>';
        Array.from(models).sort().forEach(function(m){msel.innerHTML+='<option value="'+esc(m)+'">'+esc(m)+'</option>'});
        try{applyFilter()}catch(ex){e('sa-status').textContent='渲染失败: '+ex.message;console.error(ex)}
        renderDistChart(d.evaluation);
        renderModelChart(d.evaluation);
        renderStocksTable(d.evaluation);
        renderSectorChart(d.sectors);
        renderSectorWRChart(d.sectors);
        renderSectorTable(d.sectors);
        renderDailyChart(d.evaluation);
        renderVerdictChart(d.evaluation);
      }catch(ex){e('sa-status').textContent='图表渲染失败（Chart.js未加载？请检查网络）: '+ex.message;console.error(ex)}
      e('sa-status').textContent='更新: '+new Date().toLocaleTimeString()+' | '+d.evaluation.total_all+' 条记录 | 胜率 '+d.evaluation.win_rate+'% | 均益 '+(d.evaluation.avg_return>=0?'+':'')+d.evaluation.avg_return.toFixed(2)+'%';
      e('sa-go').disabled=false;e('sa-llm').disabled=false;
    }).catch(function(x){
      e('sa-status').textContent='加载失败 — 请确认已启动: python web/strategy_app.py (端口8006) — '+x.message;
      e('sa-go').disabled=false;e('sa-llm').disabled=false;
    });
    // Load daily breakdown independently (non-blocking)
    setTimeout(loadDailyBreakdown, 100);
}

// ── Today filter ──

function applyFilter(){
  var hideToday=e('sa-hide-today').checked,excludeHold=e('sa-exclude-hold').checked,bullOnly=e('sa-bull-only').checked;
  var modelFilter=e('sa-model-filter').value;
  var excludeCyb=e('sa-exclude-cyb').checked,excludeKcb=e('sa-exclude-kcb').checked,excludeBjs=e('sa-exclude-bjs').checked;
  // Single pass: filter + aggregate
  var recs=[],buyCount=0,sellCount=0,holdCount=0,
      wins=0,sumRet=0,countRet=0,rets=[];
  var mm={};
  (window._allRecords||[]).forEach(function(r){
    // "无收益": exclude records with virtually zero return (no trading opportunity since analysis)
    if(hideToday&&(r.return_pct==null||Math.abs(r.return_pct)<0.005))return;
    if(excludeHold&&r.verdict==='观望')return;
    if(bullOnly&&r.verdict!=='看多')return;
    if(modelFilter!=='all'&&r.model_name!==modelFilter)return;
    var c2=(r.code||'').toUpperCase().replace(/\.(SH|SZ|BJ)$/,'');
    if(excludeCyb&&(c2.startsWith('300')||c2.startsWith('301')))return;
    if(excludeKcb&&c2.startsWith('688'))return;
    if(excludeBjs&&(c2.startsWith('8')||c2.startsWith('9')))return;
    recs.push(r);
    if(r.verdict==='看多')buyCount++;else if(r.verdict==='看空')sellCount++;else holdCount++;
    if(r.return_pct!=null){wins+=(r.return_pct>0?1:0);sumRet+=r.return_pct;countRet++;rets.push(r.return_pct);
      var m=r.model_name||'?';mm[m]=mm[m]||{bull:[],bear:[],rets:[]};mm[m].rets.push(r.return_pct);
      if(r.verdict==='看多')mm[m].bull.push(r.return_pct);else if(r.verdict==='看空')mm[m].bear.push(r.return_pct);
    }
  });
  renderRecords(recs,0);
  if(countRet>0){
    var wr2=wins/countRet*100,ar2=sumRet/countRet;rets.sort(function(a,b){return a-b});
    var cum=rets.reduce(function(s,v){return s+v},0);var best=rets[rets.length-1],worst=rets[0];
    var stdev=0;if(rets.length>1){var s2=0;for(var i=0;i<rets.length;i++)s2+=Math.pow(rets[i]-ar2,2);stdev=Math.sqrt(s2/(rets.length-1));}
    var fev={total_all:recs.length,total:countRet,win_rate:wr2,avg_return:ar2,cum_return:cum,best_return:best,worst_return:worst,std_dev:stdev,sharpe:stdev>0?ar2/stdev:0,max_drawdown:0,buy_count:buyCount,sell_count:sellCount,hold_count:holdCount};
    renderTiles(fev);
    var mt=[];Object.keys(mm).forEach(function(m){
      var d=mm[m],n=d.rets.length;if(n>0){
        mt.push({model:m,count:n,
          win_rate:(d.rets.filter(function(v){return v>0}).length/n*100).toFixed(1),
          avg_return:(d.rets.reduce(function(s,v){return s+v},0)/n).toFixed(2),
          bull_count:d.bull.length,bull_wr:d.bull.length>0?(d.bull.filter(function(v){return v>0}).length/d.bull.length*100).toFixed(1):'-',
          bull_ar:d.bull.length>0?(d.bull.reduce(function(s,v){return s+v},0)/d.bull.length).toFixed(2):'-',
          bear_count:d.bear.length,bear_wr:d.bear.length>0?(d.bear.filter(function(v){return v>0}).length/d.bear.length*100).toFixed(1):'-',
          bear_ar:d.bear.length>0?(d.bear.reduce(function(s,v){return s+v},0)/d.bear.length).toFixed(2):'-'
        });
      }
    });
    mt.sort(function(a,b){return b.count-a.count});renderModelTable(mt);renderModelWRChart(mt);
    // Update charts from filtered data
    if(_charts.dist){_charts.dist.destroy();_charts.dist=null}
    if(rets.length>0){renderDistChartFromRets(rets)}
    if(_charts.model){_charts.model.destroy();_charts.model=null}
    if(mt.length>0){renderModelChartFromMT(mt)}
    if(_charts.verdict){_charts.verdict.destroy();_charts.verdict=null}
    renderVerdictFromCounts(buyCount,sellCount,holdCount);
    window._filteredEval=fev;window._filteredModels=mt;window._filteredRecs=recs;
  }else{renderTiles(window._allEval);renderModelTable([]);renderModelWRChart([]);renderDistChart(window._allEval);renderModelChart(window._allEval);renderVerdictChart(window._allEval);window._filteredEval=null;window._filteredModels=null;window._filteredRecs=[]}
  // Also store model names for prompt
  window._modelNames=[];
  if(window._filteredModels)window._filteredModels.forEach(function(m){window._modelNames.push(m.model+'('+m.count+'次/胜率'+m.win_rate+'%/均益'+m.avg_return+'%)')});
}
e('sa-hide-today').addEventListener('change', function(){applyFilter();applyDailyFilter()});
e('sa-exclude-hold').addEventListener('change', function(){applyFilter();applyDailyFilter()});
e('sa-bull-only').addEventListener('change', function(){applyFilter();applyDailyFilter()});
e('sa-model-filter').addEventListener('change', function(){applyFilter();loadDailyBreakdown()});
e('sa-exclude-cyb').addEventListener('change', function(){applyFilter();applyDailyFilter()});
e('sa-exclude-kcb').addEventListener('change', function(){applyFilter();applyDailyFilter()});
e('sa-exclude-bjs').addEventListener('change', function(){applyFilter();applyDailyFilter()});

// ── Daily breakdown ──

var _dailyData=[], _selectedDates=new Set();

var _sectorStocks={};
function showSectorDetail(sector){
  var stocks=_sectorStocks[sector]||[],panel=document.getElementById('sa-sector-detail');
  if(!panel){panel=document.createElement('div');panel.id='sa-sector-detail';panel.style.cssText='margin-top:8px;padding:8px;background:var(--panel);border:1px solid var(--border);border-radius:4px;font-size:11px;max-height:300px;overflow-y:auto;';e('ch-bull-sector').parentNode.appendChild(panel);}
  var h='<b>'+esc(sector)+' — 看多股票 ('+stocks.length+'只)</b><table style="width:100%;margin-top:4px;">';
  stocks.forEach(function(s){
    var c=s.return_pct!=null?(s.return_pct>=0?G:R):M;
    var rs=s.return_pct!=null?(s.return_pct>=0?'+':'')+s.return_pct.toFixed(2)+'%':'-';
    h+='<tr><td>'+esc(s.code)+'</td><td>'+esc(s.name||'')+'</td><td style="font-size:9px;">'+esc(s.model||'')+'</td><td style="color:'+c+';font-weight:600;">'+rs+'</td></tr>';
  });
  panel.innerHTML=h+'</table>';
}

function loadDailyBreakdown(){
  var tbody=e('sa-daily')?e('sa-daily').querySelector('tbody'):null;
  if(!tbody)return;tbody.innerHTML='<tr><td colspan="10">加载中...</td></tr>';
  var mf=e('sa-model-filter').value;
  var url='/api/strategy/daily-breakdown?source='+encodeURIComponent(e('sa-src').value)+'&days='+e('sa-days').value;
  if(mf!=='all')url+='&model='+encodeURIComponent(mf);
  fetch(url).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}).then(function(d){
    _dailyDataFull=d.daily||[];_sectorStocksFull=d.sector_stocks||{};_selectedDates.clear();
    applyDailyFilter();
  }).catch(function(x){ tbody.innerHTML='<tr><td colspan="10">加载失败: '+x.message+'</td></tr>'; });
}
function applyDailyFilter(){
  var exCyb=e('sa-exclude-cyb').checked,exKcb=e('sa-exclude-kcb').checked,exBjs=e('sa-exclude-bjs').checked;
  var hideToday=e('sa-hide-today').checked,excludeHold=e('sa-exclude-hold').checked,bullOnly=e('sa-bull-only').checked;
  var today=new Date().toISOString().slice(0,10);
  // Filter sector stocks
  var ss={};
  Object.keys(_sectorStocksFull||{}).forEach(function(k){
    var stocks=(_sectorStocksFull[k]||[]).filter(function(s){
      var c2=(s.code||'').toUpperCase().replace(/\.(SH|SZ|BJ)$/,'');
      if(exCyb&&(c2.startsWith('300')||c2.startsWith('301')))return false;
      if(exKcb&&c2.startsWith('688'))return false;
      if(exBjs&&(c2.startsWith('8')||c2.startsWith('9')))return false;
      return true;
    });
    if(stocks.length>0)ss[k]=stocks;
  });
  _sectorStocks=ss;
  // Filter daily data: apply same checkboxes as applyFilter
  var daily=(_dailyDataFull||[]).map(function(d){
    // Pro-rate: if excluding certain verdicts, reduce counts proportionally
    var total=d.total,看多=d.看多_count,看空=d.看空_count,观望=d.观望_count;
    if(excludeHold){total-=(观望||0);观望=0}
    if(bullOnly){total=看多;看空=0;观望=0}
    if(hideToday&&d.date===today)total=0;
    return {date:d.date,total:total,with_returns:d.with_returns,看多_count:看多,看空_count:看空,观望_count:观望,
            看多_win_rate:d.看多_win_rate,看多_avg_return:d.看多_avg_return,看空_win_rate:d.看空_win_rate,看空_avg_return:d.看空_avg_return,
            models:d.models,sectors:d.sectors,top_stocks_看多:d.top_stocks_看多};
  });
  _dailyData=daily;
  try{ renderDailyTable(_dailyData); renderBullFromDaily(); }
  catch(ex){ e('sa-daily').querySelector('tbody').innerHTML='<tr><td colspan="10">渲染失败: '+ex.message+'</td></tr>'; }
}

function renderDailyTable(daily){
  _dailyData=daily;
  var h='';daily.forEach(function(d){
    var chk=_selectedDates.has(d.date)?' checked':'';
    var bw=d.看多_win_rate>=50?G:R,sw=d.看空_win_rate>=50?G:R,ba=d.看多_avg_return>=0?G:R;
    var sectors=Object.keys(d.sectors||{}).slice(0,3).join(', ');
    h+='<tr><td style="text-align:center;"><input type="checkbox" class="sa-daily-chk" data-date="'+d.date+'"'+chk+' onchange="onDailyCheck(this)" onclick="event.stopPropagation()"></td><td><b>'+d.date+'</b></td><td>'+d.total+'</td><td style="color:'+G+';">'+d.看多_count+'</td><td style="color:'+R+';">'+d.看空_count+'</td><td>'+d.观望_count+'</td><td style="color:'+bw+';font-weight:600;">'+d.看多_win_rate+'%</td><td style="color:'+sw+';font-weight:600;">'+d.看空_win_rate+'%</td><td style="color:'+ba+';font-weight:600;">'+(d.看多_avg_return>=0?'+':'')+d.看多_avg_return.toFixed(2)+'%</td><td style="font-size:10px;">'+sectors+'</td></tr>';
  });
  var tbody=e('sa-daily').querySelector('tbody');tbody.innerHTML=h||'<tr><td colspan="10">无数据</td></tr>';
  var selCount=_selectedDates.size;
  var bar=document.getElementById('sa-daily-bar');if(!bar){bar=document.createElement('div');bar.id='sa-daily-bar';bar.style.cssText='margin-top:4px;font-size:11px;';tbody.parentNode.parentNode.appendChild(bar);}
  bar.innerHTML=selCount>0?'已选 <b>'+selCount+'</b> 天 <button onclick="_selectedDates.clear();renderDailyTable(_dailyData);renderBullFromDaily()" class="btn" style="padding:2px 8px;font-size:10px;">清除</button>':'';
}

function onDailyCheck(cb){
  var d=cb.dataset.date;
  if(cb.checked)_selectedDates.add(d);else _selectedDates.delete(d);
  e('sa-daily-all').checked=_selectedDates.size===_dailyData.length;
  renderBullFromDaily();
}
function toggleAllDates(checked){
  if(checked)_dailyData.forEach(function(d){_selectedDates.add(d.date)});else _selectedDates.clear();
  renderDailyTable(_dailyData);renderBullFromDaily();
}

function renderBullFromDaily(){
  var dates=_selectedDates.size>0?Array.from(_selectedDates):_dailyData.map(function(d){return d.date});
  var items=_dailyData.filter(function(d){return dates.indexOf(d.date)>=0});
  if(!items.length){e('sa-bull-tiles').innerHTML='<span style="color:var(--muted);">点击上方日期行选择分析范围</span>';return}
  // Aggregate selected dates
  var totalBull=0,totalBear=0,totalHold=0,totalRet=0,wins=0,sumRet=0;
  var modelMap={},trendMap={};
  items.forEach(function(d){
    totalBull+=d.看多_count;totalBear+=d.看空_count;totalHold+=d.观望_count;
    totalRet+=d.with_returns;
    var mds=d.models||{};Object.keys(mds).forEach(function(m){modelMap[m]=(modelMap[m]||0)+(mds[m].看多||0)});
    // Trend: use daily win rates weighted by count
    if(d.看多_count>0)trendMap[d.date]={count:d.看多_count,win_rate:d.看多_win_rate,avg_return:d.看多_avg_return};
  });
  var winRate=totalBull>0?Math.round(wins/totalBull*100):0;
  var avgRet=totalBull>0?Math.round(sumRet/totalBull*100)/100:0;
  var wc=winRate>=50?'g':'r',ac=avgRet>=0?'g':'r';
  e('sa-bull-tiles').innerHTML='<div class="tile b"><div class="num">'+totalBull+'</div><div class="lbl">看多信号 ('+items.length+'天)</div></div><div class="tile '+wc+'"><div class="num">'+winRate+'%</div><div class="lbl">看多胜率</div></div><div class="tile '+ac+'"><div class="num">'+(avgRet>=0?'+':'')+avgRet.toFixed(2)+'%</div><div class="lbl">看多均益</div></div><div class="tile y"><div class="num">'+(totalBull-totalRet)+'</div><div class="lbl">尚未交易</div></div>';
  // Sector chart — use _sectorStocks for consistency with click detail
  var secs=Object.entries(_sectorStocks).map(function(e){return [e[0],e[1].length]}).sort(function(a,b){return b[1]-a[1]});
  if(_charts.bullSector)_charts.bullSector.destroy();
  _charts.bullSector=new Chart(e('ch-bull-sector'),{type:'bar',data:{labels:secs.map(function(s){return s[0]}),datasets:[{label:'看多信号数',data:secs.map(function(s){return s[1]}),backgroundColor:B,borderRadius:4}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return '看多: '+c.raw+' 只'}}}},scales:{x:{title:{display:true,text:'看多信号数量（只）'}},y:{ticks:{font:{size:9},maxTicksLimit:60},afterFit:function(s){s.width=Math.max(s.width,100)}}},onClick:function(evt,els){if(els.length>0){var idx=els[0].index;var sector=secs[idx][0];showSectorDetail(sector)}}}});
  // Model chart
  var mds=Object.entries(modelMap).sort(function(a,b){return b[1]-a[1]});
  if(_charts.bullModel)_charts.bullModel.destroy();
  _charts.bullModel=new Chart(e('ch-bull-model'),{type:'doughnut',data:{labels:mds.map(function(m){return m[0].split('/').pop()}),datasets:[{data:mds.map(function(m){return m[1]}),backgroundColor:[B,P,G,Y,R,'#f0883e','#53d8fb','#ff6b6b','#48dbfb','#a371f7']}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{font:{size:9}}}}}});
  // Trend
  var tr=Object.entries(trendMap).sort(function(a,b){return a[0].localeCompare(b[0])});
  if(_charts.bullTrend)_charts.bullTrend.destroy();
  _charts.bullTrend=new Chart(e('ch-bull-trend'),{type:'line',data:{labels:tr.map(function(t){return t[0].slice(5)}),datasets:[{label:'胜率 %',data:tr.map(function(t){return t[1].win_rate}),borderColor:G,backgroundColor:G+'30',yAxisID:'y',pointRadius:4},{label:'均益 %',data:tr.map(function(t){return t[1].avg_return}),borderColor:B,backgroundColor:B+'30',yAxisID:'y1',pointRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:10}}}},scales:{y:{position:'left',min:0,max:100,ticks:{callback:function(v){return v+'%'}}},y1:{position:'right',grid:{drawOnChartArea:false},ticks:{callback:function(v){return v+'%'}}}}}});
}

// ── Init with self-check ──
e('sa-go').addEventListener('click',load);e('sa-llm').addEventListener('click',runLLM);
e('sa-src').addEventListener('change',function(){load();loadDailyBreakdown();});
e('sa-days').addEventListener('change',function(){load();loadDailyBreakdown();});
if(typeof Chart==='undefined'){e('sa-status').textContent='Chart.js 未加载 — 请检查网络连接（需要CDN）';e('sa-go').disabled=true}
else setTimeout(load,300);

// ── Records table (paginated) ──

var _recs=[],_sortCol=null,_sortDir=1,_page=0,_pageSize=200;
function renderRecords(recs,page){
  _recs=recs||[];if(page!=null)_page=page;
  var total=_recs.length,start=_page*_pageSize,end=_page<0?total:Math.min(start+_pageSize,total);
  var pageRecs=_page<0?_recs:_recs.slice(start,end);
  e('sa-rec-count').textContent='(共'+total+'条'+(total>_pageSize&&_page>=0?', 显示'+(start+1)+'-'+end:'')+')';
  var tbody=document.querySelector('#sa-records tbody'),frag=document.createDocumentFragment();
  pageRecs.forEach(function(r){
    var tr=document.createElement('tr');
    var vc=r.verdict==='看多'?G:r.verdict==='看空'?R:M;
    var vd=r.verdict==='看多'?'🟢 看多':r.verdict==='看空'?'🔴 看空':'⚪ 观望';
    var rc=r.return_pct!=null?(r.return_pct>=0?G:R):M;
    var rs=r.return_pct!=null?(r.return_pct>=0?'+':'')+r.return_pct.toFixed(2)+'%':'-';
    var cp=r.current_price!=null?r.current_price.toFixed(2):'-';
    var at=(r.analyzed_at||'').replace('T',' ').slice(5,16);
    tr.innerHTML='<td>'+esc(r.code)+'</td><td>'+esc(r.name||'')+'</td><td style="color:'+vc+';font-weight:600;">'+vd+'</td><td style="font-size:10px;">'+esc(r.model_name||'')+'</td><td style="font-size:10px;">'+at+'</td><td>'+esc(r.rt_price||'-')+'</td><td>'+cp+'</td><td style="color:'+rc+';font-weight:700;">'+rs+'</td>';
    frag.appendChild(tr);
  });
  tbody.innerHTML='';tbody.appendChild(frag);
  // Pagination (outside table, in parent .tbl div)
  var tbl=document.getElementById('sa-records'), pager=document.getElementById('sa-rec-pager');
  if(!pager){pager=document.createElement('div');pager.id='sa-rec-pager';tbl.parentNode.appendChild(pager);}
  if(total>_pageSize){
    var np=Math.ceil(total/_pageSize),h='<span style="font-size:11px;color:var(--muted);">'+(_page<0?total+'条全部显示':(_page+1)+'/'+np+'页')+'</span> ';
    if(_page>0)h+='<button onclick="renderRecords(_recs,'+(_page-1)+')" class="btn" style="padding:2px 8px;font-size:10px;">◀ 上一页</button> ';
    if(_page<np-1&&_page>=0)h+='<button onclick="renderRecords(_recs,'+(_page+1)+')" class="btn" style="padding:2px 8px;font-size:10px;">下一页 ▶</button> ';
    if(_page>=0)h+='<button onclick="renderRecords(_recs,-1)" class="btn" style="padding:2px 8px;font-size:10px;">显示全部</button>';else h+='<button onclick="renderRecords(_recs,0)" class="btn" style="padding:2px 8px;font-size:10px;">分页</button>';
    pager.innerHTML=h;
  }else{pager.innerHTML=''}
  document.querySelectorAll('#sa-records th').forEach(function(th){th.style.cursor='pointer';th.onclick=function(){_page=0;sortRecords(th.dataset.sort)}});
}
function sortRecords(col){
  if(_sortCol===col)_sortDir*=-1;else{_sortCol=col;_sortDir=1}
  _recs.sort(function(a,b){
    var va=a[col],vb=b[col];
    if(col==='return_pct'||col==='current_price'){va=va!=null?va:-Infinity;vb=vb!=null?vb:-Infinity}
    if(typeof va==='string')return va.localeCompare(vb||'')*_sortDir;
    return ((va||0)-(vb||0))*_sortDir;
  });
  renderRecords(_recs,0);
}

function renderModelTable(mt){
  var h='';
  mt.forEach(function(m){
    var wc=m.win_rate>=60?G:m.win_rate>=45?Y:R;
    var ac=m.avg_return>=0?G:R;
    h+='<tr><td><b>'+esc(m.model)+'</b></td><td>'+m.count+'</td><td style="color:'+wc+';font-weight:600;">'+m.win_rate+'%</td><td style="color:'+ac+';font-weight:600;">'+(m.avg_return>=0?'+':'')+m.avg_return+'%</td>';
    h+='<td>'+m.bull_count+' / '+m.bull_wr+'% / <span style="color:'+(m.bull_ar>=0?G:R)+';">'+(m.bull_ar>=0?'+':'')+m.bull_ar+'%</span></td>';
    h+='<td>'+m.bear_count+' / '+m.bear_wr+'% / <span style="color:'+(m.bear_ar>=0?G:R)+';">'+(m.bear_ar>=0?'+':'')+m.bear_ar+'%</span></td></tr>';
  });
  e('sa-model-tbl').querySelector('tbody').innerHTML=h||'<tr><td colspan="6" style="color:var(--muted);">无数据</td></tr>';
}

// ── Tiles ──

function renderTiles(ev){
  var html='';
  html+='<div class="tile b"><div class="num">'+ev.total_all+'</div><div class="lbl">总分析次数</div></div>';
  var wc=ev.win_rate>=50?'g':'r';
  html+='<div class="tile '+wc+'"><div class="num">'+ev.win_rate.toFixed(1)+'%</div><div class="lbl">胜率</div></div>';
  var ac=ev.avg_return>=0?'g':'r';
  html+='<div class="tile '+ac+'"><div class="num">'+(ev.avg_return>=0?'+':'')+ev.avg_return.toFixed(2)+'%</div><div class="lbl">平均收益</div></div>';
  var cc=ev.cum_return>=0?'g':'r';
  html+='<div class="tile '+cc+'"><div class="num">'+(ev.cum_return>=0?'+':'')+ev.cum_return.toFixed(2)+'%</div><div class="lbl">累计收益</div></div>';
  var sc=ev.sharpe>=1?'g':ev.sharpe>=0?'y':'r';
  html+='<div class="tile '+sc+'"><div class="num">'+ev.sharpe.toFixed(2)+'</div><div class="lbl">夏普比率</div></div>';
  var dc2=ev.max_drawdown<=5?'g':'r';
  html+='<div class="tile '+dc2+'"><div class="num">-'+ev.max_drawdown.toFixed(2)+'%</div><div class="lbl">最大回撤</div></div>';
  e('sa-tiles').innerHTML=html;
}

function renderModelWRChart(mt){
  if(_charts.modelWR)_charts.modelWR.destroy();
  if(!mt||!mt.length)return;
  var labels=mt.map(function(m){var p=m.model.split('/');return p[p.length-1]||m.model});
  var data=mt.map(function(m){return parseFloat(m.win_rate)});
  var colors=data.map(function(v){return v>=60?G:v>=45?Y:R});
  _charts.modelWR=new Chart(e('ch-model-wr'),{type:'bar',data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderRadius:4}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){var m=mt[c.dataIndex];return '胜率: '+m.win_rate+'% | 均益: '+m.avg_return+'% | '+m.count+'次'}}}},scales:{x:{max:100,ticks:{callback:function(v){return v+'%'}},title:{display:true,text:'胜率 %'}},y:{ticks:{font:{size:9},maxTicksLimit:60},afterFit:function(s){s.width=Math.max(s.width,120)}}}}});
}

function renderDistChartFromRets(rets){
  var buckets=[-20,-10,-5,-3,-1,0,1,3,5,10,20];
  var counts=new Array(buckets.length-1).fill(0);
  rets.forEach(function(r){for(var i=0;i<buckets.length-1;i++){if(r>=buckets[i]&&r<buckets[i+1]){counts[i]++;break}}});
  var labels=buckets.slice(0,-1).map(function(b,i){return b+'~'+buckets[i+1]+'%'});
  var colors=labels.map(function(l){return l.startsWith('-')?R:G});
  if(_charts.dist)_charts.dist.destroy();
  _charts.dist=new Chart(e('ch-dist'),{type:'bar',data:{labels:labels,datasets:[{data:counts,backgroundColor:colors,borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{title:{display:true,text:'次数'}},x:{title:{display:true,text:'收益率区间'}}}}});
}
function renderModelChartFromMT(mt){
  var labels=mt.map(function(m){var p=m.model.split('/');return p[p.length-1]||m.model});
  var data=mt.map(function(m){return parseFloat(m.win_rate)});
  var colors=data.map(function(v){return v>=60?G:v>=45?Y:R});
  var rets2=mt.map(function(m){return parseFloat(m.avg_return)});
  if(_charts.model)_charts.model.destroy();
  _charts.model=new Chart(e('ch-model'),{type:'bar',data:{labels:labels,datasets:[{label:'胜率 %',data:data,backgroundColor:colors,borderRadius:4,yAxisID:'y'},{label:'均益 %',data:rets2,type:'line',borderColor:B,backgroundColor:B+'30',yAxisID:'y1',pointRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{tooltip:{callbacks:{label:function(c){return c.dataset.label+': '+(c.raw>=0?'+':'')+c.raw.toFixed(1)+(c.datasetIndex===0?'%':'%')}}}},scales:{y:{position:'left',title:{display:true,text:'胜率 %'},max:100},y1:{position:'right',title:{display:true,text:'均益 %'},grid:{drawOnChartArea:false}}}}});
}
function renderVerdictFromCounts(b,s,h){
  if(_charts.verdict)_charts.verdict.destroy();
  _charts.verdict=new Chart(e('ch-verdict'),{type:'doughnut',data:{labels:['看多','看空','观望'],datasets:[{data:[b,s,h],backgroundColor:[G,R,M],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{font:{size:11}}}}}});
}

// ── Charts ──

function renderDistChart(ev){
  var dist=ev.return_dist||[];
  var labels=dist.map(function(d){return d.range});
  var data=dist.map(function(d){return d.count});
  var colors=labels.map(function(l){return l.startsWith('-')?R:G});
  _charts.dist=new Chart(e('ch-dist'),{
    type:'bar',data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderRadius:3}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{y:{title:{display:true,text:'次数'}},x:{title:{display:true,text:'收益率区间'}}}}
  });
}

function renderModelChart(ev){
  var models=ev.by_model||[];
  var labels=models.map(function(m){var p=m.model.split('/');return p[p.length-1]||m.model});
  var data=models.map(function(m){return m.win_rate});
  var colors=data.map(function(v){return v>=60?G:v>=45?Y:R});
  var rets=models.map(function(m){return m.avg_return});
  _charts.model=new Chart(e('ch-model'),{
    type:'bar',data:{labels:labels,datasets:[{label:'胜率 %',data:data,backgroundColor:colors,borderRadius:4,yAxisID:'y'},{label:'均益 %',data:rets,type:'line',borderColor:B,backgroundColor:B+'30',yAxisID:'y1',pointRadius:3}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{tooltip:{callbacks:{label:function(c){return c.dataset.label+': '+(c.raw>=0?'+':'')+c.raw.toFixed(1)+(c.datasetIndex===0?'%':'%')}}}},
      scales:{y:{position:'left',title:{display:true,text:'胜率 %'},max:100},y1:{position:'right',title:{display:true,text:'均益 %'},grid:{drawOnChartArea:false}}}}
  });
}

function renderSectorChart(se){
  var secs=se.slice(0,15);
  var labels=secs.map(function(s){return s.industry});
  var data=secs.map(function(s){return s.avg_return});
  var colors=data.map(function(v){return v>=0?G:R});
  _charts.sector=new Chart(e('ch-sector'),{
    type:'bar',data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderRadius:4}]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},
      tooltip:{callbacks:{label:function(c){var s=secs[c.dataIndex];return '均益: '+(s.avg_return>=0?'+':'')+s.avg_return.toFixed(2)+'% | 胜率: '+s.win_rate+'% | '+s.count+'次'}}}},
      scales:{x:{ticks:{callback:function(v){return v+'%'}}}}}
  });
}

function renderSectorWRChart(se){
  var secs=se.slice(0,15);
  var labels=secs.map(function(s){return s.industry});
  var data=secs.map(function(s){return s.win_rate});
  var colors=data.map(function(v){return v>=60?G:v>=45?Y:R});
  _charts.sectorWR=new Chart(e('ch-sector-wr'),{
    type:'bar',data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderRadius:4}]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{max:100,ticks:{callback:function(v){return v+'%'}}}}}
  });
}

function renderDailyChart(ev){
  var daily=ev.daily||[];
  var labels=daily.map(function(d){return d.date.slice(5)});
  var data=daily.map(function(d){return d.avg_return});
  var colors=data.map(function(v){return v>=0?G:R});
  var wrdata=daily.map(function(d){return d.win_rate});
  _charts.daily=new Chart(e('ch-daily'),{
    type:'bar',data:{labels:labels,datasets:[{label:'均益 %',data:data,backgroundColor:colors,borderRadius:2,yAxisID:'y'},{label:'胜率 %',data:wrdata,type:'line',borderColor:B,backgroundColor:B+'30',yAxisID:'y1',pointRadius:2}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:10}}}},
      scales:{y:{position:'left',ticks:{callback:function(v){return v+'%'}}},y1:{position:'right',min:0,max:100,grid:{drawOnChartArea:false},ticks:{callback:function(v){return v+'%'}}}}}
  });
}

function renderVerdictChart(ev){
  var b=ev.buy_count||0,s=ev.sell_count||0,h=ev.hold_count||0;
  _charts.verdict=new Chart(e('ch-verdict'),{
    type:'doughnut',data:{labels:['看多','看空','观望'],datasets:[{data:[b,s,h],backgroundColor:[G,R,M],borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'right',labels:{font:{size:11}}}}}
  });
}

// ── Tables ──

function renderStocksTable(ev){
  var top=ev.top_stocks||[],bot=ev.bottom_stocks||[];
  var h='<table><tr><th>代码</th><th>名称</th><th>次数</th><th>胜率</th><th>均益</th><th>分组</th></tr>';
  top.forEach(function(s){var c=s.avg_return>=0?G:R;h+='<tr><td>'+esc(s.code)+'</td><td>'+esc(s.name||'')+'</td><td>'+s.count+'</td><td>'+s.win_rate+'%</td><td style="color:'+c+';font-weight:600;">'+(s.avg_return>=0?'+':'')+s.avg_return.toFixed(2)+'%</td><td style="color:'+G+';">🏆 最佳</td></tr>'});
  bot.forEach(function(s){var c=s.avg_return>=0?G:R;h+='<tr><td>'+esc(s.code)+'</td><td>'+esc(s.name||'')+'</td><td>'+s.count+'</td><td>'+s.win_rate+'%</td><td style="color:'+c+';font-weight:600;">'+(s.avg_return>=0?'+':'')+s.avg_return.toFixed(2)+'%</td><td style="color:'+R+';">⚠️ 最差</td></tr>'});
  h+='</table>';e('sa-stocks').innerHTML=h;
}

function renderSectorTable(se){
  var h='<table><tr><th>行业</th><th>分析次数</th><th>股票数</th><th>胜率</th><th>平均收益</th><th>代表股</th></tr>';
  se.forEach(function(s){
    var c=s.avg_return>=0?G:R;var reps=s.top_stocks? s.top_stocks.slice(0,3).map(function(x){return esc(x.code)}).join(', ') : '';
    h+='<tr><td><b>'+esc(s.industry)+'</b></td><td>'+s.count+'</td><td>'+s.stock_count+'</td><td>'+s.win_rate+'%</td><td style="color:'+c+';font-weight:600;">'+(s.avg_return>=0?'+':'')+s.avg_return.toFixed(2)+'%</td><td style="font-size:10px;">'+reps+'</td></tr>';
  });
  h+='</table>';e('sa-sector-tbl').innerHTML=h;
}

// ── LLM ──

function runLLM(){
  var fev=window._filteredEval,frecs=window._filteredRecs||[];
  if(!fev||frecs.length===0){e('sa-status').textContent='请先刷新分析数据（当前无有效收益记录）';return}
  e('sa-llm').disabled=true;e('sa-llm-content').innerHTML='<p style="color:var(--muted);">正在调用 AI 模型分析当前筛选结果...</p>';
  e('sa-llm-panel').classList.add('show');e('sa-llm-model').textContent='';
  // Build context
  var srcLabel=e('sa-src').options[e('sa-src').selectedIndex].text,daysLabel=e('sa-days').options[e('sa-days').selectedIndex].text;
  var mf=e('sa-model-filter').value;
  var filterDesc=[];
  if(e('sa-hide-today').checked)filterDesc.push('排除未交易记录');
  if(e('sa-exclude-hold').checked)filterDesc.push('排除观望');
  if(e('sa-bull-only').checked)filterDesc.push('仅看多信号');
  if(mf!=='all')filterDesc.push('模型: '+mf);
  var models=window._filteredModels||[],modelLines=models.map(function(m){return '| '+m.model+' | '+m.count+' | '+m.win_rate+'% | '+m.avg_return+'% | '+m.bull_count+'/'+m.bull_wr+'%/'+m.bull_ar+'% | '+m.bear_count+'/'+m.bear_wr+'%/'+m.bear_ar+'% |'}).join('\n');
  var top10=frecs.filter(function(r){return r.return_pct!=null}).sort(function(a,b){return (b.return_pct||0)-(a.return_pct||0)}).slice(0,10).map(function(r){return r.code+' '+r.name+' | '+r.verdict+' | '+(r.return_pct>=0?'+':'')+r.return_pct.toFixed(2)+'% | '+r.model_name}).join('\n');
  var prompt='你是A股量化策略分析师。请基于以下短线策略的真实复盘数据，撰写一份结构化分析报告。\n\n';
  prompt+='## 数据背景\n- 数据来源: '+srcLabel+'\n- 时间范围: '+daysLabel+'\n- 筛选条件: '+(filterDesc.length?filterDesc.join(', '):'无')+'\n';
  prompt+='- 有效信号数: '+fev.total+' (总记录'+fev.total_all+')\n\n';
  prompt+='## 策略绩效\n| 指标 | 数值 |\n|------|------|\n| 胜率 | '+fev.win_rate.toFixed(1)+'% |\n| 平均收益 | '+(fev.avg_return>=0?'+':'')+fev.avg_return.toFixed(2)+'% |\n| 累计收益 | '+(fev.cum_return>=0?'+':'')+fev.cum_return.toFixed(2)+'% |\n| 夏普比率 | '+fev.sharpe.toFixed(2)+' |\n| 波动率 | '+fev.std_dev.toFixed(2)+'% |\n| 看多/看空/观望 | '+fev.buy_count+'/'+fev.sell_count+'/'+fev.hold_count+' |\n\n';
  prompt+='## 模型表现\n| 模型 | 次数 | 胜率 | 均益 | 看多(次/胜率/均益) | 看空(次/胜率/均益) |\n|------|------|------|------|------|------|\n'+modelLines+'\n\n';
  prompt+='## TOP10 收益榜\n'+top10+'\n\n';
  prompt+='请按以下结构输出分析报告（中文，无需markdown标记，用数字标题）：\n\n';
  prompt+='一、策略总体评价\n(整体有效性判断，胜率vs市场基准50%，夏普是否为正，累计收益是否可观，1-2段)\n\n';
  prompt+='二、模型对比分析\n(哪个模型综合表现最优？看多方向谁最强？看空方向谁最强？不同模型的风格差异，1-2段)\n\n';
  prompt+='三、风险与改进\n(主要亏损来源，什么市况下策略容易失效，可改进的方向，1-2段)\n\n';
  prompt+='四、操作建议\n(基于当前数据的实战建议：推荐模型、关注方向、仓位策略，1段)\n\n';
  prompt+='注意：直接输出中文内容，不要用#符号。引用具体数据，客观分析。';
  var pv=e('sa-provider').value,md=e('sa-model').value;
  // Store report for DOCX download
  window._llmPrompt=prompt;
  fetch('/api/strategy/llm-report',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({evaluation:fev,sectors:window._d?window._d.sectors:[],macro:{},provider:pv,model:md,prompt:prompt})
  }).then(function(r){return r.json()}).then(function(d){
    window._llmReport=d.report||'';window._llmModel=d.model_name||'';
    var html=simpleMD(window._llmReport);
    html+='<div style="margin-top:12px;"><button onclick="downloadLLMDocx()" class="btn pri" style="background:#6f42c1;font-size:11px;">📥 下载 DOCX</button></div>';
    e('sa-llm-content').innerHTML=html;e('sa-llm-model').textContent='🤖 '+(d.model_name||'');e('sa-llm').disabled=false;
  }).catch(function(x){e('sa-llm-content').innerHTML='<p style="color:var(--red);">调用失败: '+x.message+'</p>';e('sa-llm').disabled=false})
}

function downloadLLMDocx(){
  if(!window._llmReport){return}
  var srcLabel=e('sa-src').options[e('sa-src').selectedIndex].text;
  fetch('/api/strategy/llm-report-docx',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({report:window._llmReport,title:'策略分析报告 — '+srcLabel,model:window._llmModel||''})
  }).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.blob()}).then(function(blob){
    var url=URL.createObjectURL(blob);var a=document.createElement('a');a.href=url;
    a.download='strategy-report-'+new Date().toISOString().slice(0,10)+'.docx';a.click();URL.revokeObjectURL(url);
  }).catch(function(x){console.error('DOCX failed:',x)});
}

function simpleMD(t){
  if(!t)return'';t=t.replace(/<script[\s\S]*?<\/script>/gi,'').replace(/<style[\s\S]*?<\/style>/gi,'').replace(/on\w+="[^"]*"/gi,'').replace(/on\w+='[^']*'/gi,'').replace(/<iframe[\s\S]*?<\/iframe>/gi,'');
  var lines=t.replace(/\r\n/g,'\n').split('\n');var o='',it=false,il=false;
  lines.forEach(function(l){
    var x=l.trim();
    if(x.startsWith('|')&&x.includes('|')){if(!it){o+='<table>';it=true}
      var cs=x.split('|').filter(function(c){return c.trim()});if(cs.every(function(c){return/^[-: ]+$/.test(c.trim())}))return;
      o+='<tr>'+cs.map(function(c){return'<td>'+esc(c.trim())+'</td>'}).join('')+'</tr>'}
    else{if(it){o+='</table>';it=false}if(il&&!x.startsWith('- ')&&!x.startsWith('* ')&&!x.match(/^\d+[.)]\s/)){o+='</ul>';il=false}
      if(x.startsWith('###'))o+='<h3>'+esc(x.replace(/^#+\s*/,''))+'</h3>';
      else if(x.startsWith('##'))o+='<h2>'+esc(x.replace(/^#+\s*/,''))+'</h2>';
      else if(x.startsWith('#'))o+='<h2>'+esc(x.replace(/^#+\s*/,''))+'</h2>';
      else if(x.startsWith('- ')||x.startsWith('* ')){if(!il){o+='<ul>';il=true}o+='<li>'+infmt(x.slice(2))+'</li>'}
      else if(x.match(/^\d+[.)]\s/)){if(!il){o+='<ol>';il=true}o+='<li>'+infmt(x.replace(/^\d+[.)]\s/,''))+'</li>'}
      else if(x)o+='<p>'+infmt(x)+'</p>'}});
  if(it)o+='</table>';if(il)o+='</ul>';return o}
function infmt(t){return esc(t).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\*(.+?)\*/g,'<em>$1</em>').replace(/`(.+?)`/g,'<code>$1</code>')}

// (init block at top of file)
