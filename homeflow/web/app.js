const $ = id => document.getElementById(id);
let real = false, snapshot = null, plan = null, transaction = null, original, target, showing = 'before', page = 2, pendingAction = null, busy = false;
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
 const b=document.createElement('button'); b.type='button'; b.className='app'; b.title=item.name;
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
 $('apply').disabled=!real||!plan||busy;$('restore').disabled=!transaction||busy;$('capture').disabled=!real||busy;$('generate').disabled=busy||real&&!snapshot;
}
function invalidatePlan(){plan=null;target=null;showing='before';render()}
async function request(path, body){const response=await fetch(apiBase+'/api/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+$('token').value.trim(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(120000)});let data;try{data=await response.json()}catch{throw Error('本机助手返回异常，请检查助手窗口。')}if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'请求参数不匹配，请刷新页面。');return data}
async function run(action){if(busy)return;busy=true;render();try{await action()}catch(e){message(e instanceof TypeError?'无法连接本机助手。请确认助手已启动；浏览器询问本地网络权限时选择允许，也可直接使用助手打开的本机页面。':e.message,true)}finally{busy=false;render()}}
$('connect').onclick=()=>run(async()=>{if(!$('token').value.trim())throw Error('请先输入本机助手显示的连接码。');await request('health');real=true;original=[[],[]];target=null;snapshot=null;plan=null;transaction=null;$('status').textContent='本机助手已连接 · 等待读取';$('status-dot').style.background='#72a28c';$('mode-badge').textContent='本机模式';$('generate').textContent='生成整理方案 →';$('source-note').textContent='桌面只在你的浏览器和本机助手之间传输。';$('favorites').value='';message('连接成功。点击“读取并备份桌面”开始。')});
$('capture').onclick=()=>run(async()=>{message('正在读取桌面并保存本机快照…');const r=await request('capture',{});original=r.layout;snapshot=r.snapshot_id;target=null;plan=null;transaction=null;showing='before';page=Math.min(2,original.length-1);$('status').textContent=`iPhone 已读取 · iOS ${r.ios}`;$('preview-note').textContent='快照已保存在本机。点选常用应用，再生成方案。';message('读取完成。当前操作没有修改手机。')});
function findApps(layout){return layout.flat().flatMap(a=>a.kind==='folder'?a.children:a.kind==='app'?[a]:[])}
$('generate').onclick=()=>run(async()=>{if(real){const apps=findApps(original);const names=$('favorites').value.split('\n').map(x=>x.trim()).filter(Boolean);const missing=names.filter(n=>!apps.some(a=>a.name===n||a.id===n));if(missing.length)throw Error('未找到这些应用，请从预览点选：'+missing.join('、'));const favorites=names.map(n=>apps.find(a=>a.name===n||a.id===n).id);const r=await request('plan',{snapshot_id:snapshot,favorites});target=r.layout;plan=r.plan_id;}else{target=[structuredClone(demoDock),structuredClone(demo[1]),[folder('沟通',[ '社交','邮件']),folder('生活',['出行','地图','购物','银行']),folder('系统',['计算器','时钟','天气','翻译']),folder('阅读',['阅读','播客']),folder('效率',['笔记','文件','待办','日历']),app('相册'),app('运动'),app('视频'),app('音乐')]];}showing='after';page=Math.min(2,target.length-1);message(real?'方案已生成。请逐页对照预览，再决定应用。':'演示方案已生成。所有示例图标保留；演示不会写入手机。')});
$('favorites').addEventListener('input',invalidatePlan);
$('before').onclick=()=>{showing='before';render()};$('after').onclick=()=>{showing='after';render()};$('prev').onclick=()=>{page--;render()};$('next').onclick=()=>{page++;render()};
function confirm(action){pendingAction=action;$('consent').checked=false;$('confirm-write').disabled=true;$('confirm-title').textContent=action==='apply'?'应用这份桌面方案？':'恢复本次整理前的桌面？';$('confirm-write').textContent=action==='apply'?'确认应用':'确认恢复';$('confirm').showModal()}
$('apply').onclick=()=>confirm('apply');$('restore').onclick=()=>confirm('restore');$('consent').onchange=()=>$('confirm-write').disabled=!$('consent').checked;
$('confirm').addEventListener('close',()=>{if($('confirm').returnValue!=='confirm'||!$('consent').checked)return;run(async()=>{const action=pendingAction;message(action==='apply'?'正在写入并重新读取核对，请保持连接…':'正在恢复并重新读取核对…');const r=await request(action,action==='apply'?{plan_id:plan,accept_layout_risk:true}:{transaction_id:transaction,accept_layout_risk:true});if(action==='apply'){transaction=r.transaction_id;plan=null}else{transaction=null;target=null;original=r.layout;snapshot=null;showing='before'}message(action==='apply'?'应用完成，传统桌面布局读取核对通过。':'恢复完成，传统桌面布局读取核对通过。再次整理请重新读取。')})});
if(location.hash.startsWith('#token=')){ $('token').value=location.hash.slice(7);history.replaceState(null,'',location.pathname);$('connect').click() }
render();
