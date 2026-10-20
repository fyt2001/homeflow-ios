const $ = id => document.getElementById(id);
let real = false, snapshot = null, plan = null, transaction = null, original, target, showing = 'before', page = 2, pendingAction = null, busy = false, reviewOnly = false;
const apiBase = 'http://127.0.0.1:27841';
const app = (name, id) => ({kind:'app',name,id:id || `example.${name}`});
const folder = (name, names) => ({kind:'folder',name,children:names.map(n=>app(n))});
const demoDock = [app('电话'),app('浏览器'),app('信息'),app('相机')];
original = [demoDock,[{kind:'widget',name:'天气'},{kind:'widget',name:'日历'},app('照片'),app('设置')],
['社交','音乐','出行','视频','笔记','文件','地图','阅读','日历','邮件','相册','播客','翻译','计算器','时钟','天气','运动','购物','银行','待办'].map(n=>app(n))];
const demo = structuredClone(original);
const colors=['#748faa','#8b9db6','#6b9e9a','#b7988f','#8194b6','#91a39b','#a29ab4','#b5a07e'];
const getColor = name => colors[[...name].reduce((a,c)=>a+c.codePointAt(0),0)%colors.length];
function message(text, error=false){ $('message').textContent=text; $('message').style.color=error?'#a75151':''; }
function count(layout){let apps=0,folders=0;function visit(a){if(a.kind==='app')apps++;if(a.kind==='folder'){folders++;a.children.forEach(visit)}}layout.flat().forEach(visit);return{apps,folders}}
function appButton(item){
 const b=document.createElement('button'); b.type='button'; b.disabled=busy; b.className='app'; b.title=item.name;
 const icon=document.createElement('span'); icon.className='icon'; icon.style.setProperty('--icon-color',getColor(item.name));
 if(item.kind==='folder'){icon.classList.add('folder-icon');item.children.slice(0,9).forEach(a=>{const i=document.createElement('i');i.style.setProperty('--mini-color',getColor(a.name));icon.append(i)})}else{icon.textContent=item.name.replace(/^com\./,'').slice(0,1)}
 const name=document.createElement('span');name.className='app-name';name.textContent=item.name;b.append(icon,name);
 b.onclick=()=>{if(item.kind==='folder')openFolder(item);else{const values=$('favorites').value.split('\n').map(x=>x.trim()).filter(Boolean);if(!values.includes(item.name)){values.push(item.name);$('favorites').value=values.join('\n');b.classList.add('selected');invalidatePlan();message(`已加入常用排序：${item.name}`)}}};return b;
}
function openFolder(item){$('folder-title').textContent=item.name;$('folder-items').replaceChildren(...item.children.map(appButton));$('folder-dialog').showModal()}
$('close-folder').onclick=()=>$('folder-dialog').close();
function render(){
 const layout=showing==='after'&&target?target:original; page=Math.max(1,Math.min(page,layout.length-1));
 const locked=layout[page]?.some(a=>a.kind.startsWith('widget'));$('grid').classList.toggle('locked',locked);
 $('grid').replaceChildren(...(layout[page]||[]).map(a=>{if(a.kind.startsWith('widget')){const w=document.createElement('div');w.className='widget';const s=document.createElement('span');s.textContent='▣ 小组件 · 已锁定';const strong=document.createElement('strong');strong.textContent=a.name==='天气'?'21°':a.name;w.append(s,strong);return w}return appButton(a)}));
 $('dock').replaceChildren(...layout[0].map(appButton));$('page-label').textContent=`${page} / ${layout.length-1}`;$('page-dots').textContent=layout.slice(1).map((_,i)=>i+1===page?'●':'·').join('');
 $('before').classList.toggle('active',showing==='before');$('after').classList.toggle('active',showing==='after');$('after').disabled=!target;
 $('prev').disabled=page<=1;$('next').disabled=page>=layout.length-1;const stats=count(layout);$('app-count').textContent=stats.apps;$('folder-count').textContent=stats.folders;
 $('apply').disabled=!real||!plan||busy||reviewOnly;$('restore').disabled=!transaction||busy;$('capture').disabled=!real||busy;$('generate').disabled=busy||real&&!snapshot;document.querySelectorAll('.preferences input,.preferences select,#favorites,#avoid,#more-compact,#more-spread').forEach(el=>el.disabled=busy);
}
function invalidatePlan(){plan=null;target=null;reviewOnly=false;showing='before';$('plan-notes').replaceChildren();render()}
async function request(path, body){const response=await fetch(apiBase+'/api/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+$('token').value.trim(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(120000)});let data;try{data=await response.json()}catch{throw Error('本机助手返回异常，请检查助手窗口。')}if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'请求参数不匹配，请刷新页面。');return data}
async function run(action){if(busy)return;busy=true;render();try{await action()}catch(e){message(e instanceof TypeError?'无法连接本机助手。请确认助手已启动；浏览器询问本地网络权限时选择允许，也可直接使用助手打开的本机页面。':e.message,true)}finally{busy=false;render()}}
$('connect').onclick=()=>run(async()=>{if(!$('token').value.trim())throw Error('请先输入本机助手显示的连接码。');const health=await request('health');if(!health.personalization)throw Error('请先更新本机助手到 0.2.0 或更新版本，以支持个性化方案。');real=true;original=[[],[]];target=null;snapshot=null;plan=null;transaction=null;$('status').textContent='本机助手已连接 · 等待读取';$('status-dot').style.background='#72a28c';$('mode-badge').textContent='本机模式';$('generate').textContent='生成整理方案 →';$('source-note').textContent='桌面只在你的浏览器和本机助手之间传输。';$('favorites').value='';message('连接成功。点击“读取并备份桌面”开始。')});
$('capture').onclick=()=>run(async()=>{message('正在读取桌面并保存本机快照…');const r=await request('capture',{});original=r.layout;snapshot=r.snapshot_id;target=null;plan=null;transaction=null;showing='before';page=Math.min(2,original.length-1);$('status').textContent=`iPhone 已读取 · iOS ${r.ios}`;$('preview-note').textContent='快照已保存在本机。点选常用应用，再生成方案。';message('读取完成。当前操作没有修改手机。')});
function findApps(layout){return layout.flat().flatMap(a=>a.kind==='folder'?a.children:a.kind==='app'?[a]:[])}
function preferences(){return {hand:$('hand').value,style:document.querySelector('input[name="style"]:checked').value,goal:$('goal').value,favorite_limit:Number($('favorite-limit').value),grouping:$('grouping').value,widgets:$('widgets').value}}
function values(id){return [...new Set($(id).value.split('\n').map(x=>x.trim()).filter(Boolean))]}
function demoPlan(prefs){
 if(prefs.goal==='familiar')return structuredClone(demo);
 const pool=structuredClone(demo[2]);const requested=values('favorites');const avoided=new Set(values('avoid'));
 const rank=a=>avoided.has(a.name)?999:requested.includes(a.name)?requested.indexOf(a.name):100;
 const groups={沟通:['社交','邮件'],生活:['出行','地图','购物','银行'],系统:['计算器','时钟','天气','翻译'],阅读:['阅读','播客'],效率:['笔记','文件','待办','日历'],影音:['视频','音乐'],其他:['相册','运动']};
 let items=[];
 if(prefs.style==='spread')items=pool.sort((a,b)=>rank(a)-rank(b));
 else{
  const cap=prefs.style==='compact'?Math.min(4,prefs.favorite_limit):prefs.favorite_limit;
  const promoted=requested.filter(n=>!avoided.has(n)).slice(0,cap);items=promoted.map(n=>pool.find(a=>a.name===n)).filter(Boolean);
  const remaining=pool.filter(a=>!items.includes(a));const bins=new Map();
  for(const a of remaining){const name=avoided.has(a.name)?'少打开':prefs.grouping==='original'?'其他应用':Object.keys(groups).find(k=>groups[k].includes(a.name));if(!bins.has(name))bins.set(name,[]);bins.get(name).push(a)}
  for(const [name,children] of [...bins].sort((a,b)=>(a[0]==='少打开')-(b[0]==='少打开')))items.push({kind:'folder',name,children});
 }
 const result=structuredClone(demo.slice(0,2));
 for(let start=0;start<items.length;start+=24){const chunk=items.slice(start,start+24);const rows=Math.ceil(chunk.length/4),anchor={left:1.5,right:3.5,both:2.5}[prefs.hand];const distance=i=>((Math.floor(i/4)+1-(rows-.6))/1.7)**2+((i%4+1-anchor)/1.4)**2;const slots=chunk.map((_,i)=>i).sort((a,b)=>distance(a)-distance(b));const out=Array(chunk.length);chunk.forEach((a,i)=>out[slots[i]]=a);result.push(out)}return result;
}
function showNotes(notes){$('plan-notes').replaceChildren(...notes.map(text=>{const li=document.createElement('li');li.textContent=text;return li}))}
$('generate').onclick=()=>run(async()=>{
 const prefs=preferences(),apps=findApps(original);const resolve=id=>values(id).map(n=>{const found=apps.find(a=>a.name===n||a.id===n);if(!found)throw Error('未找到应用：'+n+'。请使用预览中的名称。');return found.id});
 const favorites=resolve('favorites'),avoid=resolve('avoid');let notes=[];
 if(real){const r=await request('plan',{snapshot_id:snapshot,favorites,avoid,...prefs});target=r.layout;plan=r.plan_id;reviewOnly=r.review_only;notes=r.notes;}
 else{target=demoPlan(prefs);reviewOnly=prefs.widgets!=='preserve';notes=['虚构示例：全部 26 个图标保留，手机不会被修改。'];if(reviewOnly)notes.push(prefs.widgets==='review'?'组件精简建议：逐个确认天气、日历是否每天使用，当前未删除组件。':'组件规划建议：信息展示类靠上，交互类靠近操作手；当前未移动组件。');if(prefs.goal==='familiar')notes.push('保留熟悉感：本次不移动任何内容。')}
 const hand={left:'左手',right:'右手',both:'双手'}[prefs.hand],style={compact:'整合收纳',mixed:'常用直达',spread:'展开散放'}[prefs.style];
 notes.unshift(`${hand}操作 · ${style} · ${count(target).apps} 个应用图标 / ${count(target).folders} 个文件夹`);
 if(reviewOnly)notes.push('小组件调整尚未验证：该方案仅供评估，不能自动应用。');
 showNotes(notes);showing='after';page=Math.min(2,target.length-1);message(reviewOnly?'已生成评估预览。若要应用图标整理，请将小组件选择改为“保留原样”并重新生成。':real?'个性化方案已生成，请逐页对照预览。':'演示方案已生成。可以切换操作手和收纳风格比较效果。');
});
const preferenceIds=['hand','goal','widgets','favorite-limit','grouping'];
function preferenceChanged(){invalidatePlan();const prefs=preferences();$('widget-help').textContent=prefs.widgets==='preserve'?'小组件保持原样，可应用经过核对的图标方案。':'此选项用于小组件评估与手机操作建议，目前不会自动移动、删除或调整尺寸，生成后不能直接应用。';try{if($('remember').checked)localStorage.setItem('homeflow.preferences.v2',JSON.stringify(prefs));else localStorage.removeItem('homeflow.preferences.v2')}catch{}}
preferenceIds.forEach(id=>$(id).addEventListener('change',preferenceChanged));document.querySelectorAll('input[name="style"]').forEach(el=>el.addEventListener('change',preferenceChanged));$('remember').addEventListener('change',preferenceChanged);$('avoid').addEventListener('input',invalidatePlan);
$('more-compact').onclick=()=>{document.querySelector('input[name="style"][value="compact"]').checked=true;preferenceChanged();$('generate').click()};
$('more-spread').onclick=()=>{document.querySelector('input[name="style"][value="spread"]').checked=true;preferenceChanged();$('generate').click()};
try{const saved=JSON.parse(localStorage.getItem('homeflow.preferences.v2'));if(saved){const allowed={hand:['left','right','both'],goal:['speed','calm','familiar'],widgets:['preserve','review','replan'],grouping:['task','original'],'favorite-limit':['4','8','16']};for(const id of preferenceIds){const value=String(saved[id==='favorite-limit'?'favorite_limit':id]);if(allowed[id].includes(value))$(id).value=value}if(['compact','mixed','spread'].includes(saved.style))document.querySelector(`input[name="style"][value="${saved.style}"]`).checked=true;$('remember').checked=true}}catch{}
$('favorites').value='社交\n音乐\n出行\n视频';
$('widget-help').textContent=$('widgets').value==='preserve'?'小组件保持原样，可应用经过核对的图标方案。':'此选项仅提供小组件评估与手机操作建议，不能自动移动、删除或调整尺寸。';
$('favorites').addEventListener('input',invalidatePlan);
$('before').onclick=()=>{showing='before';render()};$('after').onclick=()=>{showing='after';render()};$('prev').onclick=()=>{page--;render()};$('next').onclick=()=>{page++;render()};
function confirm(action){pendingAction=action;$('consent').checked=false;$('confirm-write').disabled=true;$('confirm-title').textContent=action==='apply'?'应用这份桌面方案？':'恢复本次整理前的桌面？';$('confirm-write').textContent=action==='apply'?'确认应用':'确认恢复';$('confirm').showModal()}
$('apply').onclick=()=>confirm('apply');$('restore').onclick=()=>confirm('restore');$('consent').onchange=()=>$('confirm-write').disabled=!$('consent').checked;
$('confirm').addEventListener('close',()=>{if($('confirm').returnValue!=='confirm'||!$('consent').checked)return;run(async()=>{const action=pendingAction;message(action==='apply'?'正在写入并重新读取核对，请保持连接…':'正在恢复并重新读取核对…');const r=await request(action,action==='apply'?{plan_id:plan,accept_layout_risk:true}:{transaction_id:transaction,accept_layout_risk:true});if(action==='apply'){transaction=r.transaction_id;plan=null}else{transaction=null;target=null;original=r.layout;snapshot=null;showing='before'}message(action==='apply'?'应用完成，传统桌面布局读取核对通过。':'恢复完成，传统桌面布局读取核对通过。再次整理请重新读取。')})});
if(location.hash.startsWith('#token=')){ $('token').value=location.hash.slice(7);history.replaceState(null,'',location.pathname);$('connect').click() }
render();
