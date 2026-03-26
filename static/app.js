const TAB_TITLES = {
    washTab: '洗衣',
    reservationTab: '预约',
    orderTab: '订单',
    settingsTab: '设置',
};

const PROCESS_STEPS = [
    { id: 1, label: '解析机器', hint: '确认当前二维码对应的设备信息' },
    { id: 2, label: '创建订单', hint: '创建待支付订单，订单页可继续流程' },
    { id: 3, label: '确认放衣', hint: '放入衣物并确认关门' },
    { id: 4, label: '生成预支付', hint: '准备结算单和支付参数' },
    { id: 5, label: '支付启动', hint: '执行支付并启动设备' },
];

const DEFAULT_TOKEN_STATUS = {
    source: 'env',
    configured: false,
    valid: false,
    reason: 'missing',
    message: '当前未配置可用 Token，请前往设置页处理。',
};

const VALID_TABS = new Set(Object.keys(TAB_TITLES));
const AUTO_REFRESHABLE_TABS = new Set(['washTab', 'orderTab']);
const AUTO_REFRESH_INTERVAL_MS = 30 * 1000;

const state = {
    activeTab: 'washTab',
    tokenStatus: { ...DEFAULT_TOKEN_STATUS },
    security: null,
    scheduler: null,
    settings: null,
    bootstrap: {
        configLoading: true,
        settingsLoading: true,
        reservationsLoading: true,
    },
    refresh: {
        washTab: { lastRefreshedAt: null, loading: false },
        reservationTab: { lastRefreshedAt: null, loading: false },
        orderTab: { lastRefreshedAt: null, loading: false },
        settingsTab: { lastRefreshedAt: null, loading: false },
    },
    historyReady: false,
    historyRestoring: false,
    wash: {
        loading: true,
        rooms: [],
        scanMachines: [],
        view: 'home',
        roomId: null,
        roomData: null,
        machineId: null,
        machineDetail: null,
        scanMachine: null,
        process: null,
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
        activeProcesses: [],
    },
    ui: {
        toastTimer: null,
        tokenAlertReason: null,
        dialogResolver: null,
        reservationRefreshTimer: null,
        reservationRefreshInFlight: false,
        liveClockTimer: null,
        ordersOverviewRefreshPromise: null,
        activeRefreshPromise: null,
        autoRefreshTimer: null,
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
    state.activeTab = getTabFromLocation();
    bindElements();
    bindEvents();
    initOrderObserver();
    startLiveClock();
    primeInputs();
    renderWash();
    renderReservations();
    renderOrders();
    renderSettings();
    renderGlobalRefresh();
    switchTab(state.activeTab, { pushHistory: false });

    try {
        await loadConfig();
        await loadSettings();
        await loadReservations();
        if (isTokenReady()) {
            await loadLaundrySections({ showToast: false, preserveView: false });
            await hydrateReservationForm();
        } else {
            renderWash();
            renderOrders();
        }
    } catch (error) {
        handleRequestError(error, '初始化失败，请检查服务端日志。');
    } finally {
        replaceHistoryState();
        state.historyReady = true;
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
    }
});

function bindElements() {
    el.pageTitle = document.getElementById('pageTitle');
    el.topStatus = document.getElementById('topStatus');
    el.tabPanels = Array.from(document.querySelectorAll('.tab-panel'));
    el.tabButtons = Array.from(document.querySelectorAll('.tabbar-btn'));
    el.toast = document.getElementById('toast');
    el.appDialog = document.getElementById('appDialog');
    el.appDialogTitle = document.getElementById('appDialogTitle');
    el.appDialogMessage = document.getElementById('appDialogMessage');
    el.appDialogCancel = document.getElementById('appDialogCancel');
    el.appDialogConfirm = document.getElementById('appDialogConfirm');

    el.washView = document.getElementById('washView');

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

    el.ordersList = document.getElementById('ordersList');
    el.ordersSentinel = document.getElementById('ordersSentinel');
    el.loadMoreOrdersBtn = document.getElementById('loadMoreOrdersBtn');

    el.settingsForm = document.getElementById('settingsForm');
    el.settingsToken = document.getElementById('settingsToken');
    el.settingsPushplusUrl = document.getElementById('settingsPushplusUrl');
    el.settingsLeadMinutes = document.getElementById('settingsLeadMinutes');
    el.settingsPollInterval = document.getElementById('settingsPollInterval');
    el.settingsInfo = document.getElementById('settingsInfo');
    el.globalRefreshBtn = document.getElementById('globalRefreshBtn');
    el.globalRefreshLabel = document.getElementById('globalRefreshLabel');
    el.globalRefreshMeta = document.getElementById('globalRefreshMeta');
}

function bindEvents() {
    el.tabButtons.forEach(button => {
        button.addEventListener('click', () => switchTab(button.dataset.tab));
    });

    el.globalRefreshBtn.addEventListener('click', () => {
        refreshActiveTab({ reason: 'manual', silent: false, force: true }).catch(error => {
            handleRequestError(error, '刷新页面失败。', true);
        });
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

    el.appDialogConfirm.addEventListener('click', () => {
        closeAppDialog(true);
    });
    el.appDialogCancel.addEventListener('click', () => {
        closeAppDialog(false);
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

    window.addEventListener('popstate', event => {
        const nextState = event.state || buildHistoryState();
        restoreHistoryState(nextState).catch(error => {
            handleRequestError(error, '页面恢复失败，已保留当前页面。', true);
        });
    });

    document.addEventListener('visibilitychange', () => {
        renderGlobalRefresh();
        if (document.visibilityState === 'visible') {
            if (state.historyReady && AUTO_REFRESHABLE_TABS.has(state.activeTab)) {
                refreshActiveTab({ reason: 'resume', silent: true, force: true }).catch(error => {
                    handleRequestError(error, '自动刷新失败。', true);
                });
            } else if (state.activeTab === 'reservationTab') {
                restartReservationAutoRefresh();
            } else {
                scheduleActiveAutoRefresh();
            }
            return;
        }
        clearActiveAutoRefresh();
        clearReservationAutoRefresh();
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

function getReservationRefreshDelayMs() {
    const intervalSeconds = Number((state.scheduler || {}).intervalSeconds || 30);
    if (!Number.isFinite(intervalSeconds) || intervalSeconds < 5) {
        return 30 * 1000;
    }
    return intervalSeconds * 1000;
}

function clearReservationAutoRefresh() {
    if (state.ui.reservationRefreshTimer) {
        window.clearTimeout(state.ui.reservationRefreshTimer);
        state.ui.reservationRefreshTimer = null;
    }
}

function restartReservationAutoRefresh() {
    clearReservationAutoRefresh();
    if (document.visibilityState !== 'visible' || state.activeTab !== 'reservationTab') {
        return;
    }
    state.ui.reservationRefreshTimer = window.setTimeout(async () => {
        state.ui.reservationRefreshTimer = null;
        if (document.visibilityState !== 'visible' || state.activeTab !== 'reservationTab' || state.ui.reservationRefreshInFlight) {
            restartReservationAutoRefresh();
            return;
        }
        await loadReservations({ silent: true });
    }, getReservationRefreshDelayMs());
}

function getActiveRefreshState() {
    return state.refresh[state.activeTab] || { lastRefreshedAt: null, loading: false };
}

function setTabRefreshing(tabId, loading) {
    if (!state.refresh[tabId]) {
        return;
    }
    state.refresh[tabId].loading = loading;
    renderGlobalRefresh();
}

function markTabRefreshed(tabId, refreshedAt = Date.now()) {
    if (!state.refresh[tabId]) {
        return;
    }
    state.refresh[tabId].lastRefreshedAt = refreshedAt;
    renderGlobalRefresh();
}

function formatRefreshTimestamp(value) {
    if (!value) {
        return '尚未刷新';
    }
    const date = parseDateValue(value);
    if (!date) {
        return '尚未刷新';
    }
    return `${pad(date.getHours())}:${pad(date.getMinutes())} 更新`;
}

function refreshMetaText(tabId) {
    const lastUpdatedText = formatRefreshTimestamp((state.refresh[tabId] || {}).lastRefreshedAt);
    if (tabId === 'washTab') {
        if (state.wash.view === 'scan') {
            return `${lastUpdatedText} · 扫码流程仅手动刷新`;
        }
        return `${lastUpdatedText} · 可见时自动刷新`;
    }
    if (tabId === 'orderTab') {
        return `${lastUpdatedText} · 可见时自动刷新`;
    }
    return `${lastUpdatedText} · 当前页支持手动刷新`;
}

function renderGlobalRefresh() {
    if (!el.globalRefreshBtn) {
        return;
    }
    const refreshState = getActiveRefreshState();
    const tabLabel = TAB_TITLES[state.activeTab] || '当前页';
    el.globalRefreshLabel.textContent = refreshState.loading ? `正在刷新${tabLabel}` : `刷新${tabLabel}`;
    el.globalRefreshMeta.textContent = refreshMetaText(state.activeTab);
    el.globalRefreshBtn.disabled = Boolean(refreshState.loading);
}

function clearActiveAutoRefresh() {
    if (!state.ui.autoRefreshTimer) {
        return;
    }
    window.clearTimeout(state.ui.autoRefreshTimer);
    state.ui.autoRefreshTimer = null;
}

function canAutoRefreshActiveTab() {
    if (!state.historyReady || document.visibilityState !== 'visible') {
        return false;
    }
    if (!AUTO_REFRESHABLE_TABS.has(state.activeTab) || !isTokenReady()) {
        return false;
    }
    if (state.activeTab === 'washTab' && state.wash.view === 'scan') {
        return false;
    }
    return true;
}

function scheduleActiveAutoRefresh() {
    clearActiveAutoRefresh();
    if (!canAutoRefreshActiveTab()) {
        return;
    }
    state.ui.autoRefreshTimer = window.setTimeout(async () => {
        state.ui.autoRefreshTimer = null;
        try {
            await refreshActiveTab({ reason: 'auto', silent: true });
        } catch (error) {
            handleRequestError(error, '自动刷新失败。', true);
        }
    }, AUTO_REFRESH_INTERVAL_MS);
}

function restoreSelectValue(id, value) {
    if (!value) {
        return;
    }
    const select = document.getElementById(id);
    if (select) {
        select.value = value;
    }
}

async function refreshWashCurrentView(reason = 'manual') {
    const currentView = state.wash.view || 'home';
    if (currentView === 'room' && state.wash.roomId) {
        state.wash.roomData = await getRoomMachines(state.wash.roomId, { forceRefresh: true });
        renderWash();
        return;
    }
    if (currentView === 'machine' && state.wash.machineId) {
        const selectedMode = getSelectedValue('washModeSelect');
        const machineDetail = await getMachineDetail(state.wash.machineId, { forceRefresh: true });
        state.wash.machineDetail = machineDetail;
        if (state.wash.roomId) {
            state.wash.roomData = await getRoomMachines(state.wash.roomId, { forceRefresh: true });
        }
        renderWash();
        restoreSelectValue('washModeSelect', selectedMode);
        return;
    }
    if (currentView === 'process' && state.wash.process && state.wash.process.processId) {
        await openProcess(state.wash.process.processId, { pushHistory: false });
        return;
    }
    if (currentView === 'scan' && state.wash.scanMachine && state.wash.scanMachine.qrCode) {
        const selectedMode = getSelectedValue('scanModeSelect');
        const localMachine = (state.wash.scanMachines || []).find(item => item.qrCode === state.wash.scanMachine.qrCode);
        state.wash.scanMachine = {
            ...(localMachine || state.wash.scanMachine),
            modes: await getScanModes(state.wash.scanMachine.qrCode, { forceRefresh: true }),
        };
        renderWash();
        restoreSelectValue('scanModeSelect', selectedMode);
        return;
    }

    await loadLaundrySections({ showToast: false, preserveView: reason !== 'manual' });
    await hydrateReservationForm();
}

async function refreshActiveTab({ reason = 'manual', silent = false, force = false } = {}) {
    if (state.ui.activeRefreshPromise) {
        return state.ui.activeRefreshPromise;
    }

    const tabId = state.activeTab;
    const tokenRequired = tabId === 'washTab' || tabId === 'orderTab';
    if (tokenRequired && !isTokenReady()) {
        if (!silent) {
            ensureTokenReady();
        }
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
        return;
    }
    if (reason === 'auto' && !canAutoRefreshActiveTab()) {
        return;
    }

    let refreshPromise = null;
    setTabRefreshing(tabId, true);
    refreshPromise = (async () => {
        try {
            let didRefresh = true;
            if (tabId === 'washTab') {
                await refreshWashCurrentView(reason);
            } else if (tabId === 'reservationTab') {
                await loadReservations({ silent: silent || reason !== 'manual' });
            } else if (tabId === 'orderTab') {
                const preserveItems = reason !== 'manual' || Boolean(state.orders.items.length);
                didRefresh = await refreshOrdersOverview(true, { silent, preserveItems });
            } else if (tabId === 'settingsTab') {
                await loadSettings();
            }

            if (didRefresh !== false) {
                markTabRefreshed(tabId);
            }
            if (didRefresh !== false && !silent && reason === 'manual') {
                showToastMessage(`${TAB_TITLES[tabId] || '当前页'}已刷新。`);
            }
        } catch (error) {
            syncTokenFailure(error);
            if (!silent) {
                handleRequestError(error, `${TAB_TITLES[tabId] || '当前页'}刷新失败。`);
            }
            throw error;
        } finally {
            if (state.ui.activeRefreshPromise === refreshPromise) {
                state.ui.activeRefreshPromise = null;
            }
            setTabRefreshing(tabId, false);
            scheduleActiveAutoRefresh();
        }
    })();
    state.ui.activeRefreshPromise = refreshPromise;
    return refreshPromise;
}

function startLiveClock() {
    if (state.ui.liveClockTimer) {
        return;
    }
    state.ui.liveClockTimer = window.setInterval(() => {
        if (state.activeTab === 'washTab' && state.wash.view === 'process' && state.wash.process) {
            renderWash();
        }
        if (state.activeTab === 'reservationTab' && state.reservations.items.length) {
            renderReservations();
        }
        if (state.activeTab === 'orderTab' && (state.orders.activeProcesses.length || state.orders.items.length)) {
            renderOrders();
        }
        renderGlobalRefresh();
    }, 1000);
}

function buildHistoryState() {
    const payload = { tab: state.activeTab };
    if (state.activeTab !== 'washTab') {
        return payload;
    }

    payload.washView = state.wash.view;
    if (state.wash.roomId) {
        payload.roomId = state.wash.roomId;
    }
    if (state.wash.machineId) {
        payload.machineId = state.wash.machineId;
    }
    if (state.wash.scanMachine && state.wash.scanMachine.qrCode) {
        payload.qrCode = state.wash.scanMachine.qrCode;
    }
    if (state.wash.process && state.wash.process.processId) {
        payload.processId = state.wash.process.processId;
    }
    return payload;
}

function getTabFromLocation() {
    const hash = (window.location.hash || '').replace(/^#/, '').trim();
    if (VALID_TABS.has(hash)) {
        return hash;
    }
    return 'washTab';
}

function buildHistoryUrl(tabId) {
    const safeTab = VALID_TABS.has(tabId) ? tabId : 'washTab';
    return `${window.location.pathname}${window.location.search}#${safeTab}`;
}

function pushHistoryState() {
    if (!state.historyReady || state.historyRestoring) {
        return;
    }
    const payload = buildHistoryState();
    window.history.pushState(payload, '', buildHistoryUrl(payload.tab));
}

function replaceHistoryState() {
    const payload = buildHistoryState();
    window.history.replaceState(payload, '', buildHistoryUrl(payload.tab));
}

async function restoreHistoryState(navState) {
    state.historyRestoring = true;
    try {
        const tab = navState && navState.tab ? navState.tab : getTabFromLocation();
        switchTab(tab, { pushHistory: false, refresh: false });

        if (tab !== 'washTab') {
            return;
        }

        const washView = navState && navState.washView ? navState.washView : 'home';
        if (!isTokenReady()) {
            state.wash.view = 'home';
            renderWash();
            return;
        }

        if (washView === 'home') {
            state.wash.view = 'home';
            renderWash();
            return;
        }
        if (washView === 'room' && navState.roomId) {
            await openRoom(navState.roomId, { pushHistory: false });
            return;
        }
        if (washView === 'machine' && navState.machineId) {
            await openMachine(navState.machineId, { pushHistory: false, roomId: navState.roomId || null });
            return;
        }
        if (washView === 'scan' && navState.qrCode) {
            await openScanMachine(navState.qrCode, { pushHistory: false });
            return;
        }
        if (washView === 'process' && navState.processId) {
            await openProcess(navState.processId, { pushHistory: false });
            return;
        }

        state.wash.view = 'home';
        renderWash();
    } finally {
        state.historyRestoring = false;
    }
}

function switchTab(tabId, options = {}) {
    const safeTab = VALID_TABS.has(tabId) ? tabId : 'washTab';
    const previousTab = state.activeTab;
    state.activeTab = safeTab;
    el.tabPanels.forEach(panel => {
        panel.classList.toggle('active', panel.id === safeTab);
    });
    el.tabButtons.forEach(button => {
        button.classList.toggle('active', button.dataset.tab === safeTab);
    });
    el.pageTitle.textContent = TAB_TITLES[safeTab] || '海乐洗衣助手';
    renderGlobalRefresh();

    if (options.pushHistory !== false) {
        pushHistoryState();
    }

    if (safeTab === 'reservationTab') {
        loadReservations({ silent: true });
    } else {
        clearReservationAutoRefresh();
    }

    if (state.historyReady && options.refresh !== false && previousTab !== safeTab && AUTO_REFRESHABLE_TABS.has(safeTab)) {
        refreshActiveTab({ reason: 'tab-switch', silent: true, force: true }).catch(error => {
            handleRequestError(error, '刷新页面失败。', true);
        });
    }

    scheduleActiveAutoRefresh();
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

function applyTokenStatus(tokenStatus, notify = false) {
    state.tokenStatus = normalizeTokenStatus(tokenStatus);
    el.topStatus.className = `status-pill ${tokenClassName(state.tokenStatus.reason)}`;
    el.topStatus.textContent = tokenLabel();
    updateFormAvailability();

    if (notify && !isTokenReady()) {
        notifyTokenProblem();
    }
}

function isTokenReady() {
    return state.tokenStatus.valid && state.tokenStatus.reason === 'ok';
}

function getTokenAlertMessage() {
    if (state.tokenStatus.reason === 'missing') {
        return state.tokenStatus.message || '当前没有可用 Token，请到设置页填写。';
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

function notifyTokenProblem(force = false) {
    const reason = state.tokenStatus.reason;
    if (!force && state.ui.tokenAlertReason === reason) {
        return;
    }
    state.ui.tokenAlertReason = reason;
    showAlertDialog(getTokenAlertMessage());
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
    const disableTokenRequired = !isTokenReady();
    Array.from(el.reservationForm.elements).forEach(field => {
        field.disabled = disableTokenRequired;
    });
    el.loadMoreOrdersBtn.disabled = disableTokenRequired || !state.orders.hasMore;
    renderGlobalRefresh();
}

function openAppDialog({ title = '提示', message = '', confirmText = '确定', cancelText = '取消', showCancel = false }) {
    if (state.ui.dialogResolver) {
        state.ui.dialogResolver(false);
        state.ui.dialogResolver = null;
    }

    el.appDialogTitle.textContent = title;
    el.appDialogMessage.textContent = message;
    el.appDialogConfirm.textContent = confirmText;
    el.appDialogCancel.textContent = cancelText;
    el.appDialogCancel.classList.toggle('hidden', !showCancel);
    el.appDialog.classList.remove('hidden');
    el.appDialog.setAttribute('aria-hidden', 'false');

    return new Promise(resolve => {
        state.ui.dialogResolver = resolve;
    });
}

function closeAppDialog(result) {
    const resolver = state.ui.dialogResolver;
    state.ui.dialogResolver = null;
    el.appDialog.classList.add('hidden');
    el.appDialog.setAttribute('aria-hidden', 'true');
    if (resolver) {
        resolver(result);
    }
}

function showAlertDialog(message, title = '提示') {
    return openAppDialog({ title, message, confirmText: '确定', showCancel: false });
}

function showConfirmDialog(message, title = '请确认') {
    return openAppDialog({ title, message, confirmText: '确定', cancelText: '取消', showCancel: true });
}

async function loadConfig() {
    let loaded = false;
    try {
        const data = await apiGet('/api/config');
        state.security = data.security || null;
        state.scheduler = data.scheduler || null;
        state.wash.scanMachines = data.scanMachines || [];
        applyTokenStatus(data.tokenStatus || DEFAULT_TOKEN_STATUS, !((data.tokenStatus || {}).valid));
        loaded = true;
    } finally {
        state.bootstrap.configLoading = false;
        if (!isTokenReady()) {
            state.wash.loading = false;
        }
        if (loaded) {
            markTabRefreshed('washTab');
        }
        renderWash();
        renderReservations();
        renderOrders();
        renderGlobalRefresh();
        restartReservationAutoRefresh();
    }
}

async function loadSettings() {
    let loaded = false;
    try {
        const data = await apiGet('/api/settings');
        state.settings = data.settings || null;
        state.scheduler = data.scheduler || state.scheduler;
        applyTokenStatus(data.tokenStatus || state.tokenStatus);

        if (state.settings) {
            el.settingsToken.value = state.settings.token || '';
            el.settingsPushplusUrl.value = state.settings.pushplusUrl || '';
            el.settingsLeadMinutes.value = state.settings.defaultLeadMinutes || 60;
            el.settingsPollInterval.value = state.settings.reservationPollIntervalSeconds || 30;
            el.reservationLeadMinutes.value = state.settings.defaultLeadMinutes || 60;
        }
        loaded = true;
    } finally {
        state.bootstrap.settingsLoading = false;
        if (loaded) {
            markTabRefreshed('settingsTab');
        }
        renderSettings();
        renderGlobalRefresh();
        restartReservationAutoRefresh();
    }
}

async function loadLaundrySections({ showToast = false, preserveView = false } = {}) {
    state.wash.loading = true;
    if (!preserveView) {
        state.wash.view = 'home';
    }
    renderWashLoading('正在加载洗衣房和扫码机组...');

    try {
        const data = await apiGet('/api/laundry/sections');
        state.wash.rooms = data.rooms || [];
        state.wash.scanMachines = data.scanMachines || state.wash.scanMachines;
        state.wash.loading = false;
        if (!preserveView) {
            clearWashTransientState();
            state.wash.view = 'home';
        }
        markTabRefreshed('washTab');
        renderWash();
        if (showToast) {
            showToastMessage('洗衣页面已刷新。');
        }
    } catch (error) {
        syncTokenFailure(error);
        state.wash.loading = false;
        renderWash();
        throw error;
    }
}

async function loadReservations(options = {}) {
    const { silent = false, rethrow = false } = options;
    if (state.ui.reservationRefreshInFlight) {
        return;
    }

    state.ui.reservationRefreshInFlight = true;
    let loaded = false;
    try {
        const data = await apiGet('/api/reservations');
        state.reservations.items = data.items || [];
        state.scheduler = data.scheduler || state.scheduler;
        loaded = true;
    } catch (error) {
        handleRequestError(error, '读取预约和调度器状态失败。', silent);
        if (rethrow) {
            throw error;
        }
    } finally {
        state.ui.reservationRefreshInFlight = false;
        state.bootstrap.reservationsLoading = false;
        if (loaded) {
            markTabRefreshed('reservationTab');
        }
        renderReservations();
        renderGlobalRefresh();
        restartReservationAutoRefresh();
    }
}

async function loadActiveProcesses() {
    if (!isTokenReady()) {
        state.orders.activeProcesses = [];
        renderOrders();
        return false;
    }
    try {
        const data = await apiGet('/api/processes/active');
        state.orders.activeProcesses = data.items || [];
        renderOrders();
        return true;
    } catch (error) {
        syncTokenFailure(error);
        handleRequestError(error, '读取待继续流程失败。', true);
        return false;
    }
}

async function refreshOrdersOverview(reset, options = {}) {
    const { silent = false, preserveItems = false } = options;
    if (!isTokenReady()) {
        state.orders.activeProcesses = [];
        renderOrders();
        return;
    }
    if (state.ui.ordersOverviewRefreshPromise) {
        return state.ui.ordersOverviewRefreshPromise;
    }

    let refreshPromise = null;
    refreshPromise = (async () => {
        try {
            const [ordersLoaded, processesLoaded] = await Promise.all([
                preserveItems && reset ? refreshOrdersSnapshot() : loadOrders(reset, { preserveItems }),
                loadActiveProcesses(),
            ]);
            if (ordersLoaded || processesLoaded) {
                markTabRefreshed('orderTab');
                return true;
            }
            return false;
        } catch (error) {
            if (!silent) {
                handleRequestError(error, '读取订单失败。');
            }
            return false;
        } finally {
            if (state.ui.ordersOverviewRefreshPromise === refreshPromise) {
                state.ui.ordersOverviewRefreshPromise = null;
            }
        }
    })();
    state.ui.ordersOverviewRefreshPromise = refreshPromise;
    return refreshPromise;
}

function isTerminalHistoryOrder(order) {
    if (!order) {
        return false;
    }
    const state = Number(order.state || 0);
    const stateDesc = String(order.stateDesc || '');
    if (state === 411 || state === 1000) {
        return true;
    }
    if (state === 401) {
        return stateDesc.includes('订单超时关闭');
    }
    return stateDesc.includes('已取消') || stateDesc.includes('已完成') || stateDesc.includes('订单超时关闭');
}

async function refreshVisibleOrderDetails(orders) {
    const candidates = (orders || []).filter(order => order && order.orderNo && !isTerminalHistoryOrder(order));
    if (!candidates.length) {
        return;
    }

    const results = await Promise.allSettled(candidates.map(order => getOrderDetail(order.orderNo, true)));
    results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
            updateOrderFromDetail(candidates[index].orderNo, result.value);
        }
    });
}

async function loadOrders(reset, options = {}) {
    const { preserveItems = false } = options;
    if (state.orders.loading) {
        return false;
    }

    const nextPage = reset ? 1 : state.orders.page + 1;
    if (!reset && !state.orders.hasMore) {
        return false;
    }

    state.orders.loading = true;
    let loaded = false;
    if (reset && !preserveItems) {
        state.orders.items = [];
        state.orders.page = 0;
        state.orders.hasMore = true;
        state.orders.expanded = {};
        renderOrders();
    }

    try {
        const data = await apiPost('/api/orders/history', {
            page: nextPage,
            pageSize: state.orders.pageSize,
        });
        const pageItems = data.items || [];
        state.orders.items = reset ? pageItems : state.orders.items.concat(pageItems);
        state.orders.page = data.page || nextPage;
        state.orders.total = data.total || state.orders.items.length;
        state.orders.hasMore = Boolean(data.hasMore);
        if (reset && !preserveItems) {
            state.orders.expanded = {};
        }
        await refreshVisibleOrderDetails(pageItems);
        loaded = true;
    } catch (error) {
        syncTokenFailure(error);
        handleRequestError(error, '读取订单失败。', true);
    } finally {
        state.orders.loading = false;
        renderOrders();
    }
    return loaded;
}

async function refreshOrdersSnapshot() {
    if (state.orders.loading) {
        return false;
    }

    const requestedCount = Math.max(state.orders.items.length, state.orders.pageSize);
    state.orders.loading = true;
    let loaded = false;
    try {
        const data = await apiPost('/api/orders/history', {
            page: 1,
            pageSize: requestedCount,
        });
        const pageItems = data.items || [];
        state.orders.items = pageItems;
        state.orders.page = Math.max(1, Math.ceil(pageItems.length / state.orders.pageSize));
        state.orders.total = data.total || pageItems.length;
        state.orders.hasMore = Boolean(data.hasMore);
        state.orders.expanded = Object.fromEntries(
            Object.entries(state.orders.expanded).filter(([orderNo]) => pageItems.some(order => order.orderNo === orderNo))
        );
        await refreshVisibleOrderDetails(pageItems);
        loaded = true;
    } catch (error) {
        syncTokenFailure(error);
        handleRequestError(error, '读取订单失败。', true);
    } finally {
        state.orders.loading = false;
        renderOrders();
    }
    return loaded;
}

async function getRoomMachines(positionId, options = {}) {
    const { forceRefresh = false } = options;
    if (!forceRefresh && cache.roomMachines.has(positionId)) {
        return cache.roomMachines.get(positionId);
    }
    const data = await apiGet(`/api/laundry/rooms/${encodeURIComponent(positionId)}/machines`);
    cache.roomMachines.set(positionId, data);
    return data;
}

async function getMachineDetail(goodsId, options = {}) {
    const { forceRefresh = false } = options;
    if (!forceRefresh && cache.machineDetails.has(goodsId)) {
        return cache.machineDetails.get(goodsId);
    }
    const data = await apiGet(`/api/laundry/machines/${encodeURIComponent(goodsId)}`);
    cache.machineDetails.set(goodsId, data.machine);
    return data.machine;
}

async function getScanModes(qrCode, options = {}) {
    const { forceRefresh = false } = options;
    if (!forceRefresh && cache.scanModes.has(qrCode)) {
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

function applyOrderDetail(orderNo, detail) {
    if (!orderNo || !detail) {
        return;
    }
    cache.orderDetails.set(orderNo, detail);
    updateOrderFromDetail(orderNo, detail);
}

async function getProcessDetail(processId) {
    const data = await apiGet(`/api/processes/${encodeURIComponent(processId)}`);
    return data.process;
}

async function hydrateReservationForm() {
    populateReservationScanOptions();
    populateReservationRoomOptions();
    toggleReservationSourceFields();
    toggleReservationScheduleFields();
    await refreshReservationMachineOptions();
}

function populateReservationScanOptions() {
    fillSelect(
        el.reservationScanMachine,
        (state.wash.scanMachines || []).map(machine => ({
            value: machine.qrCode,
            label: machine.label,
        })),
        '请选择扫码机组'
    );
}

function populateReservationRoomOptions() {
    fillSelect(
        el.reservationRoom,
        (state.wash.rooms || []).map(room => ({
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
        fillSelect(el.reservationMachine, [], '扫码机组无需额外选择设备');
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
        const machines = (payload.machines || []).filter(machine => machine.supportsVirtualScan && machine.scanCode);
        state.reservations.roomMachines = machines;
        fillSelect(
            el.reservationMachine,
            machines.map(machine => ({
                value: machine.goodsId,
                label: `${machine.name} · ${machine.statusDetail || machine.stateDesc}`,
            })),
            machines.length ? '请选择洗衣机' : '当前洗衣房暂无可预约机器'
        );
        await refreshReservationModes();
    } catch (error) {
        syncTokenFailure(error);
        handleRequestError(error, '加载预约机器失败。', true);
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
            populateReservationModeOptions(await getScanModes(qrCode));
            return;
        }

        const goodsId = el.reservationMachine.value;
        if (!goodsId) {
            return;
        }
        const detail = await getMachineDetail(goodsId);
        const modes = (detail.modes || []).filter(() => detail.supportsVirtualScan);
        populateReservationModeOptions(modes);
    } catch (error) {
        syncTokenFailure(error);
        handleRequestError(error, '读取预约模式失败。', true);
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

function clearWashTransientState() {
    state.wash.roomId = null;
    state.wash.roomData = null;
    state.wash.machineId = null;
    state.wash.machineDetail = null;
    state.wash.scanMachine = null;
    state.wash.process = null;
    state.wash.result = null;
}

function normalizeComparableText(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[\s·•,，。:：()（）\-_/]+/g, '');
}

function uniqueSubtitle(primary, secondary) {
    const subtitle = String(secondary || '').trim();
    if (!subtitle) {
        return '';
    }
    return normalizeComparableText(primary) === normalizeComparableText(subtitle) ? '' : subtitle;
}

function joinCompactText(parts) {
    return (parts || [])
        .map(item => String(item || '').trim())
        .filter(Boolean)
        .join(' · ');
}

function renderMetaPill(label, value) {
    const text = String(value == null ? '' : value).trim();
    if (!text || text === '--') {
        return '';
    }
    return `<span class="meta-pill">${escapeHtml(label)}<strong>${escapeHtml(text)}</strong></span>`;
}

function renderWashLoading(message) {
    el.washView.innerHTML = `<div class="panel-card loading-card">${escapeHtml(message)}</div>`;
}

function renderWash() {
    if (state.bootstrap.configLoading || state.wash.loading) {
        renderWashLoading('正在加载洗衣房和扫码机组...');
        return;
    }

    if (!isTokenReady()) {
        el.washView.innerHTML = renderStatusCard('warning', '需要可用 Token', getTokenAlertMessage(), `
            <div class="spacer-sm"></div>
            <button class="btn btn-primary full-width" type="button" data-action="goto-settings">去设置</button>
        `);
        return;
    }

    if (state.wash.view === 'room') {
        el.washView.innerHTML = renderWashRoomView();
        return;
    }
    if (state.wash.view === 'machine') {
        el.washView.innerHTML = renderWashMachineView();
        return;
    }
    if (state.wash.view === 'scan') {
        el.washView.innerHTML = renderWashScanView();
        return;
    }
    if (state.wash.view === 'process') {
        el.washView.innerHTML = renderWashProcessView();
        return;
    }

    el.washView.innerHTML = renderWashHomeView();
}

function renderWashHomeView() {
    const rooms = state.wash.rooms || [];
    const scanMachines = state.wash.scanMachines || [];
    return `
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>洗衣房</h3>
                    <p>按房间快速找空闲设备。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${rooms.length} 个</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="room-list">
                ${rooms.length ? rooms.map(renderRoomCard).join('') : renderEmptyState('暂无洗衣房', '当前没有可用洗衣房数据。')}
            </div>
        </section>
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>扫码机组</h3>
                    <p>从本地机组直接进入手动流程。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip warning">${scanMachines.length} 台</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="scan-list">
                ${scanMachines.length ? scanMachines.map(renderScanMachineCard).join('') : renderEmptyState('暂无扫码机组', '请检查 machines.json 是否已配置。')}
            </div>
        </section>
    `;
}

function renderWashRoomView() {
    const payload = state.wash.roomData || {};
    const room = payload.room || {};
    const machines = payload.machines || [];
    const subtitle = uniqueSubtitle(room.name || '洗衣房', room.address || '');
    return `
        <section class="panel-card">
            <div class="action-row">
                <button class="btn btn-light" type="button" data-action="back-home">返回洗衣首页</button>
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>${escapeHtml(room.name || '洗衣房')}</h3>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${machines.length} 台</span>
                    <span class="compact-side-value">${escapeHtml(stringOrFallback(room.idleCount, '--'))} 空闲</span>
                </div>
            </div>
        </section>
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>设备列表</h3>
                    <p>运行中的设备直接显示预计完成时间。</p>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="machine-list">
                ${machines.length ? machines.map(renderRoomMachineCard).join('') : renderEmptyState('暂无设备', '这个洗衣房当前没有可显示的设备。')}
            </div>
        </section>
    `;
}

function renderWashMachineView() {
    const machine = state.wash.machineDetail;
    if (!machine) {
        return renderEmptyState('设备未找到', '请返回上一页重新选择设备。');
    }

    const locationText = machine.shopName || machine.shopAddress || '暂无洗衣房信息';
    const subtitle = uniqueSubtitle(machine.name || '设备', locationText);
    const modeOptions = (machine.modes || []).map(mode => ({
        value: String(mode.id),
        label: `${mode.label} · ${mode.price} 元`,
    }));
    return `
        <section class="panel-card">
            <div class="action-row">
                <button class="btn btn-light" type="button" data-action="back-room">返回设备列表</button>
                <button class="btn btn-light" type="button" data-action="back-home">返回洗衣首页</button>
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>${escapeHtml(machine.name || '设备')}</h3>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${modeOptions.length} 个模式</span>
                    ${machine.categoryCode ? `<span class="compact-side-value">${escapeHtml(machine.categoryCode)}</span>` : ''}
                </div>
            </div>
            <div class="detail-grid">
                <div><span>设备编号</span><span>${escapeHtml(machine.goodsId || '--')}</span></div>
                <div><span>设备类型</span><span>${escapeHtml(machine.categoryCode || '--')}</span></div>
            </div>
            <label class="mode-select">
                选择模式
                ${renderSelect('washModeSelect', modeOptions, '请选择模式')}
            </label>
            <div class="spacer-sm"></div>
            <div class="action-row">
                <button class="btn btn-secondary" type="button" data-action="create-lock-order">线上下单（到创建订单）</button>
                ${machine.supportsVirtualScan && machine.scanCode ? '<button class="btn btn-primary" type="button" data-action="open-machine-scan">虚拟扫码手动流程</button>' : ''}
            </div>
        </section>
        ${renderWashResult()}
    `;
}

function renderWashScanView() {
    const machine = state.wash.scanMachine;
    if (!machine) {
        return renderEmptyState('扫码机组未找到', '请返回洗衣首页重新选择扫码机组。');
    }

    const modeOptions = (machine.modes || []).map(mode => ({
        value: String(mode.id),
        label: `${mode.label} · ${mode.price} 元`,
    }));
    return `
        <section class="panel-card">
            <div class="action-row">
                <button class="btn btn-light" type="button" data-action="back-home">返回洗衣首页</button>
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>${escapeHtml(machine.label || '扫码机组')}</h3>
                    <p>二维码编号：${escapeHtml(maskCode(machine.qrCode || ''))}</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip warning">手动流程</span>
                    <span class="compact-side-value">${modeOptions.length} 个模式</span>
                </div>
            </div>
            <label class="mode-select">
                选择模式
                ${renderSelect('scanModeSelect', modeOptions, '请选择模式')}
            </label>
            <div class="spacer-sm"></div>
            <div class="action-row">
                <button class="btn btn-primary" type="button" data-action="start-scan-process">开始手动流程</button>
            </div>
        </section>
        ${renderWashResult()}
    `;
}

function renderWashProcessView() {
    const process = state.wash.process;
    if (!process) {
        return renderEmptyState('流程不存在', '请从扫码机组重新开始，或到订单页继续已有流程。');
    }

    const currentStep = Number(process.currentStep || 1);
    const order = process.order || null;
    const orderNo = process.orderNo || ((process.contextSummary || {}).orderNo) || '';
    const stepList = PROCESS_STEPS.map(step => {
        let className = 'pending';
        let text = '待执行';
        if (process.completed || currentStep > step.id) {
            className = 'success';
            text = '完成';
        } else if (!process.completed && !process.terminated && currentStep === step.id) {
            className = 'warning';
            text = '当前';
        } else if (process.terminated) {
            className = 'danger';
            text = '中止';
        }
        return `
            <div class="process-step ${className}">
                <div>
                    <strong>${step.id}. ${escapeHtml(step.label)}</strong>
                    <p>${escapeHtml(step.hint)}</p>
                </div>
                <span class="chip ${className}">${text}</span>
            </div>
        `;
    }).join('');

    const summaryCard = process.terminated
        ? renderStatusCard('danger', '流程已终止', process.blockedReason || '当前流程无法继续。')
        : process.completed
            ? renderStatusCard('success', '流程已完成', '订单已经支付并启动。')
            : renderStatusCard('warning', '手动扫码流程', `当前步骤：${process.currentStepLabel || '待执行'}`);

    return `
        ${summaryCard}
        <section class="panel-card">
            <div class="action-row">
                <button class="btn btn-light" type="button" data-action="back-home">返回洗衣首页</button>
                <button class="btn btn-light" type="button" data-action="goto-orders">去订单页</button>
            </div>
            <div class="spacer-sm"></div>
            <div class="detail-grid">
                <div><span>流程编号</span><span>${escapeHtml(maskCode(process.processId || ''))}</span></div>
                <div><span>二维码编号</span><span>${escapeHtml(maskCode(process.qrCode || ''))}</span></div>
                <div><span>订单号</span><span>${escapeHtml(orderNo || '--')}</span></div>
                <div><span>最近更新</span><span>${escapeHtml(formatDateTime(process.updatedAt))}</span></div>
            </div>
            ${order ? `
                <div class="spacer-sm"></div>
                <div class="callout">
                    <strong>${escapeHtml(order.machineName || '订单设备')}</strong>
                    <p class="body-text">状态：${escapeHtml(order.stateDesc || '--')} ${order.pageCode ? `· ${escapeHtml(order.pageCode)}` : ''}</p>
                    ${renderOrderTimeGrid(order)}
                </div>
            ` : ''}
        </section>
        <section class="panel-card">
            <h3>执行步骤</h3>
            <div class="spacer-sm"></div>
            <div class="process-step-list">${stepList}</div>
            <div class="spacer-sm"></div>
            <div class="action-row">
                ${!process.completed && !process.terminated ? `<button class="btn btn-primary" type="button" data-action="next-process">下一步：${escapeHtml(process.currentStepLabel || '继续')}</button>` : ''}
                <button class="btn btn-light" type="button" data-action="goto-orders">去订单页继续</button>
                <button class="btn btn-danger" type="button" data-action="reset-process">放弃流程</button>
            </div>
        </section>
    `;
}

function renderWashResult() {
    if (!state.wash.result) {
        return '';
    }

    const result = state.wash.result;
    return `
        <section class="status-card ${escapeHtml(result.variant || 'warning')}">
            <div class="status-title">
                <span>${escapeHtml(result.title || '操作结果')}</span>
                <span class="chip ${escapeHtml(result.variant || 'warning')}">${escapeHtml(variantLabel(result.variant || 'warning'))}</span>
            </div>
            <p class="body-text">${escapeHtml(result.message || '')}</p>
            ${result.order ? `
                <div class="spacer-sm"></div>
                <div class="detail-grid">
                    <div><span>订单号</span><span>${escapeHtml(result.order.orderNo || '--')}</span></div>
                    <div><span>订单状态</span><span>${escapeHtml(result.order.stateDesc || '--')}</span></div>
                </div>
            ` : ''}
            ${result.todo ? `
                <div class="spacer-sm"></div>
                <div class="callout warning">${escapeHtml(result.todo.nextStep || '后续支付链路待补充。')}</div>
            ` : ''}
        </section>
    `;
}

function renderReservations() {
    if (state.bootstrap.reservationsLoading) {
        el.reservationList.innerHTML = `<div class="panel-card loading-card">正在加载预约和调度器状态...</div>`;
        return;
    }

    const items = state.reservations.items || [];
    const scheduler = state.scheduler || {};
    el.reservationList.innerHTML = `
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>调度器状态</h3>
                    <p>轮询间隔修改后会立即生效。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip ${scheduler.running ? 'success' : 'danger'}">${scheduler.running ? '运行中' : '已停止'}</span>
                </div>
            </div>
            <div class="detail-grid">
                <div><span>轮询间隔</span><span>${escapeHtml(stringOrFallback(scheduler.intervalSeconds, '--'))} 秒</span></div>
                <div><span>最近轮询</span><span>${escapeHtml(formatDateTime(scheduler.lastTickAt))}</span></div>
                <div><span>最近创建</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).created, 0))}</span></div>
                <div><span>最近接管</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).adopted, 0))}</span></div>
                <div><span>最近补建</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).recreated, 0))}</span></div>
            </div>
            ${scheduler.lastError ? `<div class="spacer-sm"></div><div class="callout danger">${escapeHtml(scheduler.lastError)}</div>` : ''}
        </section>
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>预约任务</h3>
                    <p>手动结束订单后会自动暂停任务。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${items.length} 个</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="reservation-list">
                ${items.length ? items.map(renderReservationCard).join('') : renderEmptyState('暂无预约任务', '创建后会在这里显示每次保单窗口和最近一次执行结果。')}
            </div>
        </section>
    `;
}

function renderOrders() {
    if (state.bootstrap.configLoading) {
        el.ordersList.innerHTML = `<div class="panel-card loading-card">正在加载订单页面...</div>`;
        el.loadMoreOrdersBtn.classList.add('hidden');
        return;
    }

    if (!isTokenReady()) {
        el.ordersList.innerHTML = renderStatusCard('warning', '需要可用 Token', getTokenAlertMessage(), `
            <div class="spacer-sm"></div>
            <button class="btn btn-primary full-width" type="button" data-action="goto-settings">去设置</button>
        `);
        el.loadMoreOrdersBtn.classList.add('hidden');
        return;
    }

    const activeProcesses = state.orders.activeProcesses || [];
    const items = state.orders.items || [];
    const processByOrderNo = new Map(activeProcesses.filter(item => item && item.orderNo).map(item => [item.orderNo, item]));

    el.ordersList.innerHTML = `
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>待继续流程</h3>
                    <p>已创建订单但未走完的流程会保留在这里。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip warning">${activeProcesses.length} 条</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="order-list">
                ${activeProcesses.length ? activeProcesses.map(renderActiveProcessCard).join('') : renderEmptyState('暂无待继续流程', '当手动扫码流程创建出订单后，就会出现在这里。')}
            </div>
        </section>
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>历史订单</h3>
                    <p>下拉自动加载更多，展开查看完整信息。</p>
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${state.orders.total || items.length} 条</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="order-list">
                ${items.length ? items.map(order => renderHistoryOrderCard(order, processByOrderNo.get(order.orderNo) || null)).join('') : renderEmptyState('暂无订单', '当前没有可显示的历史订单。')}
            </div>
        </section>
    `;

    el.loadMoreOrdersBtn.classList.toggle('hidden', !state.orders.hasMore || state.orders.loading);
    el.loadMoreOrdersBtn.disabled = state.orders.loading || !state.orders.hasMore;
}

function renderSettings() {
    if (state.bootstrap.settingsLoading) {
        el.settingsInfo.innerHTML = `<div class="panel-card loading-card">正在加载设置...</div>`;
        return;
    }

    const settings = state.settings || {};
    const scheduler = state.scheduler || {};
    const sources = settings.sources || {};
    el.settingsInfo.innerHTML = `
        ${renderStatusCard(tokenClassName(state.tokenStatus.reason), 'Token 状态', getTokenAlertMessage(), `
            <div class="spacer-sm"></div>
            <div class="detail-grid">
                <div><span>Token 来源</span><span>${escapeHtml(sourceText(state.tokenStatus.source))}</span></div>
                <div><span>当前状态</span><span>${escapeHtml(tokenLabel())}</span></div>
            </div>
        `)}
        <section class="panel-card">
            <div class="card-title">
                <div>
                    <h3>设置来源</h3>
                    <p>数据库中的设置优先级高于 <code>.env</code> 默认值。</p>
                </div>
            </div>
            <div class="detail-grid">
                <div><span>Token</span><span>${escapeHtml(sourceText(sources.token))}</span></div>
                <div><span>PushPlus</span><span>${escapeHtml(sourceText(sources.pushplusUrl))}</span></div>
                <div><span>默认提前分钟</span><span>${escapeHtml(sourceText(sources.defaultLeadMinutes))}</span></div>
                <div><span>轮询间隔</span><span>${escapeHtml(sourceText(sources.reservationPollIntervalSeconds))}</span></div>
            </div>
        </section>
        <section class="panel-card">
            <div class="card-title">
                <div>
                    <h3>调度器快照</h3>
                    <p>保存轮询间隔后会立即热更新，不需要重启服务。</p>
                </div>
                <span class="chip ${scheduler.running ? 'success' : 'danger'}">${scheduler.running ? '运行中' : '已停止'}</span>
            </div>
            <div class="detail-grid">
                <div><span>当前间隔</span><span>${escapeHtml(stringOrFallback(scheduler.intervalSeconds, '--'))} 秒</span></div>
                <div><span>最近轮询</span><span>${escapeHtml(formatDateTime(scheduler.lastTickAt))}</span></div>
                <div><span>最近创建</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).created, 0))}</span></div>
                <div><span>最近接管</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).adopted, 0))}</span></div>
                <div><span>最近补建</span><span>${escapeHtml(stringOrFallback((scheduler.lastResult || {}).recreated, 0))}</span></div>
            </div>
            ${scheduler.lastError ? `<div class="spacer-sm"></div><div class="callout danger">${escapeHtml(scheduler.lastError)}</div>` : ''}
        </section>
    `;
}

function renderRoomCard(room) {
    const subtitle = uniqueSubtitle(room.name || '洗衣房', room.address || '');
    return `
        <article class="list-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(room.name || '洗衣房')}</h4>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <div class="compact-side">
                    <span class="chip ${room.enableReserve ? 'success' : 'pending'}">${room.enableReserve ? '可预约' : '普通房间'}</span>
                    <span class="compact-side-value">${escapeHtml(stringOrFallback(room.idleCount, '--'))} 空闲</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-actions">
                <button class="btn btn-primary" type="button" data-action="open-room" data-room-id="${escapeHtml(room.id)}">查看设备</button>
            </div>
        </article>
    `;
}

function renderScanMachineCard(machine) {
    return `
        <article class="list-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(machine.label || '扫码机组')}</h4>
                    <p>二维码编号：${escapeHtml(maskCode(machine.qrCode || ''))}</p>
                </div>
                <div class="compact-side">
                    <span class="chip warning">手动流程</span>
                </div>
            </div>
            <div class="compact-actions">
                <button class="btn btn-primary" type="button" data-action="open-scan" data-qr-code="${escapeHtml(machine.qrCode)}">进入流程</button>
            </div>
        </article>
    `;
}

function renderRoomMachineCard(machine) {
    const subtitle = uniqueSubtitle(machine.name || '设备', machine.floorCode || '');
    const timeText = machine.finishTimeText
        ? `预计 ${machine.finishTimeText}`
        : (machine.statusLabel === '空闲' ? '现在可用' : (machine.statusDetail || '--'));
    const detailText = machine.statusDetail && machine.statusDetail !== timeText ? machine.statusDetail : '';
    return `
        <article class="machine-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(machine.name || '设备')}</h4>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <div class="compact-side">
                    <span class="chip ${machineChipClass(machine)}">${escapeHtml(machine.statusLabel || machine.stateDesc || '未知')}</span>
                    <span class="compact-side-value">${escapeHtml(timeText)}</span>
                </div>
            </div>
            <div class="compact-meta-row">
                ${detailText ? renderMetaPill('状态', detailText) : ''}
                ${machine.floorCode ? renderMetaPill('楼层', machine.floorCode) : ''}
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-actions">
                <button class="btn btn-secondary" type="button" data-action="open-machine" data-goods-id="${escapeHtml(machine.goodsId)}">查看详情</button>
                ${machine.supportsVirtualScan && machine.scanCode ? `<button class="btn btn-light" type="button" data-action="open-scan" data-qr-code="${escapeHtml(machine.scanCode)}">虚拟扫码</button>` : ''}
            </div>
        </article>
    `;
}

function renderReservationCard(task) {
    const lastEvent = task.lastEvent || {};
    const statusClass = reservationStatusClass(task.status);
    const currentOrder = task.currentOrder || null;
    const subtitle = joinCompactText([task.machineName || '', task.modeName || '']);
    return `
        <article class="order-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(task.title || task.machineName || '预约任务')}</h4>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <div class="compact-side">
                    <span class="chip ${statusClass}">${escapeHtml(reservationStatusLabel(task.status))}</span>
                    <span class="compact-side-value">${escapeHtml(formatDateTime(task.targetTime))}</span>
                </div>
            </div>
            <div class="compact-meta-row">
                ${renderMetaPill('保单窗口', formatWindow(task.startAt, task.holdUntil))}
                ${renderMetaPill('活跃订单', task.activeOrderNo || '')}
                ${renderMetaPill('订单状态', (currentOrder || {}).stateDesc || '')}
                ${renderMetaPill('最近检查', formatDateTime(task.lastCheckedAt))}
            </div>
            ${task.lastError ? `<div class="spacer-sm"></div><div class="callout warning">${escapeHtml(task.lastError)}</div>` : ''}
            ${lastEvent.message ? `<div class="spacer-sm"></div><div class="callout">${escapeHtml(lastEvent.message)}</div>` : ''}
            <div class="spacer-sm"></div>
            <div class="compact-actions">
                ${task.processId ? `<button class="btn btn-primary" type="button" data-action="continue-reservation-process" data-task-id="${escapeHtml(String(task.id))}" data-process-id="${escapeHtml(task.processId)}">继续流程</button>` : ''}
                ${task.status === 'paused' ? `<button class="btn btn-secondary" type="button" data-action="resume-reservation" data-task-id="${escapeHtml(String(task.id))}">恢复</button>` : `<button class="btn btn-light" type="button" data-action="pause-reservation" data-task-id="${escapeHtml(String(task.id))}">暂停</button>`}
                <button class="btn btn-danger" type="button" data-action="delete-reservation" data-task-id="${escapeHtml(String(task.id))}">删除</button>
            </div>
        </article>
    `;
}

function renderActiveProcessCard(process) {
    const order = process.order || {};
    const timeMeta = getOrderTimeMeta(order);
    return `
        <article class="order-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(order.machineName || '待继续流程')}</h4>
                    <p>订单号：${escapeHtml(process.orderNo || '--')}</p>
                </div>
                <div class="compact-side">
                    <span class="chip warning">${escapeHtml(process.currentStepLabel || '待继续')}</span>
                    <span class="compact-side-value">${escapeHtml(formatDateTime(process.updatedAt))}</span>
                </div>
            </div>
            <div class="compact-meta-row">
                ${renderMetaPill('订单状态', order.stateDesc || '')}
                ${renderMetaPill('页面状态', order.pageCode || '')}
                ${timeMeta ? renderMetaPill(timeMeta.countdownLabel, timeMeta.countdownText) : ''}
            </div>
            ${process.blockedReason ? `<div class="spacer-sm"></div><div class="callout warning">${escapeHtml(process.blockedReason)}</div>` : ''}
            <div class="spacer-sm"></div>
            <div class="compact-actions">
                <button class="btn btn-primary" type="button" data-action="continue-process" data-process-id="${escapeHtml(process.processId)}">继续流程</button>
                <button class="btn btn-danger" type="button" data-action="abandon-process" data-process-id="${escapeHtml(process.processId)}">放弃流程</button>
            </div>
        </article>
    `;
}

function renderHistoryOrderCard(order, activeProcess) {
    const expanded = Boolean(state.orders.expanded[order.orderNo]);
    const processOrder = activeProcess ? (activeProcess.order || null) : null;
    const detail = cache.orderDetails.get(order.orderNo);
    const mergedOrder = {
        ...(order || {}),
        ...(processOrder || {}),
        ...(detail || {}),
    };
    const buttonSwitch = mergedOrder.buttonSwitch || {};
    const timeMeta = getOrderTimeMeta(mergedOrder);
    return `
        <article class="order-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(mergedOrder.machineName || order.machineName || '订单')}</h4>
                    <p>${escapeHtml(joinCompactText([mergedOrder.modeName || order.modeName || '--', order.orderNo || '--']))}</p>
                </div>
                <div class="compact-side">
                    <div class="inline-row">
                        ${activeProcess ? '<span class="chip warning">流程进行中</span>' : ''}
                        <span class="chip ${orderChipClass(mergedOrder.state)}">${escapeHtml(mergedOrder.stateDesc || order.stateDesc || '未知状态')}</span>
                    </div>
                    <span class="compact-side-value">${escapeHtml(timeMeta ? timeMeta.countdownText : formatDateTime(mergedOrder.createTime || order.createTime))}</span>
                </div>
            </div>
            <div class="compact-meta-row">
                ${renderMetaPill('创建时间', formatDateTime(mergedOrder.createTime || order.createTime))}
                ${timeMeta ? renderMetaPill(timeMeta.label, formatDateTime(timeMeta.value)) : ''}
            </div>
            <div class="spacer-sm"></div>
            <div class="compact-actions">
                <button class="btn btn-light" type="button" data-action="toggle-order-detail" data-order-no="${escapeHtml(order.orderNo)}">${expanded ? '收起详情' : '展开详情'}</button>
                ${buttonSwitch.canCloseOrder ? `<button class="btn btn-danger" type="button" data-action="finish-order" data-order-no="${escapeHtml(order.orderNo)}">结束订单</button>` : ''}
                ${buttonSwitch.canCancel ? `<button class="btn btn-light" type="button" data-action="cancel-order" data-order-no="${escapeHtml(order.orderNo)}">取消订单</button>` : ''}
            </div>
            ${expanded ? renderOrderDetail(detail || processOrder || mergedOrder) : ''}
        </article>
    `;
}

function renderOrderDetail(detail) {
    if (!detail) {
        return `
            <div class="spacer-sm"></div>
            <div class="callout">正在加载订单详情...</div>
        `;
    }
    const timeMeta = getOrderTimeMeta(detail);
    return `
        <div class="spacer-sm"></div>
        <div class="detail-grid">
            <div><span>状态</span><span>${escapeHtml(detail.stateDesc || '--')}</span></div>
            <div><span>页面状态</span><span>${escapeHtml(detail.pageCode || '--')}</span></div>
            <div><span>价格</span><span>${escapeHtml(stringOrFallback(detail.price, '--'))}</span></div>
            <div><span>支付时间</span><span>${escapeHtml(formatDateTime(detail.payTime))}</span></div>
            <div><span>${escapeHtml(timeMeta ? timeMeta.label : '时间')}</span><span>${escapeHtml(formatDateTime(timeMeta ? timeMeta.value : ''))}</span></div>
            <div><span>${escapeHtml(timeMeta ? timeMeta.countdownLabel : '当前阶段')}</span><span>${escapeHtml(timeMeta ? timeMeta.countdownText : '--')}</span></div>
            <div><span>洗衣房</span><span>${escapeHtml(detail.shopName || '--')}</span></div>
            <div><span>模式</span><span>${escapeHtml(detail.modeName || '--')}</span></div>
        </div>
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
        <div class="empty-state">
            <strong>${escapeHtml(title)}</strong>
            <p>${escapeHtml(description)}</p>
        </div>
    `;
}

function renderSelect(id, items, placeholder) {
    const options = [`<option value="">${escapeHtml(placeholder)}</option>`]
        .concat((items || []).map(item => `<option value="${escapeHtml(String(item.value))}">${escapeHtml(item.label)}</option>`));
    return `<select id="${escapeHtml(id)}">${options.join('')}</select>`;
}

async function handleWashClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) {
        return;
    }

    const action = button.dataset.action;
    if (action === 'goto-settings') {
        switchTab('settingsTab');
        return;
    }
    if (action === 'goto-orders') {
        switchTab('orderTab');
        return;
    }
    if (action === 'back-home') {
        clearWashTransientState();
        state.wash.view = 'home';
        renderWash();
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
        pushHistoryState();
        return;
    }
    if (action === 'back-room') {
        if (state.wash.roomId && state.wash.roomData) {
            state.wash.view = 'room';
            state.wash.machineId = null;
            state.wash.machineDetail = null;
            state.wash.scanMachine = null;
            state.wash.process = null;
            state.wash.result = null;
        } else {
            clearWashTransientState();
            state.wash.view = 'home';
        }
        renderWash();
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
        pushHistoryState();
        return;
    }

    if (!ensureTokenReady()) {
        return;
    }

    try {
        if (action === 'open-room') {
            await openRoom(button.dataset.roomId);
            return;
        }
        if (action === 'open-machine') {
            await openMachine(button.dataset.goodsId);
            return;
        }
        if (action === 'open-scan') {
            await openScanMachine(button.dataset.qrCode);
            return;
        }
        if (action === 'open-machine-scan') {
            await openMachineScan();
            return;
        }
        if (action === 'create-lock-order') {
            await createLockOrder();
            return;
        }
        if (action === 'start-scan-process') {
            await startScanProcess();
            return;
        }
        if (action === 'next-process') {
            await advanceProcess();
            return;
        }
        if (action === 'reset-process') {
            await resetProcess();
        }
    } catch (error) {
        handleRequestError(error, '操作失败，请稍后重试。');
    }
}

async function openRoom(roomId, options = {}) {
    state.wash.loading = true;
    renderWashLoading('正在加载洗衣房设备...');
    try {
        const payload = await getRoomMachines(roomId);
        state.wash.view = 'room';
        state.wash.roomId = roomId;
        state.wash.roomData = payload;
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.scanMachine = null;
        state.wash.process = null;
        state.wash.result = null;
        state.wash.loading = false;
        markTabRefreshed('washTab');
        renderWash();
        scheduleActiveAutoRefresh();
        if (options.pushHistory !== false) {
            pushHistoryState();
        }
    } catch (error) {
        state.wash.loading = false;
        renderWash();
        throw error;
    }
}

async function openMachine(goodsId, options = {}) {
    state.wash.loading = true;
    renderWashLoading('正在加载设备详情...');
    try {
        if (options.roomId && (!state.wash.roomData || state.wash.roomId !== options.roomId)) {
            state.wash.roomData = await getRoomMachines(options.roomId);
            state.wash.roomId = options.roomId;
        }
        state.wash.machineDetail = await getMachineDetail(goodsId);
        state.wash.view = 'machine';
        state.wash.machineId = goodsId;
        state.wash.scanMachine = null;
        state.wash.process = null;
        state.wash.result = null;
        state.wash.loading = false;
        markTabRefreshed('washTab');
        renderWash();
        scheduleActiveAutoRefresh();
        if (options.pushHistory !== false) {
            pushHistoryState();
        }
    } catch (error) {
        state.wash.loading = false;
        renderWash();
        throw error;
    }
}

async function openScanMachine(qrCode, options = {}) {
    const localMachine = (state.wash.scanMachines || []).find(item => item.qrCode === qrCode);
    state.wash.loading = true;
    renderWashLoading('正在读取可用模式...');
    try {
        state.wash.scanMachine = {
            ...(localMachine || { label: options.label || '虚拟扫码设备', qrCode }),
            modes: await getScanModes(qrCode),
        };
        state.wash.view = 'scan';
        state.wash.machineDetail = null;
        state.wash.process = null;
        state.wash.result = null;
        state.wash.loading = false;
        markTabRefreshed('washTab');
        renderWash();
        scheduleActiveAutoRefresh();
        if (options.pushHistory !== false) {
            pushHistoryState();
        }
    } catch (error) {
        state.wash.loading = false;
        renderWash();
        throw error;
    }
}

async function openMachineScan() {
    const machine = state.wash.machineDetail;
    if (!machine || !machine.supportsVirtualScan || !machine.scanCode) {
        await showAlertDialog('当前设备不支持虚拟扫码流程。');
        return;
    }
    await openScanMachine(machine.scanCode, { label: machine.name });
}

async function openProcess(processId, options = {}) {
    state.wash.loading = true;
    renderWashLoading('正在恢复流程...');
    try {
        state.wash.process = await getProcessDetail(processId);
        const orderNo = state.wash.process.orderNo || ((state.wash.process.contextSummary || {}).orderNo) || '';
        if (orderNo) {
            try {
                const detail = await getOrderDetail(orderNo, true);
                state.wash.process.order = detail;
                updateOrderFromDetail(orderNo, detail);
            } catch (error) {
                handleRequestError(error, '刷新订单详情失败。', true);
            }
        }
        state.wash.view = 'process';
        state.wash.result = null;
        state.wash.loading = false;
        markTabRefreshed('washTab');
        renderWash();
        renderOrders();
        scheduleActiveAutoRefresh();
        if (options.pushHistory !== false) {
            pushHistoryState();
        }
    } catch (error) {
        state.wash.loading = false;
        renderWash();
        throw error;
    }
}

async function createLockOrder() {
    const machine = state.wash.machineDetail;
    const modeId = getSelectedValue('washModeSelect');
    if (!machine || !modeId) {
        await showAlertDialog('请先选择模式。');
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
    showToastMessage('线上下单已到创建订单，后续链路仍是 TODO。');
}

async function startScanProcess() {
    const scanMachine = state.wash.scanMachine;
    const qrCode = scanMachine ? scanMachine.qrCode : '';
    const modeId = getSelectedValue('scanModeSelect');
    if (!qrCode || !modeId) {
        await showAlertDialog('请先选择模式。');
        return;
    }

    const data = await apiPost('/api/process/start', { qrCode, modeId });
    showToastMessage(data.msg || '流程已创建。');
    state.wash.result = null;
    await openProcess((data.process || {}).processId);
}

async function advanceProcess() {
    const process = state.wash.process;
    if (!process || !process.processId) {
        await showAlertDialog('当前没有可继续的流程。');
        return;
    }

    const data = await apiPost('/api/process/next', { processId: process.processId });
    await openProcess(process.processId, { pushHistory: false });
    await loadActiveProcesses();
    const latestOrderNo = (state.wash.process && (state.wash.process.orderNo || ((state.wash.process.contextSummary || {}).orderNo))) || '';
    if (latestOrderNo) {
        refreshOrdersOverview(true, { silent: true, preserveItems: true });
    } else if ((data.process || {}).completed) {
        refreshOrdersOverview(true, { silent: true, preserveItems: true });
    }
    showToastMessage(data.msg || '流程已推进。');
}

async function resetProcess() {
    const process = state.wash.process;
    if (!process || !process.processId) {
        clearWashTransientState();
        state.wash.view = 'home';
        renderWash();
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
        pushHistoryState();
        return;
    }
    if (!(await showConfirmDialog('确定放弃当前流程吗？如已创建订单，会尝试同时结束云端订单。'))) {
        return;
    }
    const data = await apiPost('/api/process/reset', {
        processId: process.processId,
        cleanupRemote: true,
    });
    showToastMessage(data.msg || '流程已重置。');
    clearWashTransientState();
    state.wash.view = 'home';
    renderWash();
    renderGlobalRefresh();
    scheduleActiveAutoRefresh();
    pushHistoryState();
    await refreshOrdersOverview(true);
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
        await showAlertDialog('请先选择预约模式。');
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
            await showAlertDialog('请先选择扫码机组。');
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
            await showAlertDialog('请先选择洗衣房和洗衣机。');
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

    const action = button.dataset.action;
    if (action === 'continue-reservation-process') {
        if (!ensureTokenReady()) {
            return;
        }
        try {
            switchTab('washTab', { pushHistory: false, refresh: false });
            await openProcess(button.dataset.processId);
        } catch (error) {
            handleRequestError(error, '恢复预约流程失败，请稍后重试。');
        }
        return;
    }

    const taskId = button.dataset.taskId;
    if (!taskId) {
        return;
    }

    try {
        if (action === 'pause-reservation') {
            const data = await apiPost(`/api/reservations/${taskId}/pause`, {});
            showToastMessage(data.msg || '预约任务已暂停。');
            await loadReservations();
            return;
        }

        if (action === 'resume-reservation') {
            const data = await apiPost(`/api/reservations/${taskId}/resume`, {});
            showToastMessage(data.msg || '预约任务已恢复。');
            await loadReservations();
            return;
        }

        if (action !== 'delete-reservation') {
            return;
        }

        if (!(await showConfirmDialog('确定删除这个预约任务吗？'))) {
            return;
        }
        const data = await apiDelete(`/api/reservations/${taskId}`);
        showToastMessage(data.msg || '预约任务已删除。');
        await loadReservations();
    } catch (error) {
        handleRequestError(error, '预约操作失败，请稍后重试。');
    }
}

async function handleOrderClick(event) {
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

    if (action === 'continue-process') {
        switchTab('washTab', { pushHistory: false, refresh: false });
        await openProcess(button.dataset.processId);
        return;
    }
    if (action === 'abandon-process') {
        if (!(await showConfirmDialog('确定放弃这个待继续流程吗？'))) {
            return;
        }
        const data = await apiPost('/api/process/reset', {
            processId: button.dataset.processId,
            cleanupRemote: true,
        });
        showToastMessage(data.msg || '流程已放弃。');
        await refreshOrdersOverview(true);
        return;
    }

    const orderNo = button.dataset.orderNo;
    if (!orderNo) {
        return;
    }

    if (action === 'toggle-order-detail') {
        const expanded = Boolean(state.orders.expanded[orderNo]);
        state.orders.expanded[orderNo] = !expanded;
        renderOrders();
        if (!expanded) {
            try {
                updateOrderFromDetail(orderNo, await getOrderDetail(orderNo));
                renderOrders();
            } catch (error) {
                handleRequestError(error, '读取订单详情失败。', true);
            }
        }
        return;
    }

    if (action === 'finish-order') {
        if (!(await showConfirmDialog(`确定结束订单 ${orderNo} 吗？`))) {
            return;
        }
        const data = await apiPost(`/api/orders/${encodeURIComponent(orderNo)}/finish`, {});
        showToastMessage(data.msg || '订单已结束。');
        applyOrderDetail(orderNo, data.order);
        await refreshSingleOrder(orderNo, data.order);
        await loadActiveProcesses();
        return;
    }

    if (!(await showConfirmDialog(`确定取消订单 ${orderNo} 吗？`))) {
        return;
    }
    let cancelError = null;
    let cancelData = null;
    try {
        cancelData = await apiPost(`/api/orders/${encodeURIComponent(orderNo)}/cancel`, {});
        showToastMessage(cancelData.msg || '订单已取消。');
        applyOrderDetail(orderNo, cancelData.order);
    } catch (error) {
        cancelError = error;
    }
    await refreshSingleOrder(orderNo);
    await loadActiveProcesses();
    if (cancelError) {
        handleRequestError(cancelError, '取消订单失败。', true);
    }
}

async function refreshSingleOrder(orderNo, initialDetail = null) {
    if (initialDetail) {
        applyOrderDetail(orderNo, initialDetail);
    } else {
        try {
            applyOrderDetail(orderNo, await getOrderDetail(orderNo, true));
        } catch (error) {
            handleRequestError(error, '刷新订单详情失败。', true);
        }
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
    item.pageCode = detail.pageCode;
    item.price = detail.price;
    item.payTime = detail.payTime;
    item.completeTime = detail.completeTime;
    item.finishTime = detail.finishTime;
    item.invalidTime = detail.invalidTime;
    item.buttonSwitch = detail.buttonSwitch || item.buttonSwitch;
    item.machineName = detail.machineName || item.machineName;
    item.modeName = detail.modeName || item.modeName;
    item.shopName = detail.shopName || item.shopName;
}

async function handleSettingsSubmit(event) {
    event.preventDefault();

    const payload = {
        token: el.settingsToken.value.trim(),
        pushplusUrl: el.settingsPushplusUrl.value.trim(),
        defaultLeadMinutes: Number(el.settingsLeadMinutes.value || 60),
        reservationPollIntervalSeconds: Number(el.settingsPollInterval.value || 30),
    };

    try {
        const data = await apiPut('/api/settings', payload);
        state.settings = data.settings || state.settings;
        state.scheduler = data.scheduler || state.scheduler;
        applyTokenStatus(data.tokenStatus || state.tokenStatus, true);
        renderWash();
        renderReservations();
        renderOrders();
        renderSettings();
        showToastMessage(data.msg || '设置已保存。');
        await loadReservations();

        if (isTokenReady()) {
            await loadLaundrySections({ showToast: false, preserveView: false });
            await hydrateReservationForm();
            await refreshOrdersOverview(true);
        } else {
            renderWash();
            renderOrders();
        }
    } catch (error) {
        handleRequestError(error, '保存设置失败。', true);
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

function fillSelect(select, items, placeholder, selectedValue = '') {
    const options = [`<option value="">${escapeHtml(placeholder)}</option>`]
        .concat((items || []).map(item => `<option value="${escapeHtml(String(item.value))}">${escapeHtml(item.label)}</option>`));
    select.innerHTML = options.join('');
    if (selectedValue) {
        select.value = selectedValue;
    }
}

function getSelectedValue(id) {
    const select = document.getElementById(id);
    return select ? select.value : '';
}

function parseDateValue(value) {
    if (!value) {
        return null;
    }
    if (value instanceof Date) {
        return Number.isNaN(value.getTime()) ? null : value;
    }
    if (typeof value === 'number') {
        const timestamp = value > 10000000000 ? value : value * 1000;
        const date = new Date(timestamp);
        return Number.isNaN(date.getTime()) ? null : date;
    }
    const text = String(value).trim();
    if (!text) {
        return null;
    }
    const candidates = [text, text.replace(' ', 'T')];
    for (const candidate of candidates) {
        const date = new Date(candidate);
        if (!Number.isNaN(date.getTime())) {
            return date;
        }
    }
    return null;
}

function formatCountdown(targetTime, expiredLabel = '已到期') {
    const target = parseDateValue(targetTime);
    if (!target) {
        return '--';
    }
    const diff = target.getTime() - Date.now();
    if (diff <= 0) {
        return expiredLabel;
    }

    const totalSeconds = Math.floor(diff / 1000);
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (days > 0) {
        return `${days}天 ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function isPendingOrder(order) {
    const stateCode = Number(order && order.state);
    const stateDesc = String((order && order.stateDesc) || '');
    const pageCode = String((order && order.pageCode) || '');
    const buttonSwitch = (order && order.buttonSwitch) || {};
    return stateCode === 50
        || Boolean(buttonSwitch.canPay)
        || stateDesc.includes('待支付')
        || stateDesc.includes('待验证')
        || ['waiting_check', 'place_clothes', 'waiting_choose_ump'].includes(pageCode);
}

function isRunningOrder(order) {
    const stateCode = Number(order && order.state);
    const stateDesc = String((order && order.stateDesc) || '');
    return stateCode === 500
        || stateDesc.includes('进行中')
        || stateDesc.includes('洗衣中')
        || stateDesc.includes('烘干中')
        || stateDesc.includes('脱水中');
}

function isCompletedOrder(order) {
    const stateCode = Number(order && order.state);
    const stateDesc = String((order && order.stateDesc) || '');
    return stateCode === 1000 || stateDesc.includes('完成');
}

function getOrderTimeMeta(order) {
    if (!order) {
        return null;
    }
    if (isCompletedOrder(order)) {
        return {
            label: '完成时间',
            value: order.completeTime,
            countdownLabel: '当前阶段',
            countdownText: '已完成',
        };
    }
    if (isRunningOrder(order)) {
        return {
            label: '预计完成时间',
            value: order.finishTime,
            countdownLabel: '当前阶段倒计时',
            countdownText: formatCountdown(order.finishTime, '已完成'),
        };
    }
    if (isPendingOrder(order)) {
        return {
            label: '失效时间',
            value: order.invalidTime,
            countdownLabel: '当前阶段倒计时',
            countdownText: formatCountdown(order.invalidTime, '已失效'),
        };
    }
    if (order.finishTime) {
        return {
            label: '预计完成时间',
            value: order.finishTime,
            countdownLabel: '当前阶段倒计时',
            countdownText: formatCountdown(order.finishTime, '已完成'),
        };
    }
    if (order.completeTime) {
        return {
            label: '完成时间',
            value: order.completeTime,
            countdownLabel: '当前阶段',
            countdownText: '已完成',
        };
    }
    if (order.invalidTime) {
        return {
            label: '失效时间',
            value: order.invalidTime,
            countdownLabel: '当前阶段倒计时',
            countdownText: formatCountdown(order.invalidTime, '已失效'),
        };
    }
    return null;
}

function renderOrderTimeGrid(order) {
    const meta = getOrderTimeMeta(order);
    if (!meta) {
        return '';
    }
    return `
        <div class="detail-grid">
            <div><span>${escapeHtml(meta.label)}</span><span>${escapeHtml(formatDateTime(meta.value))}</span></div>
            <div><span>${escapeHtml(meta.countdownLabel)}</span><span>${escapeHtml(meta.countdownText)}</span></div>
        </div>
    `;
}

function machineChipClass(machine) {
    const label = String(machine.statusLabel || machine.stateDesc || '');
    if (label.includes('运行')) {
        return 'warning';
    }
    if (label.includes('空闲')) {
        return 'success';
    }
    return 'danger';
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
    const date = parseDateValue(value);
    if (!date) {
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
    if (value.length <= 8) {
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
    return error && error.payload ? error.payload : null;
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
    } catch (error) {
        const parseError = new Error(`响应解析失败：${error.message}`);
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
