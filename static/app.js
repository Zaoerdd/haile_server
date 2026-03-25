const TAB_TITLES = {
    washTab: '洗衣',
    reservationTab: '预约',
    orderTab: '订单',
    settingsTab: '设置',
};

const DEFAULT_TOKEN_STATUS = {
    source: 'env',
    configured: false,
    valid: false,
    reason: 'missing',
    message: '当前未配置可用 Token，请前往设置页处理。',
};

const state = {
    activeTab: 'washTab',
    tokenStatus: Object.assign({}, DEFAULT_TOKEN_STATUS),
    security: null,
    scheduler: null,
    settings: null,
    wash: {
        loading: false,
        rooms: [],
        scanMachines: [],
        view: 'home',
        roomId: null,
        roomData: null,
        machineId: null,
        machineDetail: null,
        scanMachine: null,
        result: null,
    },
    reservations: {
        items: [],
        roomMachines: [],
        modeOptions: [],
    },
    orders: {
        items: [],
        page: 1,
        pageSize: 10,
        hasMore: true,
        total: 0,
        loading: false,
        expanded: {},
    },
    ui: {
        toastTimer: null,
        tokenAlertReason: null,
    },
};

const cache = {
    roomMachines: new Map(),
    machineDetails: new Map(),
    scanModes: new Map(),
    orderDetails: new Map(),
};

const el = {};

document.addEventListener('DOMContentLoaded', async () => {
    bindElements();
    bindEvents();
    initOrderObserver();
    primeInputs();
    renderWash();
    renderReservations();
    renderOrders();
    renderSettings();
    switchTab('washTab');

    try {
        await loadConfig();
        await loadSettings();
        await loadReservations();

        if (isTokenReady()) {
            await loadLaundrySections();
            await hydrateReservationForm();
        } else {
            renderWash();
            renderOrders();
        }
    } catch (err) {
        handleRequestError(err, '初始化失败，请检查服务端日志。');
    }
});

function bindElements() {
    el.pageTitle = document.getElementById('pageTitle');
    el.topStatus = document.getElementById('topStatus');
    el.tabPanels = Array.from(document.querySelectorAll('.tab-panel'));
    el.tabButtons = Array.from(document.querySelectorAll('.tabbar-btn'));
    el.toast = document.getElementById('toast');

    el.washView = document.getElementById('washView');
    el.refreshWashBtn = document.getElementById('refreshWashBtn');

    el.reservationForm = document.getElementById('reservationForm');
    el.reservationSource = document.getElementById('reservationSource');
    el.reservationScanWrap = document.getElementById('reservationScanWrap');
    el.reservationScanMachine = document.getElementById('reservationScanMachine');
    el.reservationRoomWrap = document.getElementById('reservationRoomWrap');
    el.reservationRoom = document.getElementById('reservationRoom');
    el.reservationMachineWrap = document.getElementById('reservationMachineWrap');
    el.reservationMachine = document.getElementById('reservationMachine');
    el.reservationMode = document.getElementById('reservationMode');
    el.reservationScheduleType = document.getElementById('reservationScheduleType');
    el.reservationTargetWrap = document.getElementById('reservationTargetWrap');
    el.reservationTargetTime = document.getElementById('reservationTargetTime');
    el.reservationWeekdayWrap = document.getElementById('reservationWeekdayWrap');
    el.reservationWeekday = document.getElementById('reservationWeekday');
    el.reservationWeeklyTimeWrap = document.getElementById('reservationWeeklyTimeWrap');
    el.reservationWeeklyTime = document.getElementById('reservationWeeklyTime');
    el.reservationLeadMinutes = document.getElementById('reservationLeadMinutes');
    el.reservationTitle = document.getElementById('reservationTitle');
    el.reservationList = document.getElementById('reservationList');
    el.refreshReservationsBtn = document.getElementById('refreshReservationsBtn');

    el.ordersList = document.getElementById('ordersList');
    el.refreshOrdersBtn = document.getElementById('refreshOrdersBtn');
    el.ordersSentinel = document.getElementById('ordersSentinel');
    el.loadMoreOrdersBtn = document.getElementById('loadMoreOrdersBtn');

    el.settingsForm = document.getElementById('settingsForm');
    el.settingsToken = document.getElementById('settingsToken');
    el.settingsPushplusUrl = document.getElementById('settingsPushplusUrl');
    el.settingsLeadMinutes = document.getElementById('settingsLeadMinutes');
    el.settingsInfo = document.getElementById('settingsInfo');
    el.refreshSettingsBtn = document.getElementById('refreshSettingsBtn');
}

function bindEvents() {
    el.tabButtons.forEach(button => {
        button.addEventListener('click', () => switchTab(button.dataset.tab));
    });

    el.refreshWashBtn.addEventListener('click', async () => {
        if (!ensureTokenReady()) {
            renderWash();
            return;
        }
        await loadLaundrySections(true);
        await hydrateReservationForm();
    });

    el.refreshReservationsBtn.addEventListener('click', async () => {
        await loadReservations();
    });

    el.refreshOrdersBtn.addEventListener('click', async () => {
        if (!ensureTokenReady()) {
            renderOrders();
            return;
        }
        await loadOrders(true);
    });

    el.refreshSettingsBtn.addEventListener('click', async () => {
        await loadSettings();
    });

    el.washView.addEventListener('click', handleWashClick);
    el.reservationList.addEventListener('click', handleReservationClick);
    el.ordersList.addEventListener('click', handleOrderClick);
    el.settingsForm.addEventListener('submit', handleSettingsSubmit);
    el.reservationForm.addEventListener('submit', handleReservationSubmit);
    el.loadMoreOrdersBtn.addEventListener('click', async () => {
        if (!state.orders.loading && state.orders.hasMore && ensureTokenReady()) {
            await loadOrders(false);
        }
    });

    el.reservationSource.addEventListener('change', async () => {
        toggleReservationSourceFields();
        await refreshReservationMachineOptions();
    });

    el.reservationRoom.addEventListener('change', async () => {
        await refreshReservationMachineOptions();
    });

    el.reservationMachine.addEventListener('change', async () => {
        await refreshReservationModes();
    });

    el.reservationScanMachine.addEventListener('change', async () => {
        await refreshReservationModes();
    });

    el.reservationScheduleType.addEventListener('change', () => {
        toggleReservationScheduleFields();
    });
}

function initOrderObserver() {
    if (!('IntersectionObserver' in window)) {
        return;
    }
    const observer = new IntersectionObserver(async entries => {
        const visible = entries.some(entry => entry.isIntersecting);
        if (!visible) {
            return;
        }
        if (state.activeTab !== 'orderTab' || state.orders.loading || !state.orders.hasMore || !isTokenReady()) {
            return;
        }
        await loadOrders(false);
    }, { rootMargin: '180px 0px' });
    observer.observe(el.ordersSentinel);
}

function primeInputs() {
    const future = new Date(Date.now() + 2 * 60 * 60 * 1000);
    future.setMinutes(Math.ceil(future.getMinutes() / 5) * 5, 0, 0);
    el.reservationTargetTime.value = toLocalInputValue(future);
    el.reservationWeeklyTime.value = '08:00';
}

function switchTab(tabId) {
    state.activeTab = tabId;
    el.tabPanels.forEach(panel => {
        panel.classList.toggle('active', panel.id === tabId);
    });
    el.tabButtons.forEach(button => {
        button.classList.toggle('active', button.dataset.tab === tabId);
    });
    el.pageTitle.textContent = TAB_TITLES[tabId] || '海乐洗衣助手';

    if (tabId === 'orderTab' && isTokenReady() && !state.orders.items.length && !state.orders.loading) {
        loadOrders(true).catch(err => handleRequestError(err, '加载订单失败。'));
    }
}

function normalizeTokenStatus(tokenStatus = {}) {
    return {
        source: tokenStatus.source || 'env',
        configured: Boolean(tokenStatus.configured),
        valid: Boolean(tokenStatus.valid),
        reason: tokenStatus.reason || 'missing',
        message: tokenStatus.message || 'Token 状态未知。',
    };
}

function isTokenReady() {
    return state.tokenStatus.valid && state.tokenStatus.reason === 'ok';
}

function getTokenAlertMessage() {
    if (state.tokenStatus.reason === 'missing') {
        return state.tokenStatus.message || '当前没有可用 Token，请到设置页填写或检查 .env。';
    }
    if (state.tokenStatus.reason === 'invalid') {
        return state.tokenStatus.message || 'Token 已失效，请更新后重试。';
    }
    if (state.tokenStatus.reason === 'check_failed') {
        return state.tokenStatus.message || '暂时无法校验 Token，请稍后刷新页面。';
    }
    return state.tokenStatus.message || '当前 Token 不可用。';
}

function tokenClassName(reason) {
    if (reason === 'ok') {
        return 'success';
    }
    if (reason === 'check_failed') {
        return 'warning';
    }
    return 'danger';
}

function tokenLabel() {
    if (state.tokenStatus.reason === 'ok') {
        return 'Token 正常';
    }
    if (state.tokenStatus.reason === 'check_failed') {
        return '校验失败';
    }
    if (state.tokenStatus.reason === 'invalid') {
        return 'Token 无效';
    }
    return '未配置 Token';
}

function applyTokenStatus(tokenStatus, notify = false) {
    state.tokenStatus = normalizeTokenStatus(tokenStatus);
    el.topStatus.className = `status-pill ${tokenClassName(state.tokenStatus.reason)}`;
    el.topStatus.textContent = tokenLabel();
    updateFormAvailability();

    if (notify && !isTokenReady()) {
        notifyTokenProblem();
    }
}

function notifyTokenProblem(force = false) {
    const reason = state.tokenStatus.reason;
    if (!force && state.ui.tokenAlertReason === reason) {
        return;
    }
    state.ui.tokenAlertReason = reason;
    window.alert(getTokenAlertMessage());
}

function ensureTokenReady() {
    if (isTokenReady()) {
        return true;
    }
    notifyTokenProblem(true);
    switchTab('settingsTab');
    return false;
}

function updateFormAvailability() {
    const shouldDisable = !isTokenReady();
    Array.from(el.reservationForm.elements).forEach(field => {
        field.disabled = shouldDisable;
    });
    el.refreshWashBtn.disabled = shouldDisable;
    el.refreshOrdersBtn.disabled = shouldDisable;
    el.loadMoreOrdersBtn.disabled = shouldDisable || !state.orders.hasMore;
}

function setWashLoading(message) {
    state.wash.loading = true;
    el.washView.innerHTML = `<div class="panel-card loading-card">${escapeHtml(message)}</div>`;
}

async function loadConfig() {
    const data = await apiGet('/api/config');
    state.security = data.security || null;
    state.scheduler = data.scheduler || state.scheduler;
    state.wash.scanMachines = data.scanMachines || [];
    applyTokenStatus(data.tokenStatus || DEFAULT_TOKEN_STATUS, !((data.tokenStatus || {}).valid));
}

async function loadSettings() {
    const data = await apiGet('/api/settings');
    state.settings = data.settings || null;
    state.scheduler = data.scheduler || state.scheduler;
    applyTokenStatus(data.tokenStatus || state.tokenStatus);

    if (state.settings) {
        el.settingsToken.value = state.settings.token || '';
        el.settingsPushplusUrl.value = state.settings.pushplusUrl || '';
        el.settingsLeadMinutes.value = state.settings.defaultLeadMinutes || 60;
        el.reservationLeadMinutes.value = state.settings.defaultLeadMinutes || 60;
    }

    renderSettings();
}

async function loadLaundrySections(showToast = false) {
    setWashLoading('正在加载洗衣房和扫码机组...');
    try {
        const data = await apiGet('/api/laundry/sections');
        state.wash.rooms = data.rooms || [];
        state.wash.scanMachines = data.scanMachines || state.wash.scanMachines;
        state.wash.view = 'home';
        state.wash.roomId = null;
        state.wash.roomData = null;
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.scanMachine = null;
        state.wash.loading = false;
        renderWash();
        if (showToast) {
            showToastMessage('洗衣页面已刷新。');
        }
    } catch (err) {
        syncTokenFailure(err);
        state.wash.rooms = [];
        state.wash.loading = false;
        renderWash();
        throw err;
    }
}

async function loadReservations() {
    const data = await apiGet('/api/reservations');
    state.reservations.items = data.items || [];
    state.scheduler = data.scheduler || state.scheduler;
    renderReservations();
}

async function loadOrders(reset) {
    if (state.orders.loading) {
        return;
    }

    const nextPage = reset ? 1 : state.orders.page + 1;
    if (!reset && !state.orders.hasMore) {
        return;
    }

    state.orders.loading = true;
    if (reset) {
        state.orders.items = [];
        state.orders.page = 0;
        state.orders.hasMore = true;
        renderOrders();
    }

    try {
        const data = await apiPost('/api/orders/history', {
            page: nextPage,
            pageSize: state.orders.pageSize,
        });
        state.orders.items = reset ? (data.items || []) : state.orders.items.concat(data.items || []);
        state.orders.page = data.page || nextPage;
        state.orders.total = data.total || state.orders.items.length;
        state.orders.hasMore = Boolean(data.hasMore);
        renderOrders();
    } catch (err) {
        syncTokenFailure(err);
        handleRequestError(err, '读取订单失败。', true);
    } finally {
        state.orders.loading = false;
        renderOrders();
    }
}

async function getRoomMachines(positionId) {
    if (cache.roomMachines.has(positionId)) {
        return cache.roomMachines.get(positionId);
    }
    const data = await apiGet(`/api/laundry/rooms/${encodeURIComponent(positionId)}/machines`);
    cache.roomMachines.set(positionId, data);
    return data;
}

async function getMachineDetail(goodsId) {
    if (cache.machineDetails.has(goodsId)) {
        return cache.machineDetails.get(goodsId);
    }
    const data = await apiGet(`/api/laundry/machines/${encodeURIComponent(goodsId)}`);
    cache.machineDetails.set(goodsId, data.machine);
    return data.machine;
}

async function getScanModes(qrCode) {
    if (cache.scanModes.has(qrCode)) {
        return cache.scanModes.get(qrCode);
    }
    const data = await apiPost('/api/get_modes', { qrCode });
    const result = data.modes || [];
    cache.scanModes.set(qrCode, result);
    return result;
}

async function getOrderDetail(orderNo, forceRefresh = false) {
    if (!forceRefresh && cache.orderDetails.has(orderNo)) {
        return cache.orderDetails.get(orderNo);
    }
    const data = await apiGet(`/api/orders/${encodeURIComponent(orderNo)}`);
    cache.orderDetails.set(orderNo, data.order);
    return data.order;
}

async function hydrateReservationForm() {
    populateReservationScanOptions();
    populateReservationRoomOptions();
    toggleReservationSourceFields();
    toggleReservationScheduleFields();
    await refreshReservationMachineOptions();
}

function populateReservationScanOptions() {
    const items = state.wash.scanMachines || [];
    fillSelect(
        el.reservationScanMachine,
        items.map(machine => ({
            value: machine.qrCode,
            label: machine.label,
        })),
        '请选择扫码机组'
    );
}

function populateReservationRoomOptions() {
    const rooms = state.wash.rooms || [];
    fillSelect(
        el.reservationRoom,
        rooms.map(room => ({
            value: room.id,
            label: room.name,
        })),
        '请选择洗衣房'
    );
}

async function refreshReservationMachineOptions() {
    populateReservationModeOptions([]);

    if (!isTokenReady()) {
        fillSelect(el.reservationMachine, [], 'Token 不可用');
        return;
    }

    if (el.reservationSource.value === 'scan') {
        fillSelect(el.reservationMachine, [], '扫码机组不需要选择洗衣机');
        await refreshReservationModes();
        return;
    }

    const roomId = el.reservationRoom.value;
    if (!roomId) {
        fillSelect(el.reservationMachine, [], '请先选择洗衣房');
        return;
    }

    try {
        const payload = await getRoomMachines(roomId);
        const machines = (payload.machines || []).filter(machine => machine.supportsVirtualScan);
        state.reservations.roomMachines = machines;
        fillSelect(
            el.reservationMachine,
            machines.map(machine => ({
                value: machine.goodsId,
                label: `${machine.name} · ${machine.stateDesc}`,
            })),
            machines.length ? '请选择洗衣机' : '当前洗衣房暂无可预约机器'
        );
        await refreshReservationModes();
    } catch (err) {
        syncTokenFailure(err);
        handleRequestError(err, '加载预约机器失败。', true);
    }
}

async function refreshReservationModes() {
    populateReservationModeOptions([]);

    if (!isTokenReady()) {
        return;
    }

    try {
        if (el.reservationSource.value === 'scan') {
            const qrCode = el.reservationScanMachine.value;
            if (!qrCode) {
                return;
            }
            const modes = await getScanModes(qrCode);
            populateReservationModeOptions(modes);
            return;
        }

        const goodsId = el.reservationMachine.value;
        if (!goodsId) {
            return;
        }
        const detail = await getMachineDetail(goodsId);
        const modes = (detail.modes || []).filter(() => detail.supportsVirtualScan);
        populateReservationModeOptions(modes);
    } catch (err) {
        syncTokenFailure(err);
        handleRequestError(err, '读取预约模式失败。', true);
    }
}

function populateReservationModeOptions(modes) {
    state.reservations.modeOptions = modes || [];
    fillSelect(
        el.reservationMode,
        (modes || []).map(mode => ({
            value: String(mode.id),
            label: `${mode.label} · ${mode.price} 元`,
        })),
        modes && modes.length ? '请选择模式' : '暂无模式'
    );
}

function toggleReservationSourceFields() {
    const isScan = el.reservationSource.value === 'scan';
    el.reservationScanWrap.classList.toggle('hidden', !isScan);
    el.reservationRoomWrap.classList.toggle('hidden', isScan);
    el.reservationMachineWrap.classList.toggle('hidden', isScan);
}

function toggleReservationScheduleFields() {
    const isOnce = el.reservationScheduleType.value === 'once';
    el.reservationTargetWrap.classList.toggle('hidden', !isOnce);
    el.reservationWeekdayWrap.classList.toggle('hidden', isOnce);
    el.reservationWeeklyTimeWrap.classList.toggle('hidden', isOnce);
}

function renderWash() {
    if (state.wash.loading) {
        return;
    }

    if (!isTokenReady()) {
        el.washView.innerHTML = [
            renderStatusCard('danger', '洗衣功能暂不可用', getTokenAlertMessage(), `<div class="action-row"><button class="btn btn-primary" data-action="goto-settings">去设置</button></div>`),
            renderMachineGroupIntro(),
        ].join('');
        return;
    }

    if (state.wash.view === 'room' && state.wash.roomData) {
        el.washView.innerHTML = renderRoomView();
        return;
    }

    if (state.wash.view === 'machine' && state.wash.machineDetail) {
        el.washView.innerHTML = renderMachineView();
        return;
    }

    if (state.wash.view === 'scan' && state.wash.scanMachine) {
        el.washView.innerHTML = renderScanView();
        return;
    }

    el.washView.innerHTML = renderWashHome();
}

function renderWashHome() {
    const roomCards = (state.wash.rooms || []).map(room => `
        <article class="list-card">
            <div class="card-title">
                <div>
                    <h3>${escapeHtml(room.name)}</h3>
                    <p>${escapeHtml(room.address || '暂无地址信息')}</p>
                </div>
                <span class="chip ${room.enableReserve ? 'success' : 'pending'}">${room.enableReserve ? '可预约' : '普通机房'}</span>
            </div>
            <div class="detail-grid">
                <div><span>空闲设备</span><span>${escapeHtml(stringOrFallback(room.idleCount, '--'))}</span></div>
                <div><span>预约数量</span><span>${escapeHtml(stringOrFallback(room.reserveNum, '--'))}</span></div>
            </div>
            <div class="spacer-sm"></div>
            <div class="action-row">
                <button class="btn btn-secondary" data-action="open-room" data-room-id="${escapeHtml(room.id)}">查看设备</button>
            </div>
        </article>
    `).join('');

    const scanCards = (state.wash.scanMachines || []).map(machine => `
        <article class="list-card">
            <div class="card-title">
                <div>
                    <h3>${escapeHtml(machine.label)}</h3>
                    <p>二维码编号：${escapeHtml(maskCode(machine.qrCode))}</p>
                </div>
                <span class="chip success">虚拟扫码</span>
            </div>
            <div class="action-row">
                <button class="btn btn-primary" data-action="open-scan" data-qr-code="${escapeHtml(machine.qrCode)}">立即下单</button>
            </div>
        </article>
    `).join('');

    return [
        renderStatusCard('success', '服务端 Token 可用', state.tokenStatus.message),
        `
            <section class="sheet-card">
                <h3 class="section-title">洗衣房</h3>
                <p class="section-subtitle">先选洗衣房，再进入洗衣机列表做线上下单或虚拟扫码。</p>
                <div class="room-list">
                    ${roomCards || renderEmptyState('当前没有可用洗衣房', '稍后刷新，或检查定位与接口返回。')}
                </div>
            </section>
        `,
        `
            <section class="sheet-card">
                <h3 class="section-title">扫码机组</h3>
                <p class="section-subtitle">直接使用本地 machine.json 的编号走完整扫码支付流程。</p>
                <div class="scan-list">
                    ${scanCards || renderEmptyState('当前没有扫码机组配置', '请检查 machine.json 是否已配置。')}
                </div>
            </section>
        `,
        state.wash.result ? renderWashResult() : '',
    ].join('');
}

function renderRoomView() {
    const room = state.wash.roomData.room;
    const machines = state.wash.roomData.machines || [];
    const machineCards = machines.map(machine => `
        <article class="machine-card">
            <div class="card-title">
                <div>
                    <h3>${escapeHtml(machine.name)}</h3>
                    <p>${escapeHtml(machine.floorCode || '默认楼层')}</p>
                </div>
                <span class="chip ${machine.state === 1 ? 'success' : machine.state === 2 ? 'warning' : 'danger'}">${escapeHtml(machine.stateDesc)}</span>
            </div>
            <div class="inline-row">
                ${machine.supportsVirtualScan ? '<span class="chip success">支持虚拟扫码</span>' : '<span class="chip pending">仅可线上建单</span>'}
                ${machine.enableReserve ? '<span class="chip success">接口标记可预约</span>' : ''}
            </div>
            <div class="spacer-sm"></div>
            <div class="action-row">
                <button class="btn btn-secondary" data-action="open-machine" data-goods-id="${escapeHtml(machine.goodsId)}">查看详情</button>
            </div>
        </article>
    `).join('');

    return [
        `
            <div class="action-row">
                <button class="btn btn-light" data-action="back-home">返回全部入口</button>
            </div>
        `,
        `
            <section class="sheet-card">
                <div class="card-title">
                    <div>
                        <h3>${escapeHtml(room.name)}</h3>
                        <p>${escapeHtml(room.address || '暂无地址信息')}</p>
                    </div>
                    <span class="chip pending">${escapeHtml(String(machines.length))} 台设备</span>
                </div>
                <div class="machine-list">
                    ${machineCards || renderEmptyState('这个洗衣房暂时没有设备', '可以返回上一层重新选择。')}
                </div>
            </section>
        `,
        state.wash.result ? renderWashResult() : '',
    ].join('');
}

function renderMachineView() {
    const machine = state.wash.machineDetail;
    const modes = machine.modes || [];
    const selectOptions = modes.map(mode => `<option value="${escapeHtml(String(mode.id))}">${escapeHtml(`${mode.label} · ${mode.price} 元`)}</option>`).join('');
    const virtualScanDisabled = !machine.supportsVirtualScan;

    return [
        `
            <div class="action-row">
                <button class="btn btn-light" data-action="back-room">返回设备列表</button>
            </div>
        `,
        `
            <section class="sheet-card">
                <div class="card-title">
                    <div>
                        <h3>${escapeHtml(machine.name)}</h3>
                        <p>${escapeHtml(machine.shopName || '暂无所属洗衣房信息')}</p>
                    </div>
                    <span class="chip ${machine.supportsVirtualScan ? 'success' : 'warning'}">${machine.supportsVirtualScan ? '支持虚拟扫码' : '仅线上下单'}</span>
                </div>
                <div class="detail-grid">
                    <div><span>设备编号</span><span>${escapeHtml(machine.code || '--')}</span></div>
                    <div><span>商品编号</span><span>${escapeHtml(machine.goodsId || '--')}</span></div>
                    <div><span>类别</span><span>${escapeHtml(machine.categoryCode || '--')}</span></div>
                    <div><span>预约标记</span><span>${machine.enableReserve ? '是' : '否'}</span></div>
                </div>
                <div class="mode-select">
                    <label>
                        选择模式
                        <select id="washModeSelect">
                            ${selectOptions || '<option value="">暂无模式</option>'}
                        </select>
                    </label>
                </div>
                <div class="spacer-sm"></div>
                <div class="action-row">
                    <button class="btn btn-secondary" data-action="create-lock-order" ${modes.length ? '' : 'disabled'}>线上下单</button>
                    <button class="btn btn-primary" data-action="create-scan-order" ${virtualScanDisabled || !modes.length ? 'disabled' : ''}>虚拟扫码下单</button>
                </div>
                ${virtualScanDisabled ? '<div class="spacer-sm"></div><div class="callout warning">这个设备当前没有 machine.json 对应编号，本期只能做到线上创建订单，后续支付链路保持 TODO。</div>' : ''}
            </section>
        `,
        state.wash.result ? renderWashResult() : '',
    ].join('');
}

function renderScanView() {
    const machine = state.wash.scanMachine;
    const modes = machine.modes || [];
    const selectOptions = modes.map(mode => `<option value="${escapeHtml(String(mode.id))}">${escapeHtml(`${mode.label} · ${mode.price} 元`)}</option>`).join('');

    return [
        `
            <div class="action-row">
                <button class="btn btn-light" data-action="back-home">返回全部入口</button>
            </div>
        `,
        `
            <section class="sheet-card">
                <div class="card-title">
                    <div>
                        <h3>${escapeHtml(machine.label)}</h3>
                        <p>二维码编号：${escapeHtml(maskCode(machine.qrCode))}</p>
                    </div>
                    <span class="chip success">完整扫码流程</span>
                </div>
                <label>
                    选择模式
                    <select id="scanModeSelect">
                        ${selectOptions || '<option value="">暂无模式</option>'}
                    </select>
                </label>
                <div class="spacer-sm"></div>
                <div class="action-row">
                    <button class="btn btn-primary" data-action="create-scan-order" ${modes.length ? '' : 'disabled'}>开始扫码下单并支付</button>
                </div>
            </section>
        `,
        state.wash.result ? renderWashResult() : '',
    ].join('');
}

function renderWashResult() {
    const result = state.wash.result;
    const order = result.order;
    const orderInfo = order ? `
        <div class="detail-grid">
            <div><span>订单号</span><span>${escapeHtml(order.orderNo || '--')}</span></div>
            <div><span>状态</span><span>${escapeHtml(order.stateDesc || '--')}</span></div>
            <div><span>设备</span><span>${escapeHtml(order.machineName || '--')}</span></div>
            <div><span>模式</span><span>${escapeHtml(order.modeName || '--')}</span></div>
        </div>
    ` : '';
    const todo = result.todo ? `<div class="spacer-sm"></div><div class="callout warning">${escapeHtml(result.todo.nextStep || '后续步骤待补齐。')}</div>` : '';
    return renderStatusCard(result.variant, result.title, result.message, `${orderInfo}${todo}`);
}

function renderMachineGroupIntro() {
    const scanCards = (state.wash.scanMachines || []).map(machine => `
        <article class="list-card">
            <div class="card-title">
                <div>
                    <h3>${escapeHtml(machine.label)}</h3>
                    <p>二维码编号：${escapeHtml(maskCode(machine.qrCode))}</p>
                </div>
                <span class="chip pending">待配置 Token</span>
            </div>
        </article>
    `).join('');

    return `
        <section class="sheet-card">
            <h3 class="section-title">已识别的扫码机组</h3>
            <p class="section-subtitle">Token 恢复可用后，就可以直接从这里发起完整扫码流程。</p>
            <div class="scan-list">
                ${scanCards || renderEmptyState('当前没有扫码机组配置', '请检查 machine.json 是否已配置。')}
            </div>
        </section>
    `;
}

function renderReservations() {
    const schedulerCard = renderSchedulerCard();
    const tokenCard = !isTokenReady()
        ? renderStatusCard('warning', '预约建单当前不可用', '本地任务列表仍可查看，但创建任务和模式加载需要可用 Token。')
        : '';

    const items = (state.reservations.items || []).map(task => {
        const canPause = task.status === 'scheduled' || task.status === 'holding';
        const canResume = task.status === 'paused';
        const lastEvent = task.lastEvent ? `${task.lastEvent.message || ''}` : '暂无执行记录';
        return `
            <article class="list-card">
                <div class="card-title">
                    <div>
                        <h3>${escapeHtml(task.title)}</h3>
                        <p>${escapeHtml(task.machineName)} · ${escapeHtml(task.modeName)}</p>
                    </div>
                    <span class="chip ${reservationStatusClass(task.status)}">${escapeHtml(reservationStatusLabel(task.status))}</span>
                </div>
                <div class="detail-grid">
                    <div><span>预约类型</span><span>${escapeHtml(task.scheduleType === 'weekly' ? '每周固定时间' : '单次')}</span></div>
                    <div><span>目标时间</span><span>${escapeHtml(formatDateTime(task.targetTime))}</span></div>
                    <div><span>建单窗口</span><span>${escapeHtml(formatWindow(task.startAt, task.holdUntil))}</span></div>
                    <div><span>当前订单</span><span>${escapeHtml(task.activeOrderNo || '--')}</span></div>
                </div>
                ${task.lastError ? `<div class="spacer-sm"></div><div class="callout warning">${escapeHtml(task.lastError)}</div>` : ''}
                <div class="spacer-sm"></div>
                <div class="callout">${escapeHtml(lastEvent)}</div>
                <div class="spacer-sm"></div>
                <div class="action-row">
                    ${canPause ? `<button class="btn btn-light" data-action="pause-reservation" data-task-id="${task.id}">暂停</button>` : ''}
                    ${canResume ? `<button class="btn btn-secondary" data-action="resume-reservation" data-task-id="${task.id}">恢复</button>` : ''}
                    <button class="btn btn-danger" data-action="delete-reservation" data-task-id="${task.id}">删除</button>
                </div>
            </article>
        `;
    }).join('');

    el.reservationList.innerHTML = [
        schedulerCard,
        tokenCard,
        items || renderEmptyState('还没有预约任务', '你可以先在上方创建单次或每周预约。'),
    ].join('');
}

function renderOrders() {
    if (!isTokenReady()) {
        el.ordersList.innerHTML = renderStatusCard('danger', '订单页面暂不可用', getTokenAlertMessage());
        el.loadMoreOrdersBtn.classList.add('hidden');
        return;
    }

    if (!state.orders.items.length && state.orders.loading) {
        el.ordersList.innerHTML = '<div class="panel-card loading-card">正在加载订单...</div>';
        el.loadMoreOrdersBtn.classList.add('hidden');
        return;
    }

    if (!state.orders.items.length) {
        el.ordersList.innerHTML = renderEmptyState('暂无订单记录', '下拉刷新或稍后再试。');
        el.loadMoreOrdersBtn.classList.add('hidden');
        return;
    }

    const cards = state.orders.items.map(order => {
        const detail = cache.orderDetails.get(order.orderNo);
        const expanded = Boolean(state.orders.expanded[order.orderNo]);
        const detailSection = expanded ? renderOrderDetailSection(order, detail) : '';
        return `
            <article class="order-card">
                <div class="card-title">
                    <div>
                        <h3>${escapeHtml(order.machineName)}</h3>
                        <p>${escapeHtml(order.modeName)} · ${escapeHtml(order.orderNo)}</p>
                    </div>
                    <span class="chip ${orderChipClass(order.state)}">${escapeHtml(order.stateDesc || '未知状态')}</span>
                </div>
                <div class="detail-grid">
                    <div><span>金额</span><span>${escapeHtml(order.price || '--')} 元</span></div>
                    <div><span>创建时间</span><span>${escapeHtml(formatDateTime(order.createTime))}</span></div>
                    <div><span>完成时间</span><span>${escapeHtml(formatDateTime(order.completeTime))}</span></div>
                    <div><span>订单状态</span><span>${escapeHtml(order.stateDesc || '--')}</span></div>
                </div>
                <div class="spacer-sm"></div>
                <div class="action-row">
                    <button class="btn btn-light" data-action="toggle-order-detail" data-order-no="${escapeHtml(order.orderNo)}">${expanded ? '收起详情' : '查看详情'}</button>
                </div>
                ${detailSection}
            </article>
        `;
    }).join('');

    el.ordersList.innerHTML = cards;
    el.loadMoreOrdersBtn.classList.toggle('hidden', !state.orders.hasMore);
    el.loadMoreOrdersBtn.disabled = !state.orders.hasMore || state.orders.loading;
}

function renderOrderDetailSection(order, detail) {
    if (!detail) {
        return '<div class="spacer-sm"></div><div class="callout">正在加载订单详情...</div>';
    }
    return `
        <div class="spacer-sm"></div>
        <div class="callout">
            状态页：${escapeHtml(detail.pageCode || '--')}<br>
            支付时间：${escapeHtml(formatDateTime(detail.payTime))}<br>
            失效时间：${escapeHtml(formatDateTime(detail.invalidTime))}<br>
            洗衣房：${escapeHtml(detail.shopName || '--')}
        </div>
        <div class="spacer-sm"></div>
        <div class="action-row">
            ${detail.buttonSwitch && detail.buttonSwitch.canCloseOrder ? `<button class="btn btn-danger" data-action="finish-order" data-order-no="${escapeHtml(order.orderNo)}">结束订单</button>` : ''}
            ${detail.buttonSwitch && detail.buttonSwitch.canCancel ? `<button class="btn btn-light" data-action="cancel-order" data-order-no="${escapeHtml(order.orderNo)}">取消订单</button>` : ''}
        </div>
    `;
}

function renderSettings() {
    const settings = state.settings;
    const sourceSummary = settings && settings.sources
        ? `
            <div class="detail-grid">
                <div><span>Token 来源</span><span>${escapeHtml(sourceText(settings.sources.token))}</span></div>
                <div><span>PushPlus 来源</span><span>${escapeHtml(sourceText(settings.sources.pushplusUrl))}</span></div>
                <div><span>默认提前分钟</span><span>${escapeHtml(sourceText(settings.sources.defaultLeadMinutes))}</span></div>
                <div><span>HTTPS 校验</span><span>${state.security && state.security.sslVerify ? '开启' : '关闭'}</span></div>
            </div>
        `
        : '<div class="callout">正在读取设置...</div>';

    const schedulerInfo = renderSchedulerCard();
    el.settingsInfo.innerHTML = [
        renderStatusCard(tokenClassName(state.tokenStatus.reason), tokenLabel(), state.tokenStatus.message),
        `
            <section class="sheet-card">
                <h3 class="section-title">当前设置来源</h3>
                ${sourceSummary}
            </section>
        `,
        schedulerInfo,
        `
            <section class="sheet-card">
                <h3 class="section-title">运行说明</h3>
                <div class="callout">
                    预约调度会在目标时间前一段时间自动建单，并在订单失效时尝试补建。当前版本只对支持虚拟扫码的机器开放预约。
                </div>
            </section>
        `,
    ].join('');
}

function renderSchedulerCard() {
    const scheduler = state.scheduler || {};
    const lastResult = scheduler.lastResult || {};
    return `
        <section class="sheet-card">
            <div class="card-title">
                <div>
                    <h3>调度器状态</h3>
                    <p>后台轮询预约任务并维持待支付订单。</p>
                </div>
                <span class="chip ${scheduler.running ? 'success' : 'danger'}">${scheduler.running ? '运行中' : '未运行'}</span>
            </div>
            <div class="detail-grid">
                <div><span>轮询间隔</span><span>${escapeHtml(String(scheduler.intervalSeconds || '--'))} 秒</span></div>
                <div><span>最近轮询</span><span>${escapeHtml(formatDateTime(scheduler.lastTickAt))}</span></div>
                <div><span>最近创建</span><span>${escapeHtml(stringOrFallback(lastResult.created, '--'))}</span></div>
                <div><span>最近补建</span><span>${escapeHtml(stringOrFallback(lastResult.recreated, '--'))}</span></div>
            </div>
            ${scheduler.lastError ? `<div class="spacer-sm"></div><div class="callout danger">${escapeHtml(scheduler.lastError)}</div>` : ''}
        </section>
    `;
}

function renderStatusCard(variant, title, body, extra = '') {
    return `
        <section class="status-card ${escapeHtml(variant)}">
            <div class="status-title">
                <span>${escapeHtml(title)}</span>
                <span class="chip ${escapeHtml(variant)}">${escapeHtml(variantLabel(variant))}</span>
            </div>
            <p class="body-text">${escapeHtml(body)}</p>
            ${extra}
        </section>
    `;
}

function renderEmptyState(title, description) {
    return `
        <div class="panel-card empty-state">
            <strong>${escapeHtml(title)}</strong>
            <p>${escapeHtml(description)}</p>
        </div>
    `;
}

function handleWashClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) {
        return;
    }

    const action = button.dataset.action;
    if (action === 'goto-settings') {
        switchTab('settingsTab');
        return;
    }

    if (!ensureTokenReady()) {
        return;
    }

    if (action === 'back-home') {
        state.wash.view = 'home';
        state.wash.roomId = null;
        state.wash.roomData = null;
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.scanMachine = null;
        state.wash.result = null;
        renderWash();
        return;
    }

    if (action === 'back-room') {
        state.wash.view = 'room';
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.result = null;
        renderWash();
        return;
    }

    if (action === 'open-room') {
        openRoom(button.dataset.roomId).catch(err => handleRequestError(err, '加载洗衣房失败。'));
        return;
    }

    if (action === 'open-machine') {
        openMachine(button.dataset.goodsId).catch(err => handleRequestError(err, '加载机器详情失败。'));
        return;
    }

    if (action === 'open-scan') {
        openScanMachine(button.dataset.qrCode).catch(err => handleRequestError(err, '加载扫码机模式失败。'));
        return;
    }

    if (action === 'create-lock-order') {
        createLockOrder().catch(err => handleRequestError(err, '线上下单失败。'));
        return;
    }

    if (action === 'create-scan-order') {
        createScanOrder().catch(err => handleRequestError(err, '扫码下单失败。'));
    }
}

async function openRoom(roomId) {
    setWashLoading('正在加载洗衣房设备...');
    try {
        const payload = await getRoomMachines(roomId);
        state.wash.view = 'room';
        state.wash.roomId = roomId;
        state.wash.roomData = payload;
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.scanMachine = null;
        state.wash.result = null;
    } finally {
        state.wash.loading = false;
        renderWash();
    }
}

async function openMachine(goodsId) {
    setWashLoading('正在加载机器详情...');
    try {
        const detail = await getMachineDetail(goodsId);
        state.wash.view = 'machine';
        state.wash.machineId = goodsId;
        state.wash.machineDetail = detail;
        state.wash.scanMachine = null;
        state.wash.result = null;
    } finally {
        state.wash.loading = false;
        renderWash();
    }
}

async function openScanMachine(qrCode) {
    const machine = (state.wash.scanMachines || []).find(item => item.qrCode === qrCode);
    if (!machine) {
        throw new Error('未找到对应扫码机组。');
    }
    setWashLoading('正在读取扫码机可用模式...');
    try {
        const modes = await getScanModes(qrCode);
        state.wash.view = 'scan';
        state.wash.scanMachine = { ...machine, modes };
        state.wash.result = null;
    } finally {
        state.wash.loading = false;
        renderWash();
    }
}

async function createLockOrder() {
    const machine = state.wash.machineDetail;
    const modeId = getSelectedModeValue('washModeSelect');
    if (!machine || !modeId) {
        window.alert('请先选择模式。');
        return;
    }

    const data = await apiPost('/api/orders/create-by-lock', {
        goodsId: machine.goodsId,
        modeId,
    });

    const todo = data.result || {};
    state.wash.result = {
        variant: 'warning',
        title: '线上订单已创建',
        message: data.msg || todo.message || '订单已创建，后续支付链路待补齐。',
        order: todo.order || null,
        todo: todo.todo || null,
    };
    renderWash();
    showToastMessage('线上订单已创建，后续步骤保持 TODO。');
}

async function createScanOrder() {
    const scanMachine = state.wash.view === 'scan' ? state.wash.scanMachine : null;
    const machine = state.wash.view === 'machine' ? state.wash.machineDetail : null;
    const qrCode = scanMachine ? scanMachine.qrCode : machine ? machine.scanCode : '';
    const selectId = scanMachine ? 'scanModeSelect' : 'washModeSelect';
    const modeId = getSelectedModeValue(selectId);
    if (!qrCode || !modeId) {
        window.alert('请先选择模式。');
        return;
    }

    const data = await apiPost('/api/orders/create-by-scan', {
        qrCode,
        modeId,
    });

    state.wash.result = {
        variant: 'success',
        title: '扫码下单已完成',
        message: data.msg || '订单创建和支付流程已完成。',
        order: data.order || null,
        todo: null,
    };
    renderWash();
    showToastMessage('扫码下单流程已完成。');
}

async function handleReservationSubmit(event) {
    event.preventDefault();

    if (!ensureTokenReady()) {
        return;
    }

    const source = el.reservationSource.value;
    const modeId = el.reservationMode.value;
    const mode = (state.reservations.modeOptions || []).find(item => String(item.id) === String(modeId));
    if (!modeId || !mode) {
        window.alert('请先选择预约模式。');
        return;
    }

    const payload = {
        title: el.reservationTitle.value.trim(),
        machineSource: source,
        modeId: Number(modeId),
        modeName: mode.label,
        leadMinutes: Number(el.reservationLeadMinutes.value || getDefaultLeadMinutes()),
        scheduleType: el.reservationScheduleType.value,
    };

    if (payload.scheduleType === 'once') {
        payload.targetTime = buildIsoFromLocalInput(el.reservationTargetTime.value);
    } else {
        payload.weekday = Number(el.reservationWeekday.value);
        payload.timeOfDay = el.reservationWeeklyTime.value;
    }

    if (source === 'scan') {
        const qrCode = el.reservationScanMachine.value;
        const machine = (state.wash.scanMachines || []).find(item => item.qrCode === qrCode);
        if (!qrCode || !machine) {
            window.alert('请先选择扫码机组。');
            return;
        }
        payload.machineId = qrCode;
        payload.machineName = machine.label;
        payload.qrCode = qrCode;
    } else {
        const roomId = el.reservationRoom.value;
        const goodsId = el.reservationMachine.value;
        const room = (state.wash.rooms || []).find(item => item.id === roomId);
        const machine = (state.reservations.roomMachines || []).find(item => item.goodsId === goodsId);
        if (!room || !machine) {
            window.alert('请先选择洗衣房和洗衣机。');
            return;
        }
        if (!machine.supportsVirtualScan || !machine.scanCode) {
            window.alert('当前预约仅支持可虚拟扫码的机器。');
            return;
        }
        payload.machineId = machine.goodsId;
        payload.machineName = machine.name;
        payload.roomId = room.id;
        payload.roomName = room.name;
        payload.qrCode = machine.scanCode;
    }

    const data = await apiPost('/api/reservations', payload);
    showToastMessage(data.msg || '预约任务已创建。');
    el.reservationTitle.value = '';
    await loadReservations();
}

async function handleReservationClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) {
        return;
    }

    const taskId = button.dataset.taskId;
    if (!taskId) {
        return;
    }

    if (button.dataset.action === 'pause-reservation') {
        const data = await apiPost(`/api/reservations/${taskId}/pause`, {});
        showToastMessage(data.msg || '预约任务已暂停。');
        await loadReservations();
        return;
    }

    if (button.dataset.action === 'resume-reservation') {
        const data = await apiPost(`/api/reservations/${taskId}/resume`, {});
        showToastMessage(data.msg || '预约任务已恢复。');
        await loadReservations();
        return;
    }

    if (button.dataset.action === 'delete-reservation') {
        if (!window.confirm('确定删除这个预约任务吗？')) {
            return;
        }
        const data = await apiDelete(`/api/reservations/${taskId}`);
        showToastMessage(data.msg || '预约任务已删除。');
        await loadReservations();
    }
}

async function handleOrderClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) {
        return;
    }

    if (!ensureTokenReady()) {
        return;
    }

    const orderNo = button.dataset.orderNo;
    if (!orderNo) {
        return;
    }

    if (button.dataset.action === 'toggle-order-detail') {
        const expanded = Boolean(state.orders.expanded[orderNo]);
        state.orders.expanded[orderNo] = !expanded;
        renderOrders();
        if (!expanded) {
            try {
                const detail = await getOrderDetail(orderNo);
                updateOrderFromDetail(orderNo, detail);
                renderOrders();
            } catch (err) {
                handleRequestError(err, '读取订单详情失败。', true);
            }
        }
        return;
    }

    if (button.dataset.action === 'finish-order') {
        if (!window.confirm(`确定结束订单 ${orderNo} 吗？`)) {
            return;
        }
        const data = await apiPost(`/api/orders/${encodeURIComponent(orderNo)}/finish`, {});
        showToastMessage(data.msg || '订单已结束。');
        await refreshSingleOrder(orderNo);
        return;
    }

    if (button.dataset.action === 'cancel-order') {
        if (!window.confirm(`确定取消订单 ${orderNo} 吗？`)) {
            return;
        }
        const data = await apiPost(`/api/orders/${encodeURIComponent(orderNo)}/cancel`, {});
        showToastMessage(data.msg || '订单已取消。');
        await refreshSingleOrder(orderNo);
    }
}

async function refreshSingleOrder(orderNo) {
    try {
        const detail = await getOrderDetail(orderNo, true);
        updateOrderFromDetail(orderNo, detail);
    } catch (err) {
        handleRequestError(err, '刷新订单详情失败。', true);
    }
    await loadOrders(true);
}

function updateOrderFromDetail(orderNo, detail) {
    const item = state.orders.items.find(order => order.orderNo === orderNo);
    if (!item || !detail) {
        return;
    }
    item.state = detail.state;
    item.stateDesc = detail.stateDesc;
    item.price = detail.price;
    item.completeTime = detail.completeTime;
}

async function handleSettingsSubmit(event) {
    event.preventDefault();

    const payload = {
        token: el.settingsToken.value.trim(),
        pushplusUrl: el.settingsPushplusUrl.value.trim(),
        defaultLeadMinutes: Number(el.settingsLeadMinutes.value || 60),
    };

    try {
        const data = await apiPut('/api/settings', payload);
        state.settings = data.settings || state.settings;
        state.scheduler = data.scheduler || state.scheduler;
        applyTokenStatus(data.tokenStatus || state.tokenStatus, true);
        renderSettings();
        showToastMessage(data.msg || '设置已保存。');

        if (isTokenReady()) {
            await loadLaundrySections();
            await hydrateReservationForm();
            if (state.activeTab === 'orderTab') {
                await loadOrders(true);
            }
        } else {
            renderWash();
            renderOrders();
        }
    } catch (err) {
        handleRequestError(err, '保存设置失败。', true);
    }
}

function showToastMessage(message) {
    clearTimeout(state.ui.toastTimer);
    el.toast.textContent = message;
    el.toast.classList.add('show');
    state.ui.toastTimer = window.setTimeout(() => {
        el.toast.classList.remove('show');
    }, 2600);
}

function fillSelect(select, items, placeholder) {
    const options = [`<option value="">${escapeHtml(placeholder)}</option>`]
        .concat((items || []).map(item => `<option value="${escapeHtml(String(item.value))}">${escapeHtml(item.label)}</option>`));
    select.innerHTML = options.join('');
}

function getSelectedModeValue(selectId) {
    const select = document.getElementById(selectId);
    return select ? select.value : '';
}

function reservationStatusClass(status) {
    if (status === 'holding') {
        return 'success';
    }
    if (status === 'paused') {
        return 'warning';
    }
    if (status === 'failed' || status === 'deleted') {
        return 'danger';
    }
    return 'pending';
}

function reservationStatusLabel(status) {
    if (status === 'scheduled') {
        return '未开始';
    }
    if (status === 'holding') {
        return '保单中';
    }
    if (status === 'paused') {
        return '已暂停';
    }
    if (status === 'completed') {
        return '已完成';
    }
    if (status === 'failed') {
        return '失败';
    }
    return status || '未知';
}

function orderChipClass(stateCode) {
    if (stateCode === 1000 || stateCode === 500) {
        return 'success';
    }
    if (stateCode === 401 || stateCode === 411) {
        return 'danger';
    }
    if (stateCode === 50) {
        return 'warning';
    }
    return 'pending';
}

function variantLabel(variant) {
    if (variant === 'success') {
        return '正常';
    }
    if (variant === 'warning') {
        return '提醒';
    }
    if (variant === 'danger') {
        return '注意';
    }
    return '信息';
}

function sourceText(source) {
    return source === 'database' ? '服务端数据库' : '.env 默认值';
}

function getDefaultLeadMinutes() {
    return state.settings && state.settings.defaultLeadMinutes ? state.settings.defaultLeadMinutes : 60;
}

function stringOrFallback(value, fallback) {
    return String(value == null ? fallback : value);
}

function formatWindow(startAt, holdUntil) {
    if (!startAt && !holdUntil) {
        return '--';
    }
    return `${formatDateTime(startAt)} - ${formatDateTime(holdUntil)}`;
}

function formatDateTime(value) {
    if (!value) {
        return '--';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return `${date.getMonth() + 1}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toLocalInputValue(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function buildIsoFromLocalInput(value) {
    if (!value) {
        return '';
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

function pad(value) {
    return String(value).padStart(2, '0');
}

function maskCode(value) {
    if (!value) {
        return '--';
    }
    if (value.length <= 6) {
        return value;
    }
    return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function syncTokenFailure(error) {
    const payload = extractErrorPayload(error);
    if (!payload || !payload.errorType) {
        return;
    }

    const mapping = {
        token_missing: { configured: false, valid: false, reason: 'missing' },
        token_invalid: { configured: true, valid: false, reason: 'invalid' },
        token_check_failed: { configured: true, valid: false, reason: 'check_failed' },
    };
    const next = mapping[payload.errorType];
    if (!next) {
        return;
    }

    applyTokenStatus({
        source: 'database',
        configured: next.configured,
        valid: next.valid,
        reason: next.reason,
        message: payload.msg || getTokenAlertMessage(),
    }, true);
}

function handleRequestError(error, fallbackMessage, silent = false) {
    const payload = extractErrorPayload(error);
    const message = payload && payload.msg ? payload.msg : error.message || fallbackMessage;
    if (!silent) {
        showToastMessage(message || fallbackMessage);
    }
    console.error(error);
}

function extractErrorPayload(error) {
    if (error && error.payload) {
        return error.payload;
    }
    return null;
}

async function apiGet(url) {
    return request(url, { method: 'GET' });
}

async function apiPost(url, body) {
    return request(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
}

async function apiPut(url, body) {
    return request(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
}

async function apiDelete(url) {
    return request(url, { method: 'DELETE' });
}

async function request(url, options) {
    const response = await fetch(url, {
        headers: {
            Accept: 'application/json',
            ...(options && options.headers ? options.headers : {}),
        },
        ...options,
    });

    let data = null;
    try {
        data = await response.json();
    } catch (err) {
        const parseError = new Error(`响应解析失败: ${err.message}`);
        parseError.payload = null;
        throw parseError;
    }

    if (!response.ok) {
        const requestError = new Error(data && data.msg ? data.msg : `HTTP ${response.status}`);
        requestError.payload = data;
        throw requestError;
    }

    if (data && data.status && data.status !== 'success' && data.status !== 'todo') {
        const businessError = new Error(data.msg || '请求失败');
        businessError.payload = data;
        throw businessError;
    }

    return data;
}
