import * as maplibregl from 'https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs';
const API='/api';let token=localStorage.getItem('dt_token'),refreshToken=localStorage.getItem('dt_refresh'),refreshPromise=null,user=null,map=null,assets=[],observations=[],selectedAsset=null,view='assets',assetsVisible=true,obsVisible=true,pendingPoint=null,observationCapturedAt=null,markers=new Map(),obsMarkers=[],ws=null;
const $=s=>document.querySelector(s);function authHeaders(){return {'Authorization':'Bearer '+token,'Content-Type':'application/json'}}async function renewSession(){if(refreshPromise)return refreshPromise;refreshPromise=(async()=>{let r=await fetch(API+'/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refreshToken})});if(!r.ok)throw Error('Session expired');let d=await r.json();token=d.access_token;refreshToken=d.refresh_token;localStorage.setItem('dt_token',token);localStorage.setItem('dt_refresh',refreshToken)})().finally(()=>{refreshPromise=null});return refreshPromise}async function api(path,opts={},retried=false){let r=await fetch(API+path,{...opts,headers:{...authHeaders(),...(opts.headers||{})}});if(r.status===401){if(!retried&&refreshToken&&!path.includes('/auth/')){try{await renewSession();return api(path,opts,true)}catch{}}logout();throw Error('Session expired')}if(!r.ok){let e=await r.json().catch(()=>({detail:'Request failed'}));throw Error(e.detail||'Request failed')}return r.status===204?null:r.json()}
function toast(msg){let t=$('#toast');t.textContent=msg;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',3500)}function setLogin(on){$('#login').style.display=on?'grid':'none'}function logout(){clearTrip();setPinMode(false);closeModal();let oldRefresh=refreshToken;if(oldRefresh)fetch(API+'/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:oldRefresh})}).catch(()=>{});localStorage.removeItem('dt_token');localStorage.removeItem('dt_refresh');token=null;refreshToken=null;user=null;setLogin(true);stopLive();clearInterval(assetPoll);clearInterval(observationPoll)}
async function signIn(){try{let data=await fetch(API+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:$('#email').value,password:$('#password').value})}).then(async r=>{let d=await r.json();if(!r.ok)throw Error(d.detail||'Sign in failed');return d});token=data.access_token;refreshToken=data.refresh_token;localStorage.setItem('dt_token',token);localStorage.setItem('dt_refresh',refreshToken);start()}catch(e){$('#loginError').textContent=e.message}}
$('#loginBtn').onclick=signIn;$('#password').onkeydown=e=>{if(e.key==='Enter')signIn()};$('#seedBtn').onclick=async()=>{try{let x=await fetch(API+'/dev/seed',{method:'POST'}).then(r=>r.json());toast(x.message||'Demo workspace initialized');await signIn()}catch(e){$('#loginError').textContent='Could not initialize demo workspace'}};$('#signout').onclick=logout;
function initMap(){if(map)return;map=new maplibregl.Map({container:'map',style:'https://tiles.openfreemap.org/styles/dark',center:[-104.99,39.739],zoom:11});map.addControl(new maplibregl.NavigationControl(),'top-right');map.on('load',()=>{refreshAssets();refreshObs()});map.on('contextmenu', e => openObservation([e.lngLat.lng, e.lngLat.lat]));map.on('click', e => {if(pinMode)openObservation([e.lngLat.lng, e.lngLat.lat])})}
function makeMarker(asset){let el=document.createElement('div');el.style.cssText='width:17px;height:17px;border-radius:50%;border:3px solid #eef7fb;box-shadow:0 0 0 5px #59d8bc44;cursor:pointer;background:#55dab9';let m=new maplibregl.Marker({element:el}).setLngLat([asset.lng,asset.lat]).setPopup(new maplibregl.Popup({offset:16}).setHTML(`<b>${esc(asset.name)}</b><br><span>${asset.status} · ${asset.speed||0} km/h</span>`)).addTo(map);el.onclick=()=>{selectedAsset=asset.id;renderSidebar()};return m}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refreshAssets(){try{assets=await api('/assets');renderSidebar();for(let a of assets){if(a.lat==null)continue;let marker=markers.get(a.id);if(marker)marker.setLngLat([a.lng,a.lat]);else markers.set(a.id,makeMarker(a));let el=markers.get(a.id).getElement();let color=a.status==='moving'?'#55dab9':a.status==='stopped'?'#f2b95d':'#718394';el.style.background=color;el.style.boxShadow=`0 0 0 5px ${color}44`;if(!assetsVisible)el.style.display='none'} $('#mapStatus').textContent=`${assets.length} assets · updated ${new Date().toLocaleTimeString()}`}catch(e){}}
function renderSidebar(){let counts={moving:0,stopped:0,offline:0};assets.forEach(a=>counts[a.status]++);$('#movingCount').textContent=counts.moving;$('#stoppedCount').textContent=counts.stopped;$('#offlineCount').textContent=counts.offline;let root=$('#items');if(view==='history'&&root.querySelector('#tripForm'))return;if(view==='assets'){let q=$('#search').value.toLowerCase();let list=assets.filter(a=>a.name.toLowerCase().includes(q));root.innerHTML=list.map(a=>`<div class="asset ${a.id===selectedAsset?'sel':''}" data-id="${a.id}"><i class="assetdot ${a.status}"></i><div class="assetinfo"><div class="assetname">${esc(a.name)}</div><div class="assetmeta">${esc(a.kind)} · ${a.recorded_at?new Date(a.recorded_at).toLocaleTimeString():'No GPS fix'}</div></div><span class="speed">${a.speed?Math.round(a.speed)+' km/h':''}</span></div>`).join('')||'<div class="empty">No assets match your search.</div>';root.querySelectorAll('.asset').forEach(el=>el.onclick=()=>{selectedAsset=+el.dataset.id;let a=assets.find(x=>x.id===selectedAsset);if(a?.lat)map.flyTo({center:[a.lng,a.lat],zoom:14});renderSidebar()});return}if(view==='observations'){root.innerHTML=observations.map(o=>`<div class="asset" data-id="${o.id}"><i class="assetdot" style="background:#70a7ff"></i><div class="assetinfo"><div class="assetname">${esc(o.note||'Observation pin')}</div><div class="assetmeta">${esc(o.author)} · ${new Date(o.observed_at).toLocaleString()}</div></div></div>`).join('')||'<div class="empty">No observations yet. Right-click the map to add one.</div>';root.querySelectorAll('.asset').forEach(el=>el.onclick=()=>{let o=observations.find(x=>x.id==el.dataset.id);map.flyTo({center:[o.lng,o.lat],zoom:15})});return}if(view==='alerts'){api('/alerts').then(rows=>{if(view!=='alerts')return;root.innerHTML=rows.map(a=>`<div class="asset"><i class="assetdot" style="background:#f2b95d"></i><div class="assetinfo"><div class="assetname">${esc(a.message)}</div><div class="assetmeta">${new Date(a.created_at).toLocaleString()}</div></div></div>`).join('')||'<div class="empty">No alerts. Geofence events will appear here.</div>'});return}root.innerHTML='<div class="empty">Select an asset to browse up to 30 days of trip history.</div>'}
function pendingObservationQueue(){try{let value=JSON.parse(localStorage.getItem('dt_pending_observations')||'[]');if(!Array.isArray(value))return [];return value.filter(o=>o&&typeof o.client_id==='string'&&typeof o.note==='string'&&Number.isFinite(o.lat)&&o.lat>=-90&&o.lat<=90&&Number.isFinite(o.lng)&&o.lng>=-180&&o.lng<=180&&Number.isInteger(o.user_id)&&Number.isInteger(o.org_id)).slice(0,1000)}catch{return []}}
function savePendingObservationQueue(queue){localStorage.setItem('dt_pending_observations',JSON.stringify(queue))}
async function refreshObs(){try{observations=await api('/observations')}catch{}const queued=pendingObservationQueue().filter(o=>user&&o.user_id===user.id&&o.org_id===user.org_id).map(o=>({...o,id:`pending-${o.client_id}`,author:'You · waiting to sync',pending:true}));observations=[...observations,...queued];obsMarkers.forEach(m=>m.remove());obsMarkers=[];for(let o of observations){let el=document.createElement('div');el.style.cssText=`width:14px;height:14px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${o.pending?'#e8bf68':'#70a7ff'};border:2px solid white;box-shadow:0 2px 8px #0008;cursor:pointer`;let html=`<div class="obs-popup"><b>${esc(o.note||'Observation')}</b><br>${esc(o.author||'You')}${o.pending?'<br>Waiting for connection':''}<br>${new Date(o.observed_at).toLocaleString()}</div>`;let m=new maplibregl.Marker({element:el}).setLngLat([o.lng,o.lat]).setPopup(new maplibregl.Popup({offset:12}).setHTML(html)).addTo(map);if(!obsVisible)el.style.display='none';obsMarkers.push(m)}if(view==='observations')renderSidebar()}
async function flushPendingObservations(){if(!navigator.onLine||!token||!user)return;let queue=pendingObservationQueue(),synced=0;for(const item of queue){if(item.user_id!==user.id||item.org_id!==user.org_id)continue;try{const {user_id,org_id,...payload}=item;await api('/observations',{method:'POST',body:JSON.stringify(payload)});queue=queue.filter(x=>x.client_id!==item.client_id);savePendingObservationQueue(queue);synced++}catch{return}}if(synced){await refreshObs();if(view==='observations')toast(`${synced} queued observation${synced===1?'':'s'} synced`)}}
window.addEventListener('online',flushPendingObservations);
let pinMode = false;
function setPinMode(active) {
    pinMode = active;
    $('#dropPin').setAttribute('aria-pressed', String(active));
    $('#dropPin').textContent = active ? 'Cancel pin placement' : '＋ Drop observation';
    if (map) map.getCanvas().style.cursor = active ? 'crosshair' : '';
}

function openObservation(point){
    if (!user) return;
    setPinMode(false);
    stopTrip();
    pendingPoint = point;
    observationCapturedAt = new Date().toISOString();
    $('#modalSave').onclick = saveObservation;
    $('#modalSave').textContent = 'Save';
    $('#modalTitle').textContent='Drop an observation';$('#modalSub').textContent=`${point[1].toFixed(5)}, ${point[0].toFixed(5)} · ${new Date(observationCapturedAt).toLocaleString()}`;$('#modalFields').innerHTML=`<label class="field">Note<textarea id="obsNote" maxlength="2000" placeholder="What did you observe?"></textarea></label><label class="field">Related asset (optional)<select id="obsAsset"><option value="">No asset linked</option>${assets.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>`;$('#modalback').style.display='grid';setTimeout(()=>$('#obsNote')?.focus(),50)}function closeModal(){$('#modalback').style.display='none'}$('#modalClose').onclick=$('#modalCancel').onclick=closeModal;$('#modalSave').onclick=async()=>{let item={client_id:crypto.randomUUID(),lat:pendingPoint[1],lng:pendingPoint[0],note:$('#obsNote').value,asset_id:$('#obsAsset').value?+$('#obsAsset').value:null,observed_at:observationCapturedAt,user_id:user.id,org_id:user.org_id};try{const {user_id,org_id,...payload}=item;await api('/observations',{method:'POST',body:JSON.stringify(payload)});closeModal();await refreshObs();toast('Observation saved to your organization')}catch(e){if(!navigator.onLine||e instanceof TypeError){try{let queue=pendingObservationQueue();queue.push(item);savePendingObservationQueue(queue);closeModal();await refreshObs();toast('Observation saved on this device and will sync when online')}catch{toast('Could not save the offline observation on this device')}}else toast(e.message)}};
$('#dropPin').onclick = () => {
    setPinMode(!pinMode);
    if (pinMode) toast('Tap or click the map to place an observation pin');
};
document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && pinMode) setPinMode(false);
});
$('#layerAssets').onclick=()=>{assetsVisible=!assetsVisible;$('#layerAssets').classList.toggle('active',assetsVisible);markers.forEach(m=>m.getElement().style.display=assetsVisible?'':'none')};$('#layerObs').onclick=()=>{obsVisible=!obsVisible;$('#layerObs').classList.toggle('active',obsVisible);obsMarkers.forEach(m=>m.getElement().style.display=obsVisible?'':'none')};$('#search').oninput=()=>renderSidebar();document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{clearTrip();view=b.dataset.view;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));if(view==='history')openTripHistory();else renderSidebar()});
let tripTimer = null;
let tripRequest = 0;

function stopTrip() {
    clearInterval(tripTimer);
    tripTimer = null;
    const play = $('#tripPlay');
    if (play) play.textContent = 'Play';
}

function clearTrip() {
    ++tripRequest;
    stopTrip();
    for (const [layer, source] of [['trip-cursor-dot', 'trip-cursor'], ['trip-line', 'trip']]) {
        if (map?.getLayer(layer)) map.removeLayer(layer);
        if (map?.getSource(source)) map.removeSource(source);
    }
}

function openTripHistory() {
    clearTrip();
    view = 'history';
    document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === view));
    $('#items').innerHTML = `
        <form id="tripForm">
            <label class="field">Asset <select id="tripAsset" required>
                <option value="">Choose an asset</option>
                ${assets.map(asset => `<option value="${asset.id}">${esc(asset.name)}</option>`).join('')}
            </select></label>
            <label class="field">From (local time) <input id="tripFrom" type="datetime-local" required></label>
            <label class="field">To (local time) <input id="tripTo" type="datetime-local" required></label>
            <button class="btn" type="submit">Load trip</button>
        </form>
        <p class="assetmeta">History covers the last 30 days. Narrow the range if you reach 10,000 fixes.</p>
        <p class="assetmeta" id="tripStatus" role="status"></p>
        <div id="tripControls"></div>`;
    $('#tripAsset').value = selectedAsset ?? '';
    const now = new Date();
    const localInput = date => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    $('#tripTo').value = localInput(now);
    $('#tripFrom').value = localInput(new Date(now.getTime() - 24 * 3600000));
    $('#tripForm').oninput = () => {
        clearTrip();
        $('#tripControls').replaceChildren();
        $('#tripStatus').textContent = '';
    };
    $('#tripForm').onsubmit = event => {
        event.preventDefault();
        loadTrip();
    };
}

async function loadTrip() {
    clearTrip();
    const request = tripRequest;
    const controls = $('#tripControls');
    const status = $('#tripStatus');
    controls.replaceChildren();
    const from = new Date($('#tripFrom').value);
    const to = new Date($('#tripTo').value);
    // The date controls have minute precision. Include fixes within the selected end minute.
    to.setSeconds(59, 999);
    if (from > to) {
        status.textContent = 'Choose an end time at or after the start time.';
        return;
    }
    if (!map?.isStyleLoaded()) {
        status.textContent = 'The map is still loading. Try again when it is ready.';
        return;
    }
    status.textContent = 'Loading trip…';
    try {
        const query = new URLSearchParams({from: from.toISOString(), to: to.toISOString()});
        const rows = await api(`/assets/${$('#tripAsset').value}/positions?${query}`);
        if (request !== tripRequest) return;
        status.textContent = rows.length ? `${rows.length} recorded GPS fixes` : 'No GPS fixes in this date range.';
        if (!rows.length) return;
        const coordinates = rows.map(row => [row.lng, row.lat]);
        if (coordinates.length > 1) {
            map.addSource('trip', {type:'geojson', data:{type:'LineString', coordinates}});
            map.addLayer({id:'trip-line', type:'line', source:'trip', paint:{'line-color':'#55dab9', 'line-width':4}});
        }
        const point = index => ({type:'Point', coordinates:coordinates[index]});
        map.addSource('trip-cursor', {type:'geojson', data:point(0)});
        map.addLayer({id:'trip-cursor-dot', type:'circle', source:'trip-cursor', paint:{
            'circle-radius':8, 'circle-color':'#fff', 'circle-stroke-color':'#55dab9', 'circle-stroke-width':4
        }});
        const bounds = coordinates.reduce((box, coordinate) => box.extend(coordinate),
            new maplibregl.LngLatBounds(coordinates[0], coordinates[0]));
        map.fitBounds(bounds, {padding:40, maxZoom:15, animate:false});
        controls.innerHTML = `
            <button class="btn" id="tripPlay" type="button">Play</button>
            <label class="field">Recorded position <input id="tripScrub" type="range" min="0" max="${rows.length - 1}" value="0"></label>
            <output class="assetmeta" id="tripTime" for="tripScrub"></output>`;
        const play = $('#tripPlay');
        const scrub = $('#tripScrub');
        play.disabled = scrub.disabled = rows.length === 1;
        const showFix = index => {
            scrub.value = index;
            const time = new Date(rows[index].recorded_at).toLocaleString();
            scrub.setAttribute('aria-valuetext', time);
            $('#tripTime').value = time;
            map.getSource('trip-cursor').setData(point(index));
        };
        scrub.oninput = () => {
            stopTrip();
            showFix(Number(scrub.value));
        };
        play.onclick = () => {
            if (tripTimer !== null) {
                stopTrip();
                return;
            }
            if (Number(scrub.value) === rows.length - 1) showFix(0);
            play.textContent = 'Pause';
            tripTimer = setInterval(() => {
                const next = Number(scrub.value) + 1;
                showFix(next);
                if (next === rows.length - 1) stopTrip();
            }, 500);
        };
        showFix(0);
    } catch (error) {
        if (request === tripRequest) {
            clearTrip();
            controls.replaceChildren();
            status.textContent = error.message;
        }
    }
}

$('#historyBtn').onclick = openTripHistory;
$('#geoBtn').onclick=async()=>{try{let fences=await api('/geofences');if(!fences.length){toast('No geofences yet. Add a circle from the quick form.');showGeofenceForm();return}let f=fences[0],g=f.geometry;if(f.shape==='circle')map.flyTo({center:[g.lng,g.lat],zoom:14});else map.flyTo({center:g.coordinates[0],zoom:13});toast(`${fences.length} geofence${fences.length===1?'':'s'} configured`)}catch(e){toast(e.message)}};
function showGeofenceForm(){if(user?.role!=='admin'){toast('Only admins can create geofences');return}$('#modalTitle').textContent='Create a geofence';$('#modalSub').textContent='Create a circle around the current map center.';$('#modalFields').innerHTML=`<label class="field">Name<input id="geoName" value="New geofence"></label><div class="row"><label class="field">Radius (meters)<input id="geoRadius" type="number" value="400" min="20"></label><label class="field">Center<input id="geoCenter" disabled value="Map center"></label></div>`;$('#modalback').style.display='grid';$('#modalSave').onclick=async()=>{try{let p=map.getCenter();await api('/geofences',{method:'POST',body:JSON.stringify({name:$('#geoName').value,shape:'circle',geometry:{lat:p.lat,lng:p.lng,radius_m:+$('#geoRadius').value}})});$('#modalSave').onclick=saveObservation;closeModal();toast('Geofence created')}catch(e){toast(e.message)}}}const saveObservation=$('#modalSave').onclick;
$('#addAsset').onclick=()=>{if(user?.role!=='admin'){toast('Only admins can add assets');return}$('#modalTitle').textContent='Add an asset';$('#modalSub').textContent='Create an asset record in this organization.';$('#modalFields').innerHTML=`<label class="field">Asset name<input id="assetName" placeholder="e.g. Service truck 04"></label><label class="field">Type<select id="assetKind"><option>vehicle</option><option>trailer</option><option>equipment</option></select></label><label class="field">Tracker ID (optional)<input id="deviceId" placeholder="Device serial number"></label>`;$('#modalback').style.display='grid';$('#modalSave').onclick=async()=>{try{let asset=await api('/assets',{method:'POST',body:JSON.stringify({name:$('#assetName').value,kind:$('#assetKind').value,device_id:$('#deviceId').value.trim()||null})});await refreshAssets();if(asset.device_token){$('#modalTitle').textContent='Save tracker credential';$('#modalSub').textContent='This secret is shown once. Copy it into the tracker configuration now.';$('#modalFields').innerHTML=`<label class="field">Device token<textarea id="issuedToken" readonly>${asset.device_token}</textarea></label><div style="font-size:11px;color:#ffcc7a">The token is stored as a hash. It cannot be recovered after you close this window.</div>`;$('#modalSave').textContent='Copy token and close';$('#modalSave').onclick=async()=>{try{await navigator.clipboard.writeText(asset.device_token)}catch{}closeModal();$('#modalSave').textContent='Save';$('#modalSave').onclick=saveObservation;toast('Asset added. Store its tracker token securely.')}}else{closeModal();$('#modalSave').onclick=saveObservation;toast('Asset added')}}catch(e){toast(e.message)}}};
let heartbeat = null;
let reconnectTimer = null;
let liveGeneration = 0;

function stopLive() {
    ++liveGeneration;
    clearInterval(heartbeat);
    clearTimeout(reconnectTimer);
    const socket = ws;
    ws = null;
    socket?.close();
}

function scheduleReconnect(generation) {
    if (!token || generation !== liveGeneration) return;
    $('.live span').textContent = 'Reconnecting…';
    reconnectTimer = setTimeout(connectLive, 3000);
}

async function connectLive() {
    stopLive();
    if (!token) return;
    const generation = liveGeneration;
    $('.live span').textContent = 'Connecting…';
    try {
        const {ticket} = await api('/live-ticket', {method:'POST'});
        if (generation !== liveGeneration) return;
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(`${protocol}//${location.host}/api/live`, ['dottracker', `ticket.${ticket}`]);
        ws = socket;
        socket.onopen = () => {
            if (ws !== socket) return;
            $('.live span').textContent = 'Live connection';
            heartbeat = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) socket.send('ping');
            }, 20000);
        };
        socket.onmessage = () => {
            if (ws === socket) refreshAssets();
        };
        socket.onclose = () => {
            if (ws !== socket) return;
            ws = null;
            clearInterval(heartbeat);
            scheduleReconnect(generation);
        };
    } catch {
        scheduleReconnect(generation);
    }
}

let assetPoll = null;
let observationPoll = null;
async function start(){try{user=await api('/me');$('#userEmail').textContent=user.email;setLogin(false);initMap();await Promise.all([refreshAssets(),refreshObs()]);flushPendingObservations();connectLive();clearInterval(assetPoll);clearInterval(observationPoll);assetPoll=setInterval(refreshAssets,15000);observationPoll=setInterval(refreshObs,60000)}catch(e){setLogin(true)}}if(token)start();
