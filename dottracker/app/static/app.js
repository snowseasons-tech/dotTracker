import * as maplibregl from '/static/vendor/maplibre-gl-6.16.0/maplibre-gl.mjs';
const API='/api';let token=localStorage.getItem('dt_token'),refreshToken=localStorage.getItem('dt_refresh'),refreshPromise=null,user=null,map=null,assets=[],observations=[],selectedAsset=null,view='assets',assetsVisible=true,obsVisible=true,pendingPoint=null,observationCapturedAt=null,markers=new Map(),obsMarkers=[],ws=null;
const $=s=>document.querySelector(s);const inviteToken = new URLSearchParams(location.hash.slice(1)).get('invite');function authHeaders(){return {'Authorization':'Bearer '+token,'Content-Type':'application/json'}}async function renewSession(){if(refreshPromise)return refreshPromise;refreshPromise=(async()=>{let r=await fetch(API+'/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refreshToken})});if(!r.ok)throw Error('Session expired');let d=await r.json();token=d.access_token;refreshToken=d.refresh_token;localStorage.setItem('dt_token',token);localStorage.setItem('dt_refresh',refreshToken)})().finally(()=>{refreshPromise=null});return refreshPromise}async function api(path,opts={},retried=false){let r=await fetch(API+path,{...opts,headers:{...authHeaders(),...(opts.headers||{})}});if(r.status===401){if(!retried&&refreshToken&&!path.includes('/auth/')){try{await renewSession();return api(path,opts,true)}catch{}}logout();throw Error('Session expired')}if(!r.ok){let e=await r.json().catch(()=>({detail:'Request failed'}));throw Error(e.detail||'Request failed')}return r.status===204?null:r.json()}
function toast(msg){let t=$('#toast');t.textContent=msg;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',3500)}function setLogin(on){$('#login').style.display=on?'grid':'none'}function logout(){cancelFenceDrawing();clearTrip();setPinMode(false);closeModal();let oldRefresh=refreshToken;if(oldRefresh)fetch(API+'/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:oldRefresh})}).catch(()=>{});localStorage.removeItem('dt_token');localStorage.removeItem('dt_refresh');token=null;refreshToken=null;user=null;observations=[];cdotStations=[];cdotLoaded=null;map?.getSource('cdot-stations')?.setData(cdotFeatures());obsMarkers.forEach(marker=>marker.remove());obsMarkers=[];map?.getSource('checkpoints')?.setData(checkpointFeatures());view='assets';$('#items').replaceChildren();document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.view===view));setLogin(true);stopLive();setLiveState('offline','Disconnected');clearInterval(assetPoll);clearInterval(observationPoll);clearInterval(inspectionPoll);inspectionSites=[];map?.getSource('inspection-sites')?.setData(inspectionFeatures());geofences=[];geofencePopup?.remove();map?.getSource('geofences')?.setData(geofenceFeatures());alertDots=[];alertPopup?.remove();map?.getSource('alert-dots')?.setData(alertFeatures())}
async function signIn(){try{let data=await fetch(API+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:$('#email').value,password:$('#password').value})}).then(async r=>{let d=await r.json();if(!r.ok)throw Error(d.detail||'Sign in failed');return d});token=data.access_token;refreshToken=data.refresh_token;localStorage.setItem('dt_token',token);localStorage.setItem('dt_refresh',refreshToken);start()}catch(e){$('#loginError').textContent=e.message}}
$('#loginBtn').onclick=signIn;$('#password').onkeydown=e=>{if(e.key==='Enter')signIn()};
async function configureDemoControls(){
    try {
        const response = await fetch(API+'/config', {cache:'no-store'});
        if (!response.ok) throw new Error('Configuration unavailable');
        const config = await response.json();
        const enabled = config.demo_seed_enabled === true;
        $('#seedBtn').hidden = !enabled;
        $('#demoHint').hidden = !enabled;
    } catch {
        $('#seedBtn').hidden = true;
        $('#demoHint').hidden = true;
    }
}
configureDemoControls();
$('#seedBtn').onclick=async()=>{
    try {
        const response = await fetch(API+'/dev/seed', {method:'POST'});
        const result = await response.json().catch(()=>({}));
        if (!response.ok) throw new Error('Could not initialize demo workspace');
        if (!result.seeded || !result.email || !result.password) {
            $('#loginError').textContent = result.message || 'Demo workspace could not be initialized';
            return;
        }
        $('#email').value = result.email;
        $('#password').value = result.password;
        toast('Demo workspace initialized');
        await signIn();
    } catch {
        $('#loginError').textContent = 'Could not initialize demo workspace';
    }
};
$('#signout').onclick=logout;
function configureInvitation(){
    if (!inviteToken) return;
    $('#signInPanel').hidden = true;
    $('#inviteAcceptance').hidden = false;
}
configureInvitation();
$('#inviteAcceptance').addEventListener('submit', async event => {
    event.preventDefault();
    const password = $('#invitePassword').value;
    if (password !== $('#invitePasswordConfirm').value) {
        $('#inviteError').textContent = 'Passwords do not match.';
        return;
    }
    try {
        const response = await fetch(API+'/invites/accept', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({token:inviteToken,password})
        });
        const result = await response.json().catch(()=>({}));
        if (!response.ok) throw new Error(result.detail || 'Could not accept invitation');
        token = result.access_token;
        refreshToken = result.refresh_token;
        localStorage.setItem('dt_token', token);
        localStorage.setItem('dt_refresh', refreshToken);
        history.replaceState(null, '', location.pathname + location.search);
        $('#inviteError').textContent = '';
        setLogin(false);
        start();
    } catch (error) {
        $('#inviteError').textContent = error.message;
    }
});
$('#inviteBack').onclick = () => {
    history.replaceState(null, '', location.pathname + location.search);
    $('#inviteAcceptance').hidden = true;
    $('#signInPanel').hidden = false;
    if (token) {
        setLogin(false);
        start();
    }
};
function initMap(){if(map)return;map=new maplibregl.Map({container:'map',style:'https://tiles.openfreemap.org/styles/dark',center:[-104.99,39.739],zoom:11});map.addControl(new maplibregl.NavigationControl(),'top-right');map.on('load',()=>{initGeofenceLayer();initCheckpointLayer();initAlertLayer();initCdotLayer();initInspectionLayer();refreshAssets();refreshObs()});map.on('contextmenu', e => {if (!fenceDraft) openObservation([e.lngLat.lng, e.lngLat.lat])});map.on('click', e => {
    if (fenceDraft) {
        addFenceCorner(e.lngLat.wrap());
        return;
    }
    if (pinMode) {
        openObservation([e.lngLat.lng, e.lngLat.lat]);
        return;
    }
    if (!map.getLayer('checkpoint-symbols')) return;
    const [feature] = map.queryRenderedFeatures(e.point, {layers:['checkpoint-symbols']});
    const checkpoint = feature && observations.find(item => String(item.id) === feature.properties.id);
    if (checkpoint) {
        new maplibregl.Popup({offset:24}).setLngLat([checkpoint.lng, checkpoint.lat])
            .setHTML(observationPopup(checkpoint)).addTo(map);
    }
})}
function makeMarker(asset){let el=document.createElement('div');el.style.cssText='width:17px;height:17px;border-radius:50%;border:3px solid #eef7fb;box-shadow:0 0 0 5px #59d8bc44;cursor:pointer;background:#55dab9';let m=new maplibregl.Marker({element:el}).setLngLat([asset.lng,asset.lat]).setPopup(new maplibregl.Popup({offset:16}).setHTML(`<b>${esc(asset.name)}</b><br><span>${asset.status} · ${asset.speed||0} km/h</span>`)).addTo(map);el.onclick=()=>{selectedAsset=asset.id;renderSidebar()};return m}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
let lastAssetsRefresh = null;
async function refreshAssets() {
    try {
        assets = await api('/assets');
        renderSidebar();
        for (const asset of assets) {
            if (asset.lat == null) continue;
            let marker = markers.get(asset.id);
            if (marker) marker.setLngLat([asset.lng, asset.lat]);
            else markers.set(asset.id, makeMarker(asset));
            const el = markers.get(asset.id).getElement();
            const color = asset.status === 'moving' ? '#55dab9' : asset.status === 'stopped' ? '#f2b95d' : '#718394';
            el.style.background = color;
            el.style.boxShadow = `0 0 0 5px ${color}44`;
            if (!assetsVisible) el.style.display = 'none';
        }
        lastAssetsRefresh = new Date();
        const status = $('#mapStatus');
        status.dataset.state = 'ok';
        status.textContent = `${assets.length} assets · updated ${lastAssetsRefresh.toLocaleTimeString()}`;
    } catch {
        const status = $('#mapStatus');
        status.dataset.state = 'error';
        status.textContent = lastAssetsRefresh
            ? `Asset refresh failed · showing data from ${lastAssetsRefresh.toLocaleTimeString()}`
            : 'Asset data unavailable · retrying';
    }
}
function renderTeam(root) {
    if (user?.role !== 'admin') {
        root.innerHTML = '<div class="empty" role="alert">Team invitations are available to workspace admins.</div>';
        return;
    }
    root.innerHTML = `<section id="teamPanel">
        <h3>Invite a teammate</h3>
        <p class="assetmeta">Create a single-use invitation that expires in 48 hours. Copy the link and deliver it through a trusted channel.</p>
        <form id="teamInviteForm">
            <label class="field">Email address<input id="teamInviteEmail" type="email" maxlength="320" autocomplete="email" required></label>
            <label class="field">Workspace role<select id="teamInviteRole"><option value="viewer">Viewer</option><option value="admin">Administrator</option></select></label>
            <button class="btn primary" id="createTeamInvite" type="submit">Create invite link</button>
        </form>
        <div class="error" id="teamInviteError" role="status" aria-live="polite"></div>
        <section id="teamInviteResult" hidden>
            <p id="teamInviteMessage" role="status"></p>
            <label class="field">Invite link<input id="teamInviteLink" readonly></label>
            <button class="btn" id="copyTeamInvite" type="button">Copy invite link</button>
        </section>
    </section>`;
    $('#teamInviteForm').onsubmit = async event => {
        event.preventDefault();
        const button = $('#createTeamInvite');
        button.disabled = true;
        $('#teamInviteError').textContent = '';
        try {
            const result = await api('/invites', {
                method:'POST',
                body:JSON.stringify({email:$('#teamInviteEmail').value,role:$('#teamInviteRole').value})
            });
            const inviteUrl = new URL(location.href);
            inviteUrl.hash = new URLSearchParams({invite:result.invite_token}).toString();
            $('#teamInviteLink').value = inviteUrl.toString();
            $('#teamInviteMessage').textContent = `Invite for ${result.email} (${result.role}) expires ${new Date(result.expires_at).toLocaleString()}.`;
            $('#teamInviteResult').hidden = false;
            $('#teamInviteEmail').value = '';
        } catch (error) {
            $('#teamInviteError').textContent = error.message;
        } finally {
            button.disabled = false;
        }
    };
    $('#copyTeamInvite').onclick = async () => {
        const input = $('#teamInviteLink');
        try {
            await navigator.clipboard.writeText(input.value);
            toast('Invite link copied');
        } catch {
            input.focus();
            input.select();
            input.setSelectionRange(0, input.value.length);
            toast('Select the invite link and copy it');
        }
    };
}

function renderSidebar(){let counts={moving:0,stopped:0,offline:0};assets.forEach(a=>counts[a.status]++);$('#movingCount').textContent=counts.moving;$('#stoppedCount').textContent=counts.stopped;$('#offlineCount').textContent=counts.offline;let root=$('#items');if(view==='team'){if(!root.querySelector('#teamPanel'))renderTeam(root);return}if(view==='safety'){if(!root.querySelector('#safetyPanel'))renderSafety(root);return}if(view==='dispatch'){renderDispatch(root);return}if(view==='stations'){renderStations(root);return}if(view==='history'&&root.querySelector('#tripForm'))return;if(view==='assets'){let q=$('#search').value.toLowerCase();let list=assets.filter(a=>a.name.toLowerCase().includes(q));root.innerHTML=list.map(a=>`<div class="asset ${a.id===selectedAsset?'sel':''}" data-id="${a.id}"><i class="assetdot ${a.status}"></i><div class="assetinfo"><div class="assetname">${esc(a.name)}</div><div class="assetmeta">${esc(a.kind)} · ${a.recorded_at?new Date(a.recorded_at).toLocaleTimeString():'No GPS fix'}</div></div><span class="speed">${a.speed?Math.round(a.speed)+' km/h':''}</span></div>`).join('')||'<div class="empty">No assets match your search.</div>';root.querySelectorAll('.asset').forEach(el=>el.onclick=()=>{selectedAsset=+el.dataset.id;let a=assets.find(x=>x.id===selectedAsset);if(a?.lat)map.flyTo({center:[a.lng,a.lat],zoom:14});renderSidebar()});return}if(view==='observations'){root.innerHTML=observations.map(o=>`<div class="asset" data-id="${o.id}"><i class="assetdot" style="background:#70a7ff"></i><div class="assetinfo"><div class="assetname">${esc(o.note||(o.kind==='mobile_dot_checkpoint'?'Temporary inspection checkpoint':'Observation pin'))}</div><div class="assetmeta">${esc(o.author)} · ${new Date(o.observed_at).toLocaleString()}</div></div></div>`).join('')||'<div class="empty">No observations yet. Right-click the map to add one.</div>';root.querySelectorAll('.asset').forEach(el=>el.onclick=()=>{let o=observations.find(x=>x.id==el.dataset.id);map.flyTo({center:[o.lng,o.lat],zoom:15})});return}if(view==='alerts'){api('/alerts').then(rows=>{if(view!=='alerts')return;root.innerHTML=rows.map(a=>`<div class="asset"><i class="assetdot" style="background:#f2b95d"></i><div class="assetinfo"><div class="assetname">${esc(a.message)}</div><div class="assetmeta">${new Date(a.created_at).toLocaleString()}</div></div></div>`).join('')||'<div class="empty">No alerts. Geofence events will appear here.</div>'});return}root.innerHTML='<div class="empty">Select an asset to browse up to 30 days of trip history.</div>'}
function pendingObservationQueue(){try{let value=JSON.parse(localStorage.getItem('dt_pending_observations')||'[]');if(!Array.isArray(value))return [];return value.filter(o=>o&&typeof o.client_id==='string'&&typeof o.note==='string'&&Number.isFinite(o.lat)&&o.lat>=-90&&o.lat<=90&&Number.isFinite(o.lng)&&o.lng>=-180&&o.lng<=180&&Number.isInteger(o.user_id)&&Number.isInteger(o.org_id)).slice(0,1000)}catch{return []}}
function savePendingObservationQueue(queue){localStorage.setItem('dt_pending_observations',JSON.stringify(queue))}
function initCheckpointLayer() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 48;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.beginPath();
    ctx.moveTo(24, 4);
    ctx.lineTo(45, 42);
    ctx.lineTo(3, 42);
    ctx.closePath();
    ctx.fillStyle = '#ffcb63';
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#191004';
    ctx.stroke();
    ctx.fillStyle = '#191004';
    ctx.fillRect(22, 17, 4, 12);
    ctx.fillRect(22, 32, 4, 4);
    map.addImage('checkpoint-warning', ctx.getImageData(0, 0, 48, 48), {pixelRatio:2});
    map.addSource('checkpoints', {type:'geojson', data:checkpointFeatures()});
    map.addLayer({
        id:'checkpoint-symbols', type:'symbol', source:'checkpoints',
        layout:{
            'icon-image':'checkpoint-warning', 'icon-anchor':'bottom',
            'icon-allow-overlap':true, 'visibility':obsVisible?'visible':'none'
        },
        paint:{'icon-opacity':['case', ['get', 'pending'], 0.65, 1]}
    });
}

function checkpointFeatures() {
    return {
        type:'FeatureCollection',
        features:observations.filter(item => item.kind === 'mobile_dot_checkpoint').map(item => ({
            type:'Feature',
            id:String(item.id),
            geometry:{type:'Point', coordinates:[item.lng, item.lat]},
            properties:{id:String(item.id), pending:Boolean(item.pending)}
        }))
    };
}

function observationPopup(item) {
    const label = item.kind === 'mobile_dot_checkpoint' ? 'Temporary inspection checkpoint' : 'Observation';
    return `<div class="obs-popup"><b>${esc(label)}</b>${item.note?`<br>${esc(item.note)}`:''}<br>${esc(item.author||'You')}${item.pending?'<br>Waiting for connection':''}<br>${new Date(item.observed_at).toLocaleString()}</div>`;
}

async function refreshObs({localOnly = false} = {}) {
    const scope = user;
    if (!scope) return;
    let confirmed = observations.filter(item => !item.pending);
    if (!localOnly && navigator.onLine) {
        try {
            confirmed = await api('/observations');
        } catch {}
    }
    if (user !== scope) return;
    const now = Date.now();
    confirmed = confirmed.filter(item => !item.expires_at || Date.parse(item.expires_at) > now);
    const uploaded = new Set(confirmed.map(item => item.client_id));
    const queued = pendingObservationQueue()
        .filter(item => item.user_id === scope.id && item.org_id === scope.org_id && !uploaded.has(item.client_id))
        .filter(item => item.kind !== 'mobile_dot_checkpoint' || Date.parse(item.observed_at) + 7200000 > now)
        .map(item => ({...item, id:`pending-${item.client_id}`, author:'You · waiting to sync', pending:true}));
    observations = [...confirmed, ...queued];
    obsMarkers.forEach(marker => marker.remove());
    obsMarkers = [];
    map.getSource('checkpoints')?.setData(checkpointFeatures());
    for (const item of observations) {
        if (item.kind === 'mobile_dot_checkpoint') continue;
        const el = document.createElement('div');
        el.style.cssText = `width:14px;height:14px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${item.pending?'#e8bf68':'#70a7ff'};border:2px solid white;box-shadow:0 2px 8px #0008;cursor:pointer`;
        const marker = new maplibregl.Marker({element:el}).setLngLat([item.lng, item.lat])
            .setPopup(new maplibregl.Popup({offset:12}).setHTML(observationPopup(item))).addTo(map);
        if (!obsVisible) el.style.display = 'none';
        obsMarkers.push(marker);
    }
    if (view === 'observations') renderSidebar();
}
async function flushPendingObservations(){if(!navigator.onLine||!token||!user)return;let queue=pendingObservationQueue(),synced=0;for(const item of queue){if(item.user_id!==user.id||item.org_id!==user.org_id)continue;try{const {user_id,org_id,...payload}=item;await api('/observations',{method:'POST',body:JSON.stringify(payload)});queue=queue.filter(x=>x.client_id!==item.client_id);savePendingObservationQueue(pendingObservationQueue().filter(x=>x.client_id!==item.client_id));synced++}catch{return}}if(synced){await refreshObs();if(view==='observations')toast(`${synced} queued observation${synced===1?'':'s'} synced`)}}
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
    $('#modalTitle').textContent='Drop an observation';$('#modalSub').textContent=`${point[1].toFixed(5)}, ${point[0].toFixed(5)} · ${new Date(observationCapturedAt).toLocaleString()}`;$('#modalFields').innerHTML=`<label class="field">Pin type<select id="obsKind"><option value="observation">Observation</option><option value="mobile_dot_checkpoint">Temporary inspection checkpoint (2 hours)</option>${user.role==='admin'?'<option value="inspection_site">Non-permanent safety inspection station</option>':''}</select></label><label class="field">Note<textarea id="obsNote" maxlength="2000" placeholder="What did you observe?"></textarea></label><label class="field">Related asset (optional)<select id="obsAsset"><option value="">No asset linked</option>${assets.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>`;$('#modalback').style.display='grid';$('#obsKind').onchange=e=>{if(e.target.value==='inspection_site')openInspectionSite(point)};setTimeout(()=>$('#obsNote')?.focus(),50)}function closeModal(){$('#modalback').style.display='none'}$('#modalClose').onclick=$('#modalCancel').onclick=closeModal;$('#modalSave').onclick=async()=>{let item={client_id:crypto.randomUUID(),lat:pendingPoint[1],lng:pendingPoint[0],note:$('#obsNote').value,kind:$('#obsKind').value,asset_id:$('#obsAsset').value?+$('#obsAsset').value:null,observed_at:observationCapturedAt,user_id:user.id,org_id:user.org_id};
if(item.kind==='mobile_dot_checkpoint'){
    try{
        const queue=pendingObservationQueue();
        if(queue.length>=1000)throw Error('Observation outbox is full');
        savePendingObservationQueue([...queue,item]);
        closeModal();
        await refreshObs({localOnly:true});
        await flushPendingObservations();
    }catch(error){toast(error.message)}
    return;
}
try{const {user_id,org_id,...payload}=item;await api('/observations',{method:'POST',body:JSON.stringify(payload)});closeModal();await refreshObs();toast('Observation saved to your organization')}catch(e){if(!navigator.onLine||e instanceof TypeError){try{let queue=pendingObservationQueue();queue.push(item);savePendingObservationQueue(queue);closeModal();await refreshObs();toast('Observation saved on this device and will sync when online')}catch{toast('Could not save the offline observation on this device')}}else toast(e.message)}};
const saveObservation = $('#modalSave').onclick;
$('#dropPin').onclick = () => {
    if (fenceDraft) cancelFenceDrawing();
    setPinMode(!pinMode);
    if (pinMode) toast('Tap or click the map to place an observation pin');
};
document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (fenceDraft) cancelFenceDrawing();
    else if (pinMode) setPinMode(false);
});
$('#layerAssets').onclick=()=>{assetsVisible=!assetsVisible;$('#layerAssets').classList.toggle('active',assetsVisible);markers.forEach(m=>m.getElement().style.display=assetsVisible?'':'none')};$('#layerObs').onclick=()=>{obsVisible=!obsVisible;$('#layerObs').classList.toggle('active',obsVisible);obsMarkers.forEach(m=>m.getElement().style.display=obsVisible?'':'none');if(map?.getLayer('checkpoint-symbols'))map.setLayoutProperty('checkpoint-symbols','visibility',obsVisible?'visible':'none')};$('#search').oninput=()=>renderSidebar();document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{clearTrip();view=b.dataset.view;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));if(view==='history')openTripHistory();else renderSidebar()});
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
let fenceDraft = null;
let fenceClosed = false;

function fenceDraftFeatures(points) {
    const features = points.map(coordinates => ({
        type:'Feature', properties:{}, geometry:{type:'Point', coordinates}
    }));
    if (points.length >= 2) {
        features.push({type:'Feature', properties:{}, geometry:{
            type:'LineString', coordinates:fenceClosed ? [...points, points[0]] : points
        }});
    }
    if (fenceClosed) features.push({type:'Feature', properties:{}, geometry:{
        type:'Polygon', coordinates:[[...points, points[0]]]
    }});
    return {type:'FeatureCollection', features};
}

function refreshFenceDraft() {
    map.getSource('fence-draft').setData(fenceDraftFeatures(fenceDraft));
    const count = fenceDraft.length;
    $('#fenceInstructions').textContent = `Tap or click the map to add corners. ${count} of at least 3 placed.`;
    $('#fenceUndo').disabled = count === 0;
    $('#fenceInstructions').textContent = fenceClosed ? `Closed boundary with ${count} corners.` : $('#fenceInstructions').textContent;
    $('#fenceClose').hidden = fenceClosed;
    $('#fenceClose').disabled = count < 3;
    $('#fenceFinish').hidden = !fenceClosed;
}

function addFenceCorner(point) {
    if (fenceClosed) return;
    if (fenceDraft.length >= 500) {
        toast('A polygon can have up to 500 corners.');
        return;
    }
    if (fenceDraft.some(([lng, lat]) => lng === point.lng && lat === point.lat)) return;
    fenceDraft.push([point.lng, point.lat]);
    refreshFenceDraft();
}

function cancelFenceDrawing() {
    if (!fenceDraft) return;
    fenceDraft = null;
    fenceClosed = false;
    $('#fenceDrawing').hidden = true;
    for (const layer of ['fence-draft-corners', 'fence-draft-outline', 'fence-draft-fill']) map.removeLayer(layer);
    map.removeSource('fence-draft');
    if ($('#geoName')) closeModal();
}

function startFenceDrawing() {
    if (!map?.getStyle()?.layers?.length) {
        toast('Wait for the map to finish loading.');
        return;
    }
    cancelFenceDrawing();
    setPinMode(false);
    fenceDraft = [];
    map.addSource('fence-draft', {type:'geojson', data:fenceDraftFeatures(fenceDraft)});
    map.addLayer({id:'fence-draft-fill', type:'fill', source:'fence-draft',
        filter:['==', ['geometry-type'], 'Polygon'], paint:{'fill-color':'#57d3c1', 'fill-opacity':0.25}});
    map.addLayer({id:'fence-draft-outline', type:'line', source:'fence-draft',
        filter:['==', ['geometry-type'], 'LineString'], paint:{'line-color':'#57d3c1', 'line-width':3}});
    map.addLayer({id:'fence-draft-corners', type:'circle', source:'fence-draft',
        filter:['==', ['geometry-type'], 'Point'], paint:{'circle-color':'#57d3c1', 'circle-radius':6,
            'circle-stroke-color':'#111a24', 'circle-stroke-width':2}});
    $('#fenceDrawing').hidden = false;
    refreshFenceDraft();
}

$('#fenceUndo').onclick = () => {
    if (fenceClosed) fenceClosed = false;
    else fenceDraft.pop();
    refreshFenceDraft();
};
$('#fenceReset').onclick = () => {
    fenceClosed = false;
    fenceDraft = [];
    refreshFenceDraft();
};
$('#fenceClose').onclick = () => {
    if (fenceDraft.length < 3) return;
    fenceClosed = true;
    refreshFenceDraft();
};
$('#fenceCancel').onclick = cancelFenceDrawing;
$('#fenceFinish').onclick = () => showGeofenceForm('polygon');

function showGeofenceForm(shape) {
    const polygon = shape === 'polygon';
    $('#modalTitle').textContent = polygon ? 'Save polygon geofence' : 'Create circle geofence';
    $('#modalSub').textContent = polygon ? 'Give the drawn boundary a name.' : 'Use the current map center.';
    $('#modalFields').innerHTML = `
        <label class="field">Name <input id="geoName" maxlength="120" value="New geofence"></label>
        ${polygon ? '' : '<label class="field">Radius (meters) <input id="geoRadius" type="number" value="400" min="20" max="100000"></label>'}<p id="fenceSaveError" class="error" role="alert"></p>`;
    $('#modalback').style.display = 'grid';
    $('#geoName').focus();
    $('#modalSave').textContent = 'Save';
    $('#modalSave').onclick = async () => {
        const name = $('#geoName').value.trim();
        const feedback = $('#fenceSaveError');
        feedback.textContent = '';
        if (!name) { feedback.textContent = 'Enter a geofence name.'; return; }
        const center = map.getCenter().wrap();
        const geometry = polygon ? {coordinates:fenceDraft} :
            {lat:center.lat, lng:center.lng, radius_m:$('#geoRadius').valueAsNumber};
        const save = $('#modalSave');
        save.disabled = true;
        try {
            await api('/geofences', {method:'POST', body:JSON.stringify({name, shape, geometry})});
            cancelFenceDrawing();
            closeModal();
            toast('Geofence created');
            if (!geofencesVisible) setGeofencesVisible(true);
            refreshGeofences();
        } catch (error) {
            feedback.textContent = error.message;
        } finally {
            save.disabled = false;
        }
    };
}

async function locateGeofence() {
    await refreshGeofences();
    if (!geofences.length) { toast('No geofences configured'); return; }
    if (!geofencesVisible) setGeofencesVisible(true);
    const bounds = new maplibregl.LngLatBounds();
    for (const fence of geofences) geofenceRing(fence).forEach(point => bounds.extend(point));
    map.fitBounds(bounds, {padding:60, maxZoom:15});
    toast(`${geofences.length} configured geofence${geofences.length === 1 ? '' : 's'}`);
}

$('#geoBtn').onclick = () => {
    cancelFenceDrawing();
    if (user?.role !== 'admin') { locateGeofence(); return; }
    $('#modalTitle').textContent = 'Create a geofence';
    $('#modalSub').textContent = 'Choose a shape for the property boundary.';
    $('#modalFields').innerHTML = '<button class="btn" id="chooseCircle">Circle</button> <button class="btn" id="choosePolygon">Draw polygon</button> <button class="btn" id="locateFence">Find existing</button>';
    $('#modalback').style.display = 'grid';
    $('#modalSave').textContent = 'Close';
    $('#modalSave').onclick = closeModal;
    $('#locateFence').onclick = () => { closeModal(); locateGeofence(); };
    $('#chooseCircle').onclick = () => showGeofenceForm('circle');
    $('#choosePolygon').onclick = () => { closeModal(); startFenceDrawing(); };
};

$('#addAsset').onclick=()=>{if(user?.role!=='admin'){toast('Only admins can add assets');return}cancelFenceDrawing();$('#modalTitle').textContent='Add an asset';$('#modalSub').textContent='Create an asset record in this organization.';$('#modalFields').innerHTML=`<label class="field">Asset name<input id="assetName" placeholder="e.g. Service truck 04"></label><label class="field">Type<select id="assetKind"><option>vehicle</option><option>trailer</option><option>equipment</option></select></label><label class="field">Tracker ID (optional)<input id="deviceId" placeholder="Device serial number"></label>`;$('#modalback').style.display='grid';$('#modalSave').onclick=async()=>{try{let asset=await api('/assets',{method:'POST',body:JSON.stringify({name:$('#assetName').value,kind:$('#assetKind').value,device_id:$('#deviceId').value.trim()||null})});await refreshAssets();if(asset.device_token){$('#modalTitle').textContent='Save tracker credential';$('#modalSub').textContent='This secret is shown once. Copy it into the tracker configuration now.';$('#modalFields').innerHTML=`<label class="field">Device token<textarea id="issuedToken" readonly>${asset.device_token}</textarea></label><div style="font-size:11px;color:#ffcc7a">The token is stored as a hash. It cannot be recovered after you close this window.</div>`;$('#modalSave').textContent='Copy token and close';$('#modalSave').onclick=async()=>{try{await navigator.clipboard.writeText(asset.device_token)}catch{}closeModal();$('#modalSave').textContent='Save';$('#modalSave').onclick=saveObservation;toast('Asset added. Store its tracker token securely.')}}else{closeModal();$('#modalSave').onclick=saveObservation;toast('Asset added')}}catch(e){toast(e.message)}}};
let dispatchDatabase;
let dispatchSync;

function openDispatchDatabase() {
    dispatchDatabase ??= new Promise((resolve, reject) => {
        const request = indexedDB.open('dottracker-dispatch', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('routes', {keyPath: 'key'});
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
    return dispatchDatabase;
}

async function changeCachedRoute(key, change) {
    const db = await openDispatchDatabase();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction('routes', change ? 'readwrite' : 'readonly');
        const store = transaction.objectStore('routes');
        const read = store.get(key);
        let entry;
        read.onsuccess = () => {
            entry = read.result ?? {key, route: null, outbox: []};
            if (change) {
                change(entry);
                store.put(entry);
            }
        };
        transaction.oncomplete = () => resolve(entry);
        transaction.onabort = () => reject(transaction.error);
    });
}

function syncDispatch(asset) {
    if (!navigator.onLine || !asset?.isConnected || !asset.value || !user) return;
    if (dispatchSync) {
        return dispatchSync.then(() => syncDispatch(asset));
    }
    const scope = user;
    const assetId = asset.value;
    const key = `${scope.org_id}:${scope.id}:${assetId}`;
    const path = `/assets/${assetId}/dispatch-route`;
    dispatchSync = (async () => {
        const entry = await changeCachedRoute(key);
        const mutations = [...entry.outbox].sort((a, b) => a.changed_at.localeCompare(b.changed_at));
        for (const mutation of mutations) {
            if (user !== scope || !navigator.onLine) return;
            const receipt = await api(path + '/status', {
                method: 'POST',
                headers: {'Idempotency-Key': mutation.mutation_id},
                body: JSON.stringify(mutation)
            });
            if (receipt?.synced !== true || !receipt.route) throw Error('Status update was not acknowledged');
            await changeCachedRoute(key, cached => {
                if (!cached.route || receipt.route.revision >= cached.route.revision) cached.route = receipt.route;
                cached.outbox = cached.outbox.filter(item => item.mutation_id !== mutation.mutation_id);
            });
        }
    })().finally(() => { dispatchSync = null; });
    return dispatchSync;
}

let safetySync = null;

function syncSafety() {
    if (safetySync || !user || !navigator.onLine) return safetySync;
    const scope = user;
    const key = `safety:${scope.org_id}:${scope.id}`;
    safetySync = (async () => {
        const entry = await changeCachedRoute(key);
        for (const submission of entry.outbox) {
            if (user !== scope || !navigator.onLine) break;
            const receipt = await api('/safety/submissions', {method:'POST', body:JSON.stringify(submission)});
            if (!receipt?.synced) throw Error('Submission was not acknowledged');
            await changeCachedRoute(key, cached => {
                cached.outbox = cached.outbox.filter(item => item.submission_id !== submission.submission_id);
            });
        }
    })().finally(() => { safetySync = null; });
    return safetySync;
}

async function renderSafety(root) {
    const scope = user;
    if (!scope) return;
    const key = `safety:${scope.org_id}:${scope.id}`;
    root.innerHTML = '<div id="safetyPanel"><p role="status">Loading checklist…</p></div>';
    const panel = $('#safetyPanel');
    try {
        let entry = await changeCachedRoute(key);
        if (!entry.template && navigator.onLine) {
            const response = await fetch('/static/safety-v1.json');
            if (!response.ok) throw Error('Could not load checklist');
            const template = await response.json();
            entry = await changeCachedRoute(key, cached => { cached.template = template; });
        }
        if (!panel.isConnected || user !== scope) return;
        if (!entry.template) throw Error('Open Safety while online to cache the checklist');
        const template = entry.template;
        panel.innerHTML = `<form id="safetyForm">
            <h3>${esc(template.title)}</h3>
            ${Object.entries(template.items).map(([key,label]) => `<label class="field">${esc(label)}
                <select required name="${esc(key)}"><option value="">Choose a result</option>
                <option value="checked">Checked</option><option value="concern">Concern</option>
                <option value="not_applicable">Not applicable</option></select></label>`).join('')}
            <label class="field">Notes <textarea name="notes" maxlength="2000"></textarea></label>
            <button class="btn primary" type="submit">Submit checklist</button></form>
            <p id="safetyStatus" role="status"></p>
            ${scope.role==='admin'?'<button class="btn" id="safetyReview">Review submissions</button><ol id="safetyHistory"></ol>':''}`;
        const status = panel.querySelector('#safetyStatus');
        const showPending = async () => {
            const cached = await changeCachedRoute(key);
            status.textContent = `${cached.outbox.length} checklists pending sync`;
        };
        const form = panel.querySelector('form');
        form.onsubmit = async event => {
            event.preventDefault();
            const button = form.querySelector('button');
            const {notes, ...answers} = Object.fromEntries(new FormData(form));
            if (Object.values(answers).includes('concern') && !notes.trim()) {
                status.textContent = 'Add a note for any concern.';
                return;
            }
            button.disabled = true;
            try {
                const submission = {submission_id:crypto.randomUUID(), template_version:template.version,
                    completed_at:new Date().toISOString(), answers, notes};
                await changeCachedRoute(key, cached => cached.outbox.push(submission));
                form.reset();
                await showPending();
                await syncSafety();
                await showPending();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        };
        const review = panel.querySelector('#safetyReview');
        if (review) review.onclick = async () => {
            try {
                const rows = await api('/safety/submissions');
                const history = panel.querySelector('#safetyHistory');
                history.replaceChildren();
                for (const row of rows) {
                    const item = document.createElement('li');
                    const answers = Object.entries(row.answers).map(([key,value]) =>
                        `${template.items[key] ?? key}: ${value.replaceAll('_',' ')}`).join(' · ');
                    item.textContent = `${row.author} · v${row.template_version} · ${new Date(row.completed_at).toLocaleString()} · ${answers} ${row.notes}`;
                    history.append(item);
                }
                if (!rows.length) status.textContent = 'No submitted checklists yet.';
            } catch (error) { status.textContent = error.message; }
        };
        await showPending();
        await syncSafety();
        await showPending();
    } catch (error) {
        if (panel.isConnected) panel.querySelector('[role=status]').textContent = error.message;
    }
}

window.addEventListener('online', async () => {
    const scope = user;
    if (!scope) return;
    const status = $('#safetyStatus');
    try {
        await syncSafety();
        if (status?.isConnected && user === scope) {
            const cached = await changeCachedRoute(`safety:${scope.org_id}:${scope.id}`);
            status.textContent = `${cached.outbox.length} checklists pending sync`;
        }
    } catch (error) {
        if (status?.isConnected && user === scope) status.textContent = error.message;
    }
});

let dispatchRequest = 0;

function renderDispatch(root) {
    if (root.querySelector('#dispatchAsset') || !assets.length) {
        if (!assets.length) root.innerHTML = '<div class="empty">No assets available for dispatch.</div>';
        return;
    }
    root.innerHTML = `
        <label class="field">Crew asset <select id="dispatchAsset">
            <option value="">Choose an asset</option>
            ${assets.map(asset => `<option value="${asset.id}">${esc(asset.name)}</option>`).join('')}
        </select></label>
        <p class="assetmeta" id="dispatchStatus" role="status"></p>
        <ol id="dispatchStops"></ol>`;
    const asset = $('#dispatchAsset');
    asset.value = selectedAsset ?? '';
    asset.onchange = () => refreshDispatch(asset, {upload:true});
    refreshDispatch(asset, {upload:true});
}

async function refreshDispatch(asset, {upload = false} = {}) {
    const request = ++dispatchRequest;
    const status = $('#dispatchStatus');
    const stops = $('#dispatchStops');
    const scope = user;
    const assetId = asset.value;
    if (!assetId || !scope) {
        stops.replaceChildren();
        status.textContent = 'Choose an asset to see its assigned stops.';
        return;
    }
    const key = `${scope.org_id}:${scope.id}:${assetId}`;
    const isCurrent = () => request === dispatchRequest && asset.isConnected && user === scope;
    const render = entry => {
        if (!isCurrent()) return;
        stops.replaceChildren();
        if (!entry.route) {
            status.textContent = 'No saved route for this asset.';
            return;
        }
        status.textContent = `Route revision ${entry.route.revision} · ${entry.outbox.length} changes pending sync`;
        const progress = new Map(Object.entries(entry.route.progress ?? {}));
        for (const mutation of entry.outbox) progress.set(mutation.job_id, mutation.status);
        for (const stop of entry.route.plan.stops) {
            const item = document.createElement('li');
            const label = document.createElement('label');
            const select = document.createElement('select');
            label.textContent = stop.job_id + ' ';
            for (const value of ['pending', 'completed', 'skipped']) select.add(new Option(value, value));
            select.value = progress.get(stop.job_id) ?? 'pending';
            select.onchange = async () => {
                select.disabled = true;
                try {
                    const mutation = {
                        mutation_id: crypto.randomUUID(), job_id: stop.job_id,
                        status: select.value, changed_at: new Date().toISOString()
                    };
                    const saved = await changeCachedRoute(key, cached => cached.outbox.push(mutation));
                    render(saved);
                    if (isCurrent()) refreshDispatch(asset, {upload:true});
                } catch (error) {
                    if (isCurrent()) {
                        select.value = progress.get(stop.job_id) ?? 'pending';
                        select.disabled = false;
                        status.textContent = `Could not save change · ${error.message}`;
                    }
                }
            };
            label.append(select);
            item.append(label);
            stops.append(item);
        }
    };
    try {
        render(await changeCachedRoute(key));
        if (!isCurrent() || !navigator.onLine) return;
        if (upload) {
            try {
                await syncDispatch(asset);
            } finally {
                render(await changeCachedRoute(key));
            }
        }
        if (!isCurrent()) return;
        const route = await api(`/assets/${assetId}/dispatch-route`);
        if (!isCurrent()) return;
        const saved = await changeCachedRoute(key, entry => {
            if (!entry.route || route.revision >= entry.route.revision) entry.route = route;
        });
        render(saved);
    } catch (error) {
        if (isCurrent()) status.textContent = error.message;
    }
}

function resumeDispatch() {
    const asset = $('#dispatchAsset');
    if (!document.hidden && view === 'dispatch' && asset?.value) refreshDispatch(asset, {upload:true});
}

document.addEventListener('visibilitychange', resumeDispatch);
window.addEventListener('online', resumeDispatch);

let heartbeat = null;
let reconnectTimer = null;
let liveGeneration = 0;

function setLiveState(state, label) {
    const indicator = $('#liveIndicator');
    indicator.dataset.state = state;
    indicator.querySelector('span').textContent = label;
}

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
    setLiveState('retrying', 'Reconnecting…');
    reconnectTimer = setTimeout(connectLive, 3000);
}

async function connectLive() {
    stopLive();
    if (!token) return;
    const generation = liveGeneration;
    setLiveState('connecting', 'Connecting…');
    try {
        const {ticket} = await api('/live-ticket', {method:'POST'});
        if (generation !== liveGeneration) return;
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(`${protocol}//${location.host}/api/live`, ['dottracker', `ticket.${ticket}`]);
        ws = socket;
        socket.onopen = () => {
            if (ws !== socket) return;
            setLiveState('connected', 'Live connection');
            socket.send('sync');
            heartbeat = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) socket.send('ping');
            }, 20000);
        };
        socket.onmessage = event => {
            if (ws !== socket) return;
            const message = JSON.parse(event.data);
            if (message.type === 'position') { refreshAssets(); scheduleAlertRefresh(); }
            if (message.type === 'checkpoint_created' || message.type === 'sync_ready') refreshObs();
            if (message.type === 'inspection_site_changed' || message.type === 'sync_ready') refreshInspectionSites();
            if (message.type === 'sync_ready' || message.type === 'route_updated') {
                const asset = $('#dispatchAsset');
                if (!dispatchSync && view === 'dispatch' && asset?.value &&
                    (message.type === 'sync_ready' || Number(asset.value) === message.asset_id)) {
                    refreshDispatch(asset, {upload: message.type === 'sync_ready'});
                }
            }
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
// --- v3: CDOT stations (ports of entry) and highway milepoint lookup ---
let cdotStations = [], cdotLoaded = null, cdotVisible = false, cdotLookup = null;

function cdotFeatures() {
    return {
        type:'FeatureCollection',
        features:cdotStations.map(station => ({
            type:'Feature',
            geometry:{type:'Point', coordinates:[station.lng, station.lat]},
            properties:{id:String(station.id), status:station.status}
        }))
    };
}

function initCdotLayer() {
    map.addSource('cdot-stations', {type:'geojson', data:cdotFeatures()});
    map.addLayer({
        id:'cdot-station-dots', type:'circle', source:'cdot-stations',
        layout:{visibility:cdotVisible ? 'visible' : 'none'},
        paint:{
            'circle-radius':['interpolate', ['linear'], ['zoom'], 5, 5, 12, 9],
            'circle-color':['match', ['get', 'status'], 'planned', '#9aa9b8', '#ff9f43'],
            'circle-stroke-width':2, 'circle-stroke-color':'#0b1118'
        }
    });
    map.on('click', 'cdot-station-dots', e => {
        const station = cdotStations.find(item => String(item.id) === e.features?.[0]?.properties.id);
        if (station) new maplibregl.Popup({offset:12}).setLngLat([station.lng, station.lat]).setHTML(cdotPopup(station)).addTo(map);
    });
    map.on('mouseenter', 'cdot-station-dots', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'cdot-station-dots', () => { map.getCanvas().style.cursor = ''; });
}

function loadCdotStations() {
    cdotLoaded ??= api('/v1/cdot/stations').then(rows => {
        cdotStations = rows;
        map?.getSource('cdot-stations')?.setData(cdotFeatures());
        return rows;
    }).catch(error => { cdotLoaded = null; throw error; });
    return cdotLoaded;
}

function cdotStationLabel(station) {
    return `${station.name} ${station.direction}`;
}

function cdotPopup(station) {
    const status = station.status === 'planned' ? 'Planned · not open yet' : esc(station.phone || 'Open');
    return `<b>${esc(cdotStationLabel(station))}</b><br><span>Port of entry · ${esc(station.highway)} MP ${esc(station.milepost)}</span><br><span>${status}</span>`;
}

async function setCdotVisible(visible) {
    cdotVisible = visible;
    $('#layerCdot').classList.toggle('active', visible);
    $('#layerCdot').setAttribute('aria-pressed', String(visible));
    if (map?.getLayer('cdot-station-dots')) map.setLayoutProperty('cdot-station-dots', 'visibility', visible ? 'visible' : 'none');
    if (!visible) return;
    try {
        const rows = await loadCdotStations();
        if (!rows.length) { toast('No CDOT stations loaded yet. Run the cdot-stations loader.'); return; }
        const bounds = new maplibregl.LngLatBounds();
        rows.forEach(station => bounds.extend([station.lng, station.lat]));
        map?.fitBounds(bounds, {padding:60, maxZoom:9});
    } catch (error) {
        toast(`CDOT stations unavailable: ${error.message}`);
    }
}
$('#layerCdot').onclick = () => setCdotVisible(!cdotVisible);

function describeCdotLookup(result) {
    if (!result) return '';
    if (result.error) return `<div class="assetmeta">${esc(result.error)}</div>`;
    const {milepoint, patrol, nearest_station: station} = result;
    const parts = [];
    parts.push(milepoint
        ? `<div class="assetname">Route ${esc(milepoint.route)} · MP ${esc(milepoint.refpt)}</div><div class="assetmeta">${esc(milepoint.distance_m)} m from the milepoint marker</div>`
        : '<div class="assetname">Not on a CDOT highway</div><div class="assetmeta">No milepoint within 2 km</div>');
    if (patrol) parts.push(`<div class="assetmeta">Region ${esc(patrol.region)} · Section ${esc(patrol.maintenance_section)} · Patrol ${esc(patrol.patrol)} (${esc(patrol.supervisor_area)}) · MP ${esc(patrol.begin_refpt)}–${esc(patrol.end_refpt)}</div>`);
    if (station) parts.push(`<div class="assetmeta">Nearest port of entry: ${esc(cdotStationLabel(station))}, ${(station.distance_m / 1000).toFixed(1)} km</div>`);
    return parts.join('');
}

async function locateAssetOnHighway(asset) {
    cdotLookup = {assetId:asset.id, pending:true};
    if (view === 'stations') renderSidebar();
    try {
        const result = await api(`/v1/cdot/locate?lat=${encodeURIComponent(asset.lat)}&lng=${encodeURIComponent(asset.lng)}`);
        cdotLookup = {assetId:asset.id, result};
    } catch (error) {
        cdotLookup = {assetId:asset.id, result:{error:error.message}};
    }
    if (view === 'stations') renderSidebar();
}

function renderStations(root) {
    const asset = assets.find(item => item.id === selectedAsset && item.lat != null);
    const lookup = asset && cdotLookup?.assetId === asset.id ? cdotLookup : null;
    const locate = asset
        ? `<div class="asset" id="cdotLocate"><i class="assetdot" style="background:#ff9f43"></i><div class="assetinfo"><div class="assetname">Locate ${esc(asset.name)} on CDOT highways</div><div class="assetmeta">${lookup?.pending ? 'Looking up milepoint…' : 'Milepoint, maintenance patrol and nearest port of entry'}</div>${lookup?.result ? describeCdotLookup(lookup.result) : ''}</div></div>`
        : '<div class="empty">Select an asset with a GPS fix to find its highway milepoint and patrol.</div>';
    const list = cdotStations.map(station => `<div class="asset" data-cdot="${esc(station.id)}"><i class="assetdot" style="background:${station.status === 'planned' ? '#9aa9b8' : '#ff9f43'}"></i><div class="assetinfo"><div class="assetname">${esc(cdotStationLabel(station))}</div><div class="assetmeta">${esc(station.highway)} MP ${esc(station.milepost)} · ${station.status === 'planned' ? 'Planned' : esc(station.phone || 'Open')}</div></div></div>`).join('');
    root.innerHTML = inspectionSitesSection() + locate + (list || `<div class="empty">${cdotLoaded ? 'No CDOT stations loaded. Run the cdot-stations loader.' : 'Loading CDOT stations…'}</div>`);
    root.querySelector('#cdotLocate')?.addEventListener('click', () => locateAssetOnHighway(asset));
    root.querySelectorAll('[data-site]').forEach(el => el.onclick = () => {
        const site = inspectionSites.find(item => String(item.id) === el.dataset.site);
        if (site) { map.flyTo({center:[site.lng, site.lat], zoom:13}); openInspectionPopup(site); }
    });
    root.querySelectorAll('[data-cdot]').forEach(el => el.onclick = () => {
        const station = cdotStations.find(item => String(item.id) === el.dataset.cdot);
        if (!station) return;
        if (!cdotVisible) setCdotVisible(true);
        map.flyTo({center:[station.lng, station.lat], zoom:13});
    });
    if (!cdotLoaded) loadCdotStations().then(() => { if (view === 'stations') renderSidebar(); }).catch(error => toast(`CDOT stations unavailable: ${error.message}`));
}
// --- v3.1: non-permanent safety inspection stations ---
let inspectionSites = [], inspectionVisible = true, inspectionPoll = null, inspectionPopup = null;

function liveInspectionSites() {
    const now = Date.now();
    return inspectionSites.filter(site => Date.parse(site.ends_at) > now)
        .map(site => ({...site, status: Date.parse(site.starts_at) > now ? 'scheduled' : 'active'}));
}

function inspectionFeatures() {
    return {
        type:'FeatureCollection',
        features:liveInspectionSites().map(site => ({
            type:'Feature',
            geometry:{type:'Point', coordinates:[site.lng, site.lat]},
            properties:{id:String(site.id), status:site.status}
        }))
    };
}

function initInspectionLayer() {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 48;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    // Octagon badge, deliberately unlike the triangular 2-hour checkpoint pin.
    ctx.beginPath();
    for (let i = 0; i < 8; i++) {
        const angle = Math.PI / 8 + i * Math.PI / 4;
        const x = 24 + 20 * Math.cos(angle), y = 24 + 20 * Math.sin(angle);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = '#5aa9ff';
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#06121f';
    ctx.stroke();
    ctx.fillStyle = '#06121f';
    ctx.fillRect(14, 17, 20, 4);
    ctx.fillRect(14, 27, 20, 4);
    map.addImage('inspection-site', ctx.getImageData(0, 0, 48, 48), {pixelRatio:2});
    map.addSource('inspection-sites', {type:'geojson', data:inspectionFeatures()});
    map.addLayer({
        id:'inspection-site-symbols', type:'symbol', source:'inspection-sites',
        layout:{'icon-image':'inspection-site', 'icon-allow-overlap':true, visibility:inspectionVisible ? 'visible' : 'none'},
        paint:{'icon-opacity':['case', ['==', ['get', 'status'], 'scheduled'], 0.55, 1]}
    });
    map.on('click', 'inspection-site-symbols', e => {
        if (pinMode || fenceDraft) return;
        const site = liveInspectionSites().find(item => String(item.id) === e.features?.[0]?.properties.id);
        if (site) openInspectionPopup(site);
    });
    map.on('mouseenter', 'inspection-site-symbols', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'inspection-site-symbols', () => { map.getCanvas().style.cursor = ''; });
}

async function refreshInspectionSites() {
    if (!token) return;
    try {
        inspectionSites = await api('/v1/inspection-sites');
    } catch {
        return;
    }
    map?.getSource('inspection-sites')?.setData(inspectionFeatures());
    if (view === 'stations') renderSidebar();
}

function formatWhen(iso) {
    const when = new Date(iso);
    const sameDay = when.toDateString() === new Date().toDateString();
    return sameDay
        ? when.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})
        : when.toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
}

function inspectionWindow(site) {
    return site.status === 'scheduled'
        ? `Scheduled · ${formatWhen(site.starts_at)} – ${formatWhen(site.ends_at)}`
        : `Active until ${formatWhen(site.ends_at)}`;
}

function inspectionLocation(site) {
    const parts = [site.highway, site.milepost != null ? `MP ${site.milepost}` : null, site.direction].filter(Boolean);
    return parts.join(' · ');
}

function openInspectionPopup(site) {
    const live = liveInspectionSites().find(item => item.id === site.id) || site;
    const el = document.createElement('div');
    el.className = 'obs-popup';
    const line = (text, tag = 'div') => { const node = document.createElement(tag); node.textContent = text; el.append(node); return node; };
    line(live.name, 'b');
    line('Non-permanent safety inspection station');
    line(live.agency);
    if (inspectionLocation(live)) line(inspectionLocation(live));
    line(inspectionWindow(live));
    if (live.notes) line(live.notes);
    if (live.placed_by) line(`Placed by ${live.placed_by} · ${formatWhen(live.placed_at)}`).style.opacity = '0.75';
    if (user?.role === 'admin') {
        const end = document.createElement('button');
        end.className = 'btn';
        end.style.marginTop = '8px';
        end.textContent = live.status === 'scheduled' ? 'Cancel site' : 'End now';
        end.onclick = async () => {
            if (!confirm(`Take "${live.name}" off the map for your whole workspace?`)) return;
            end.disabled = true;
            try {
                await api(`/v1/inspection-sites/${live.id}/end`, {method:'POST'});
                inspectionPopup?.remove();
                toast('Inspection site ended');
                await refreshInspectionSites();
            } catch (error) {
                end.disabled = false;
                toast(error.message);
            }
        };
        el.append(end);
    }
    inspectionPopup?.remove();
    inspectionPopup = new maplibregl.Popup({offset:14}).setLngLat([live.lng, live.lat]).setDOMContent(el).addTo(map);
}

function setInspectionVisible(visible) {
    inspectionVisible = visible;
    $('#layerSites').classList.toggle('active', visible);
    $('#layerSites').setAttribute('aria-pressed', String(visible));
    if (map?.getLayer('inspection-site-symbols')) map.setLayoutProperty('inspection-site-symbols', 'visibility', visible ? 'visible' : 'none');
}
$('#layerSites').onclick = () => setInspectionVisible(!inspectionVisible);

function inspectionSitesSection() {
    const sites = liveInspectionSites();
    if (!sites.length) return user?.role === 'admin'
        ? '<div class="empty">No inspection sites up. Choose Drop observation, click the map, and pick "Non-permanent safety inspection station".</div>'
        : '';
    return sites.map(site => `<div class="asset" data-site="${esc(site.id)}"><i class="assetdot" style="background:#5aa9ff;opacity:${site.status === 'scheduled' ? 0.55 : 1}"></i><div class="assetinfo"><div class="assetname">${esc(site.name)}</div><div class="assetmeta">${esc(site.agency)}${inspectionLocation(site) ? ' · ' + esc(inspectionLocation(site)) : ''}</div><div class="assetmeta">${esc(inspectionWindow(site))}</div></div></div>`).join('');
}

function localInputValue(date) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function openInspectionSite(point) {
    if (user?.role !== 'admin') return;
    pendingPoint = point;
    const start = new Date();
    const end = new Date(start.getTime() + 4 * 3600000);
    $('#modalTitle').textContent = 'Add a non-permanent inspection station';
    $('#modalSub').textContent = `${point[1].toFixed(5)}, ${point[0].toFixed(5)} · visible to everyone in your workspace until it ends`;
    $('#modalFields').innerHTML = `<label class="field">Station name<input id="siteName" maxlength="120" placeholder="e.g. I-70 Silverthorne pull-off"></label>
<label class="field">Agency<input id="siteAgency" maxlength="80" list="siteAgencies" placeholder="Who runs it"><datalist id="siteAgencies"><option value="Colorado State Patrol"><option value="CDOT Port of Entry"><option value="FMCSA"></datalist></label>
<label class="field">Highway<input id="siteHighway" maxlength="40" placeholder="Looking up…"></label>
<label class="field">Milepost<input id="siteMilepost" type="number" min="0" max="9999" step="0.1"></label>
<label class="field">Direction<select id="siteDirection"><option value="">Not set</option><option>NB</option><option>SB</option><option>EB</option><option>WB</option><option>Both</option></select></label>
<label class="field">Starts<input id="siteStarts" type="datetime-local" value="${localInputValue(start)}"></label>
<label class="field">Ends<input id="siteEnds" type="datetime-local" value="${localInputValue(end)}"></label>
<label class="field">Notes<textarea id="siteNotes" maxlength="1000" placeholder="Pull-off details, inspection level, contact"></textarea></label>`;
    $('#modalSave').textContent = 'Add station';
    $('#modalSave').onclick = saveInspectionSite;
    $('#modalback').style.display = 'grid';
    setTimeout(() => $('#siteName')?.focus(), 50);
    api(`/v1/cdot/locate?lat=${encodeURIComponent(point[1])}&lng=${encodeURIComponent(point[0])}`).then(result => {
        const highway = $('#siteHighway'), milepost = $('#siteMilepost');
        if (!highway || pendingPoint !== point) return;
        highway.placeholder = 'e.g. I-70';
        if (result.milepoint && !highway.value) highway.value = `Route ${result.milepoint.route}`;
        if (result.milepoint && !milepost.value) milepost.value = result.milepoint.refpt;
    }).catch(() => { const highway = $('#siteHighway'); if (highway) highway.placeholder = 'e.g. I-70'; });
}

async function saveInspectionSite() {
    const name = $('#siteName').value.trim(), agency = $('#siteAgency').value.trim();
    if (!name || !agency) { toast('Add a station name and agency'); return; }
    const starts = new Date($('#siteStarts').value), ends = new Date($('#siteEnds').value);
    if (Number.isNaN(starts.getTime()) || Number.isNaN(ends.getTime())) { toast('Set when the station starts and ends'); return; }
    const milepost = $('#siteMilepost').value;
    const body = {
        name, agency, lat:pendingPoint[1], lng:pendingPoint[0],
        highway:$('#siteHighway').value.trim() || null,
        milepost:milepost === '' ? null : Number(milepost),
        direction:$('#siteDirection').value || null,
        notes:$('#siteNotes').value,
        starts_at:starts.toISOString(), ends_at:ends.toISOString()
    };
    $('#modalSave').disabled = true;
    try {
        await api('/v1/inspection-sites', {method:'POST', body:JSON.stringify(body)});
        closeModal();
        toast('Inspection station added for your workspace');
        if (!inspectionVisible) setInspectionVisible(true);
        await refreshInspectionSites();
    } catch (error) {
        toast(error.message);
    } finally {
        $('#modalSave').disabled = false;
    }
}
// --- v3.2: saved geofences drawn on the map ---
let geofences = [], geofencesVisible = true, geofencePopup = null;

function geofenceRing(fence) {
    const g = fence.geometry || {};
    if (fence.shape === 'polygon') return Array.isArray(g.coordinates) ? g.coordinates : [];
    if (![g.lat, g.lng, g.radius_m].every(Number.isFinite)) return [];
    // A circle as a 64-sided ring at its true ground radius.
    const earth = 6371008.8, lat = g.lat * Math.PI / 180, d = g.radius_m / earth;
    const ring = [];
    for (let i = 0; i < 64; i++) {
        const bearing = 2 * Math.PI * i / 64;
        const lat2 = Math.asin(Math.sin(lat) * Math.cos(d) + Math.cos(lat) * Math.sin(d) * Math.cos(bearing));
        const lng2 = g.lng * Math.PI / 180 + Math.atan2(Math.sin(bearing) * Math.sin(d) * Math.cos(lat), Math.cos(d) - Math.sin(lat) * Math.sin(lat2));
        ring.push([lng2 * 180 / Math.PI, lat2 * 180 / Math.PI]);
    }
    return ring;
}

function geofenceFeatures() {
    return {
        type:'FeatureCollection',
        features:geofences.map(fence => ({fence, ring:geofenceRing(fence)})).filter(({ring}) => ring.length >= 3).map(({fence, ring}) => ({
            type:'Feature',
            geometry:{type:'Polygon', coordinates:[[...ring, ring[0]]]},
            properties:{id:String(fence.id), enabled:fence.enabled !== false}
        }))
    };
}

function initGeofenceLayer() {
    map.addSource('geofences', {type:'geojson', data:geofenceFeatures()});
    const visibility = geofencesVisible ? 'visible' : 'none';
    map.addLayer({id:'geofence-fill', type:'fill', source:'geofences', layout:{visibility},
        paint:{'fill-color':'#57d3c1', 'fill-opacity':['case', ['get', 'enabled'], 0.12, 0.05]}});
    map.addLayer({id:'geofence-outline', type:'line', source:'geofences', layout:{visibility, 'line-join':'round'},
        paint:{'line-color':'#57d3c1', 'line-width':2, 'line-opacity':['case', ['get', 'enabled'], 0.9, 0.4]}});
    map.on('click', 'geofence-fill', e => {
        if (pinMode || fenceDraft) return;
        // A point or symbol above the fence gets the click instead.
        const above = map.queryRenderedFeatures(e.point, {layers:['checkpoint-symbols', 'inspection-site-symbols', 'cdot-station-dots', 'alert-dots'].filter(id => map.getLayer(id))});
        if (above.length) return;
        const fence = geofences.find(item => String(item.id) === e.features?.[0]?.properties.id);
        if (fence) openGeofencePopup(fence, e.lngLat);
    });
}

async function refreshGeofences() {
    if (!token) return;
    try {
        geofences = await api('/geofences');
    } catch {
        return;
    }
    map?.getSource('geofences')?.setData(geofenceFeatures());
}

function geofenceSummary(fence) {
    return fence.shape === 'circle'
        ? `Circle · ${Math.round(fence.geometry.radius_m).toLocaleString()} m radius`
        : `Polygon · ${fence.geometry.coordinates.length} corners`;
}

function openGeofencePopup(fence, lngLat) {
    const el = document.createElement('div');
    el.className = 'obs-popup';
    const line = (text, tag = 'div') => { const node = document.createElement(tag); node.textContent = text; el.append(node); return node; };
    line(fence.name, 'b');
    line('Geofence');
    line(geofenceSummary(fence));
    line(fence.enabled === false ? 'Alerts paused' : 'Enter and exit alerts on');
    if (user?.role === 'admin') {
        const paused = fence.enabled === false;
        const toggle = document.createElement('button');
        toggle.className = 'btn';
        toggle.style.marginTop = '8px';
        toggle.style.marginRight = '6px';
        toggle.textContent = paused ? 'Resume alerts' : 'Pause alerts';
        toggle.onclick = async () => {
            toggle.disabled = true;
            try {
                const updated = await api(`/geofences/${fence.id}`, {method:'PATCH', body:JSON.stringify({enabled:paused})});
                toast(updated.enabled ? 'Geofence alerts resumed' : 'Geofence alerts paused');
                await refreshGeofences();
                const fresh = geofences.find(item => item.id === fence.id);
                if (fresh) openGeofencePopup(fresh, lngLat); else geofencePopup?.remove();
            } catch (error) {
                toggle.disabled = false;
                toast(error.message);
            }
        };
        el.append(toggle);
        const remove = document.createElement('button');
        remove.className = 'btn';
        remove.style.marginTop = '8px';
        remove.textContent = 'Delete geofence';
        remove.onclick = async () => {
            if (!confirm(`Delete "${fence.name}"? Its past alerts are deleted with it.`)) return;
            remove.disabled = true;
            try {
                await api(`/geofences/${fence.id}`, {method:'DELETE'});
                geofencePopup?.remove();
                toast('Geofence deleted');
                await refreshGeofences();
            } catch (error) {
                remove.disabled = false;
                toast(error.message);
            }
        };
        el.append(remove);
    }
    geofencePopup?.remove();
    geofencePopup = new maplibregl.Popup({offset:8}).setLngLat(lngLat).setDOMContent(el).addTo(map);
}

function setGeofencesVisible(visible) {
    geofencesVisible = visible;
    $('#layerFences').classList.toggle('active', visible);
    $('#layerFences').setAttribute('aria-pressed', String(visible));
    for (const id of ['geofence-fill', 'geofence-outline']) if (map?.getLayer(id)) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    if (!visible) geofencePopup?.remove();
}
$('#layerFences').onclick = () => setGeofencesVisible(!geofencesVisible);
// --- v3.3: geofence alerts on the map ---
const ALERT_WINDOW_MS = 24 * 3600000;
let alertDots = [], alertsVisible = true, alertPopup = null, alertRefreshTimer = null;

function recentAlertDots() {
    const since = Date.now() - ALERT_WINDOW_MS;
    return alertDots.filter(alert => Number.isFinite(alert.lat) && Number.isFinite(alert.lng)
        && Date.parse(alert.occurred_at || alert.created_at) >= since);
}

function alertFeatures() {
    return {
        type:'FeatureCollection',
        features:recentAlertDots().map(alert => ({
            type:'Feature',
            geometry:{type:'Point', coordinates:[alert.lng, alert.lat]},
            properties:{id:String(alert.id), event:alert.event || ''}
        }))
    };
}

function initAlertLayer() {
    map.addSource('alert-dots', {type:'geojson', data:alertFeatures()});
    map.addLayer({id:'alert-dots', type:'circle', source:'alert-dots', layout:{visibility:alertsVisible ? 'visible' : 'none'},
        paint:{'circle-radius':6, 'circle-color':['match', ['get', 'event'], 'exit', '#ff8a65', '#f2b95d'],
            'circle-stroke-color':'#0b1118', 'circle-stroke-width':2}});
    map.on('click', 'alert-dots', e => {
        if (pinMode || fenceDraft) return;
        const alert = alertDots.find(item => String(item.id) === e.features?.[0]?.properties.id);
        if (!alert) return;
        const el = document.createElement('div');
        el.className = 'obs-popup';
        const line = (text, tag = 'div') => { const node = document.createElement(tag); node.textContent = text; el.append(node); };
        line(alert.message, 'b');
        if (alert.asset_name) line(alert.asset_name);
        line(new Date(alert.occurred_at || alert.created_at).toLocaleString());
        alertPopup?.remove();
        alertPopup = new maplibregl.Popup({offset:10}).setLngLat([alert.lng, alert.lat]).setDOMContent(el).addTo(map);
    });
    map.on('mouseenter', 'alert-dots', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'alert-dots', () => { map.getCanvas().style.cursor = ''; });
}

async function refreshAlertDots() {
    if (!token) return;
    try {
        alertDots = await api('/alerts');
    } catch {
        return;
    }
    map?.getSource('alert-dots')?.setData(alertFeatures());
}

function scheduleAlertRefresh() {
    // Fixes can arrive every few seconds; refresh alert dots at most once per burst.
    clearTimeout(alertRefreshTimer);
    alertRefreshTimer = setTimeout(refreshAlertDots, 3000);
}

function setAlertsVisible(visible) {
    alertsVisible = visible;
    $('#layerAlerts').classList.toggle('active', visible);
    $('#layerAlerts').setAttribute('aria-pressed', String(visible));
    if (map?.getLayer('alert-dots')) map.setLayoutProperty('alert-dots', 'visibility', visible ? 'visible' : 'none');
    if (!visible) alertPopup?.remove();
}
$('#layerAlerts').onclick = () => setAlertsVisible(!alertsVisible);
async function start(){try{user=await api('/me');$('#userEmail').textContent=user.email;$('#teamTab').hidden=user.role!=='admin';setLogin(false);initMap();await Promise.all([refreshAssets(),refreshObs(),refreshInspectionSites(),refreshGeofences(),refreshAlertDots()]);flushPendingObservations();syncSafety()?.catch(()=>{});connectLive();clearInterval(assetPoll);clearInterval(observationPoll);assetPoll=setInterval(refreshAssets,15000);observationPoll=setInterval(refreshObs,60000);clearInterval(inspectionPoll);inspectionPoll=setInterval(()=>{refreshInspectionSites();refreshGeofences();refreshAlertDots()},60000)}catch(e){setLogin(true)}}if(token&&!inviteToken)start();
