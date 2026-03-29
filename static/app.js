const TAB_TITLES = {
    washTab: '洗衣',
    reservationTab: '预约',
    orderTab: '订单',
    settingsTab: '设置',
};

const PROCESS_STEPS = [
    { id: 1, label: '创建到待付款', hint: '解析机器、创建订单并确认放衣，直到进入待付款状态', backendSteps: [1, 2, 3] },
    { id: 2, label: '支付并启动', hint: '生成支付参数并完成支付启动', backendSteps: [4, 5] },
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
const BOOTSTRAP = window.__APP_BOOTSTRAP__ || {};
const BASE_PATH = normalizeBasePath(BOOTSTRAP.basePath || '');

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
        homeLoaded: false,
        rooms: [],
        scanMachines: [],
        view: 'home',
        machineHostView: null,
        roomId: null,
        roomCategoryCode: '',
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
        composerOpen: false,
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
        pendingRefreshRequest: null,
        autoRefreshTimer: null,
        favoriteStatusRequestId: 0,
        roomRequestId: 0,
    },
};

const cache = {
    roomMachines: new Map(),
    machineDetails: new Map(),
    scanModes: new Map(),
    scanMachineStatus: new Map(),
    orderDetails: new Map(),
};

const el = {};

function normalizeBasePath(value) {
    const raw = String(value || '').trim();
    if (!raw || raw === '/') {
        return '';
    }
    return `/${raw.replace(/^\/+|\/+$/g, '')}`;
}

function withBasePath(path) {
    const raw = String(path || '');
    if (!BASE_PATH || !raw || /^[a-z]+:\/\//i.test(raw) || raw.startsWith('//')) {
        return raw;
    }
    if (raw === BASE_PATH || raw.startsWith(`${BASE_PATH}/`)) {
        return raw;
    }
    if (raw.startsWith('/')) {
        return `${BASE_PATH}${raw}`;
    }
    return `${BASE_PATH}/${raw.replace(/^\/+/, '')}`;
}

function displayProcessStepId(stepId) {
    const numericStep = Number(stepId || 0);
    const displayStep = PROCESS_STEPS.find(step => (step.backendSteps || [step.id]).includes(numericStep));
    if (displayStep) {
        return displayStep.id;
    }
    return numericStep;
}

function resolveProcessDisplayStep(process) {
    const currentStep = Number((process || {}).currentStep || 0);
    return PROCESS_STEPS.find(step => (step.backendSteps || [step.id]).includes(currentStep)) || null;
}

function processDisplayStepLabel(process) {
    if ((process || {}).completed) {
        return '已完成';
    }
    const step = resolveProcessDisplayStep(process);
    if (step) {
        return step.label;
    }
    return (process || {}).currentStepLabel || '待执行';
}

function normalizeProcessToastMessage(message) {
    const text = String(message || '').trim();
    if (!text) {
        return '流程已推进。';
    }
    if (text.startsWith('第一阶段完成：')) {
        return '已到待付款。';
    }
    if (text.startsWith('第二阶段完成：')) {
        return '付款成功，设备已启动。';
    }
    return text;
}

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
        const configPromise = loadConfig();
        const settingsPromise = loadSettings();
        const reservationsPromise = loadReservations({ rethrow: true });

        await configPromise;
        const laundryPromise = isTokenReady()
            ? loadLaundrySections({ showToast: false, preserveView: false })
            : Promise.resolve();

        await Promise.all([
            settingsPromise,
            reservationsPromise,
            laundryPromise,
        ]);
        if (isTokenReady()) {
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
    el.topRefreshStatus = document.getElementById('topRefreshStatus');
    el.tabPanels = Array.from(document.querySelectorAll('.tab-panel'));
    el.tabButtons = Array.from(document.querySelectorAll('.tabbar-btn'));
    el.toast = document.getElementById('toast');
    el.appDialog = document.getElementById('appDialog');
    el.appDialogTitle = document.getElementById('appDialogTitle');
    el.appDialogMessage = document.getElementById('appDialogMessage');
    el.appDialogCancel = document.getElementById('appDialogCancel');
    el.appDialogConfirm = document.getElementById('appDialogConfirm');

    el.washView = document.getElementById('washView');

    el.reservationTab = document.getElementById('reservationTab');
    el.reservationComposer = document.getElementById('reservationComposer');
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
}

function bindEvents() {
    el.tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            openTabRoot(button.dataset.tab).catch(error => {
                handleRequestError(error, '切换页面失败。', true);
            });
        });
    });

    el.washView.addEventListener('click', handleWashClick);
    el.washView.addEventListener('keydown', handleClickableCardKeydown);
    el.reservationTab.addEventListener('click', handleReservationClick);
    el.ordersList.addEventListener('click', handleOrderClick);
    el.ordersList.addEventListener('keydown', handleClickableCardKeydown);
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

function handleClickableCardKeydown(event) {
    if (event.defaultPrevented || (event.key !== 'Enter' && event.key !== ' ')) {
        return;
    }
    const card = event.target.closest('[data-clickable-card="true"]');
    if (!card || event.target !== card) {
        return;
    }
    event.preventDefault();
    card.click();
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
        return '未刷新';
    }
    const date = parseDateValue(value);
    if (!date) {
        return '未刷新';
    }
    return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function renderGlobalRefresh() {
    if (!el.topRefreshStatus) {
        return;
    }
    const refreshState = getActiveRefreshState();
    const tabLabel = TAB_TITLES[state.activeTab] || '当前页';
    const lastUpdatedText = formatRefreshTimestamp(refreshState.lastRefreshedAt);
    el.topRefreshStatus.textContent = refreshState.loading
        ? `${tabLabel} 刷新中 · ${lastUpdatedText}`
        : `${tabLabel} ${lastUpdatedText === '未刷新' ? '未刷新' : `上次刷新 ${lastUpdatedText}`}`;
    el.topRefreshStatus.classList.toggle('loading', Boolean(refreshState.loading));
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
    if ((currentView === 'room' || currentView === 'machine') && (state.wash.roomId || state.wash.machineId)) {
        const selectedMode = getSelectedValue('washModeSelect');
        const shouldRefreshRoom = currentView === 'room' || (currentView === 'machine' && state.wash.machineHostView !== 'home');
        const roomPromise = shouldRefreshRoom && state.wash.roomId
            ? getRoomMachines(state.wash.roomId, { forceRefresh: true, categoryCode: state.wash.roomCategoryCode || '' })
            : Promise.resolve(null);
        const machinePromise = state.wash.machineId
            ? getMachineDetail(state.wash.machineId, { forceRefresh: true })
            : Promise.resolve(null);
        const [roomData, machineDetail] = await Promise.all([roomPromise, machinePromise]);
        if (roomData) {
            state.wash.roomData = roomData;
        }
        if (machineDetail) {
            state.wash.machineDetail = machineDetail;
            const machineRoomId = String((state.wash.machineDetail || {}).shopId || '').trim();
            if (shouldRefreshRoom && !state.wash.roomId && machineRoomId) {
                state.wash.roomId = machineRoomId;
                state.wash.roomCategoryCode = state.wash.roomCategoryCode || String((state.wash.machineDetail || {}).categoryCode || '').trim();
                state.wash.roomData = await getRoomMachines(machineRoomId, {
                    forceRefresh: true,
                    categoryCode: state.wash.roomCategoryCode || '',
                });
            }
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
        const [modes, linkedStatus] = await Promise.all([
            getScanModes(state.wash.scanMachine.qrCode, { forceRefresh: true }),
            getScanMachineStatus(state.wash.scanMachine.qrCode, { forceRefresh: true }),
        ]);
        state.wash.scanMachine = {
            ...(localMachine || state.wash.scanMachine),
            modes,
            linkedStatus,
        };
        renderWash();
        restoreSelectValue('scanModeSelect', selectedMode);
        return;
    }

    await loadLaundrySections({ showToast: false, preserveView: reason !== 'manual' });
    await hydrateReservationForm();
}

async function refreshActiveTab({ reason = 'manual', silent = false, force = false } = {}) {
    const tabId = state.activeTab;
    if (state.ui.activeRefreshPromise) {
        if (force) {
            state.ui.pendingRefreshRequest = { tabId, reason, silent };
        }
        return state.ui.activeRefreshPromise;
    }

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
            const pendingRequest = state.ui.pendingRefreshRequest;
            if (pendingRequest) {
                state.ui.pendingRefreshRequest = null;
            }
            if (pendingRequest && pendingRequest.tabId === state.activeTab) {
                refreshActiveTab({ ...pendingRequest, force: false }).catch(error => {
                    handleRequestError(error, '刷新页面失败。', true);
                });
            }
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
    if (state.wash.roomCategoryCode) {
        payload.roomCategoryCode = state.wash.roomCategoryCode;
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
            await openRoom(navState.roomId, { pushHistory: false, roomCategoryCode: navState.roomCategoryCode || '' });
            if (navState.machineId) {
                await openMachine(navState.machineId, {
                    pushHistory: false,
                    roomId: navState.roomId || null,
                    roomCategoryCode: navState.roomCategoryCode || '',
                });
            }
            return;
        }
        if (washView === 'machine' && navState.machineId) {
            await openMachine(navState.machineId, {
                pushHistory: false,
                roomId: navState.roomId || null,
                roomCategoryCode: navState.roomCategoryCode || '',
            });
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

    if (safeTab !== 'reservationTab') {
        setReservationComposerOpen(false);
        clearReservationAutoRefresh();
    }

    if (state.historyReady && options.refresh !== false && previousTab !== safeTab) {
        refreshActiveTab({ reason: 'tab-switch', silent: true, force: true }).catch(error => {
            handleRequestError(error, '刷新页面失败。', true);
        });
    }

    scheduleActiveAutoRefresh();
}

function scrollToPageTop() {
    if (typeof window === 'undefined' || typeof window.scrollTo !== 'function') {
        return;
    }
    window.scrollTo({ top: 0, left: 0 });
}

function resetWashTabToRoot() {
    clearWashTransientState();
    state.wash.view = 'home';
    state.wash.loading = false;
}

function resetOrderTabToRoot() {
    state.orders.expanded = {};
}

function shouldPushRootHistory(previousTab, nextTab) {
    if (!state.historyReady) {
        return false;
    }
    if (previousTab !== nextTab) {
        return true;
    }
    if (nextTab === 'washTab' && state.wash.view !== 'home') {
        return true;
    }
    return false;
}

function resetTabToRoot(tabId) {
    if (tabId === 'washTab') {
        resetWashTabToRoot();
        renderWash();
        return;
    }
    if (tabId === 'orderTab') {
        resetOrderTabToRoot();
        renderOrders();
        return;
    }
    if (tabId === 'reservationTab') {
        setReservationComposerOpen(false);
        renderReservations();
        return;
    }
    if (tabId === 'settingsTab') {
        renderSettings();
    }
}

async function openTabRoot(tabId) {
    const safeTab = VALID_TABS.has(tabId) ? tabId : 'washTab';
    const previousTab = state.activeTab;
    const shouldPushHistory = shouldPushRootHistory(previousTab, safeTab);

    switchTab(safeTab, { pushHistory: false, refresh: false });
    resetTabToRoot(safeTab);
    renderGlobalRefresh();
    scrollToPageTop();

    if (shouldPushHistory) {
        pushHistoryState();
    } else {
        replaceHistoryState();
    }

    await refreshActiveTab({ reason: 'tab-root', silent: true, force: true });
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

function updateReservationComposerVisibility() {
    if (!el.reservationComposer) {
        return;
    }
    const open = Boolean(state.reservations.composerOpen);
    el.reservationComposer.classList.toggle('hidden', !open);
    el.reservationComposer.setAttribute('aria-hidden', open ? 'false' : 'true');
}

function setReservationComposerOpen(open) {
    state.reservations.composerOpen = Boolean(open);
    updateReservationComposerVisibility();
}

function getFavoriteMachinesFromPayload(data) {
    const favorites = data && Array.isArray(data.favorites) ? data.favorites : null;
    if (favorites) {
        return favorites;
    }
    return data && Array.isArray(data.scanMachines) ? data.scanMachines : [];
}

function mergeFavoriteStatuses(scanMachines) {
    const machines = Array.isArray(scanMachines) ? scanMachines : [];
    const previousStatuses = new Map(
        (state.wash.scanMachines || []).map(machine => [machine.qrCode, machine.linkedStatus || null])
    );
    return machines.map(machine => ({
        ...machine,
        linkedStatus: previousStatuses.get(machine.qrCode) || cache.scanMachineStatus.get(machine.qrCode) || null,
    }));
}

function applyFavoriteStatuses(items) {
    const statuses = Array.isArray(items) ? items : [];
    const byQrCode = new Map(
        statuses
            .filter(item => item && item.qrCode)
            .map(item => [
                item.qrCode,
                {
                    matched: Boolean(item.matched),
                    room: item.room || null,
                    machine: item.machine || null,
                },
            ])
    );
    byQrCode.forEach((status, qrCode) => {
        cache.scanMachineStatus.set(qrCode, status);
    });
    state.wash.scanMachines = (state.wash.scanMachines || []).map(machine => ({
        ...machine,
        linkedStatus: byQrCode.has(machine.qrCode)
            ? byQrCode.get(machine.qrCode)
            : (machine.linkedStatus || cache.scanMachineStatus.get(machine.qrCode) || null),
    }));
    if (state.wash.scanMachine && state.wash.scanMachine.qrCode && byQrCode.has(state.wash.scanMachine.qrCode)) {
        state.wash.scanMachine = {
            ...state.wash.scanMachine,
            linkedStatus: byQrCode.get(state.wash.scanMachine.qrCode),
        };
    }
}

async function fetchFavoriteStatuses(options = {}) {
    const { forceRefresh = false } = options;
    const suffix = forceRefresh ? '?force=1' : '';
    const data = await apiGet(`/api/laundry/favorites/statuses${suffix}`);
    return data.items || [];
}

async function refreshFavoriteStatusesInBackground(options = {}) {
    const { forceRefresh = false, renderWhenDone = true, silent = true } = options;
    const requestId = ++state.ui.favoriteStatusRequestId;
    if (!(state.wash.scanMachines || []).length) {
        return [];
    }
    try {
        const items = await fetchFavoriteStatuses({ forceRefresh });
        if (requestId !== state.ui.favoriteStatusRequestId) {
            return [];
        }
        applyFavoriteStatuses(items);
        if (renderWhenDone) {
            renderWash();
        }
        return items;
    } catch (error) {
        if (!silent) {
            syncTokenFailure(error);
            handleRequestError(error, '读取收藏设备状态失败。', true);
        }
        return [];
    }
}

async function syncFavoriteMachines(scanMachines, options = {}) {
    const { hydrateStatus = false, forceRefresh = false } = options;
    state.wash.scanMachines = mergeFavoriteStatuses(scanMachines);
    populateReservationScanOptions();
    if (hydrateStatus && state.wash.scanMachines.length) {
        await refreshFavoriteStatusesInBackground({ forceRefresh, renderWhenDone: false, silent: true });
    }
    return state.wash.scanMachines;
}

async function loadConfig() {
    let loaded = false;
    try {
        const data = await apiGet('/api/config');
        state.security = data.security || null;
        state.scheduler = data.scheduler || null;
        await syncFavoriteMachines(getFavoriteMachinesFromPayload(data));
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
    const shouldBlock = !state.wash.homeLoaded && !(state.wash.rooms || []).length && !(state.wash.scanMachines || []).length;
    state.wash.loading = shouldBlock;
    if (!preserveView) {
        state.wash.view = 'home';
    }
    if (shouldBlock) {
        renderWashLoading('正在加载洗衣房和收藏...');
    } else {
        renderWash();
    }

    try {
        const data = await apiGet('/api/laundry/sections');
        state.wash.rooms = data.rooms || [];
        await syncFavoriteMachines(getFavoriteMachinesFromPayload(data));
        state.wash.homeLoaded = true;
        state.wash.loading = false;
        if (!preserveView) {
            clearWashTransientState();
            state.wash.view = 'home';
        }
        markTabRefreshed('washTab');
        renderWash();
        refreshFavoriteStatusesInBackground({ forceRefresh: true, renderWhenDone: true, silent: true });
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
    if (state === 401 || state === 411 || state === 1000) {
        return true;
    }
    return stateDesc.includes('已取消') || stateDesc.includes('已完成') || stateDesc.includes('订单超时关闭');
}

function shouldRefreshHistoryOrderDetail(order) {
    if (!order || !order.orderNo) {
        return false;
    }
    if (!isTerminalHistoryOrder(order)) {
        return true;
    }
    const cachedDetail = cache.orderDetails.get(order.orderNo);
    return Boolean(cachedDetail && !isTerminalHistoryOrder(cachedDetail));
}

function dropStaleTerminalOrderDetail(order) {
    if (!order || !order.orderNo || !isTerminalHistoryOrder(order)) {
        return;
    }
    const cachedDetail = cache.orderDetails.get(order.orderNo);
    if (cachedDetail && !isTerminalHistoryOrder(cachedDetail)) {
        cache.orderDetails.delete(order.orderNo);
    }
}

function shouldUseHistoryOrderOverlay(order, overlay) {
    if (!overlay) {
        return false;
    }
    return !(isTerminalHistoryOrder(order) && !isTerminalHistoryOrder(overlay));
}

async function refreshVisibleOrderDetails(orders) {
    const candidates = (orders || []).filter(shouldRefreshHistoryOrderDetail);
    if (!candidates.length) {
        return;
    }

    const results = await Promise.allSettled(candidates.map(order => getOrderDetail(order.orderNo, true)));
    results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
            updateOrderFromDetail(candidates[index].orderNo, result.value);
        } else {
            dropStaleTerminalOrderDetail(candidates[index]);
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

function buildRoomMachinesCacheKey(positionId, categoryCode = '') {
    return `${String(positionId || '').trim()}::${String(categoryCode || '').trim()}`;
}

async function getRoomMachines(positionId, options = {}) {
    const { forceRefresh = false, categoryCode = '' } = options;
    const cacheKey = buildRoomMachinesCacheKey(positionId, categoryCode);
    if (!forceRefresh && cache.roomMachines.has(cacheKey)) {
        return cache.roomMachines.get(cacheKey);
    }
    const query = new URLSearchParams();
    if (categoryCode) {
        query.set('categoryCode', categoryCode);
    }
    if (forceRefresh) {
        query.set('force', '1');
    }
    const url = `/api/laundry/rooms/${encodeURIComponent(positionId)}/machines${query.toString() ? `?${query.toString()}` : ''}`;
    const data = await apiGet(url);
    cache.roomMachines.set(cacheKey, data);
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

async function getScanMachineStatus(qrCode, options = {}) {
    const { forceRefresh = false } = options;
    if (!forceRefresh && cache.scanMachineStatus.has(qrCode)) {
        return cache.scanMachineStatus.get(qrCode);
    }
    const data = await apiGet(`/api/laundry/scan-machines/${encodeURIComponent(qrCode)}/status`);
    const result = {
        matched: Boolean(data.matched),
        room: data.room || null,
        machine: data.machine || null,
    };
    cache.scanMachineStatus.set(qrCode, result);
    return result;
}

async function addFavoriteMachine(machine) {
    const data = await apiPost('/api/laundry/favorites', {
        label: machine.name || machine.label || '收藏设备',
        qrCode: machine.scanCode || machine.qrCode || machine.code || '',
        goodsId: machine.goodsId || '',
        shopId: machine.shopId || '',
        shopName: machine.shopName || '',
        categoryCode: machine.categoryCode || '',
        categoryName: machine.categoryName || '',
    });
    return getFavoriteMachinesFromPayload(data);
}

async function removeFavoriteMachine(qrCode) {
    const data = await apiDelete(`/api/laundry/favorites/${encodeURIComponent(qrCode)}`);
    return getFavoriteMachinesFromPayload(data);
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
        '请选择收藏机器'
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
        fillSelect(el.reservationMachine, [], '收藏机器无需额外选择设备');
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
        const machines = payload.machines || [];
        state.reservations.roomMachines = machines;
        fillSelect(
            el.reservationMachine,
            machines.map(machine => ({
                value: machine.goodsId,
                label: `${machine.name} · ${machine.statusDetail || machine.stateDesc}`,
            })),
            machines.length ? '请选择洗衣机' : '当前洗衣房暂无机器'
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
        if (!detail.supportsVirtualScan || !detail.scanCode) {
            state.reservations.modeOptions = [];
            fillSelect(el.reservationMode, [], '当前设备暂不支持扫码下单');
            return;
        }
        populateReservationModeOptions(detail.modes || []);
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
    state.wash.machineHostView = null;
    state.wash.roomId = null;
    state.wash.roomCategoryCode = '';
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

function dedupeCompactTextList(items) {
    const seen = new Set();
    return (items || [])
        .map(item => String(item || '').trim())
        .filter(Boolean)
        .filter(item => {
            const key = normalizeComparableText(item);
            if (!key || seen.has(key)) {
                return false;
            }
            seen.add(key);
            return true;
        });
}

function textAppearsInMessages(messages, value) {
    const target = normalizeComparableText(value);
    if (!target) {
        return false;
    }
    return (messages || []).some(message => normalizeComparableText(message).includes(target));
}

function renderMetaPill(label, value) {
    const text = String(value == null ? '' : value).trim();
    if (!text || text === '--') {
        return '';
    }
    return `<span class="meta-pill">${escapeHtml(label)}<strong>${escapeHtml(text)}</strong></span>`;
}

function renderCardIcon() {
    return '<span class="card-icon" aria-hidden="true">></span>';
}

function weekdayLabel(value) {
    const labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
    const index = Number(value);
    return Number.isInteger(index) && index >= 0 && index < labels.length ? labels[index] : '--';
}

function reservationScheduleText(task) {
    if ((task || {}).scheduleType === 'weekly') {
        const timeText = String((task || {}).timeOfDay || '').trim() || '--';
        return `每${weekdayLabel((task || {}).weekday)} ${timeText} · 下次 ${formatDateTime((task || {}).targetTime)}`;
    }
    return `单次 · ${formatDateTime((task || {}).targetTime)}`;
}

function getBrowserTimeZone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (error) {
        return '';
    }
}

function renderWashLoading(message) {
    el.washView.innerHTML = `<div class="panel-card loading-card">${escapeHtml(message)}</div>`;
}

function renderWash() {
    if (state.bootstrap.configLoading || state.wash.loading) {
        renderWashLoading('正在加载洗衣房和收藏...');
        return;
    }

    if (!isTokenReady()) {
        el.washView.innerHTML = renderStatusCard('warning', '需要可用 Token', getTokenAlertMessage(), `
            <div class="spacer-sm"></div>
            <button class="btn btn-primary full-width" type="button" data-action="goto-settings">去设置</button>
        `);
        return;
    }

    if (state.wash.view === 'machine') {
        if (state.wash.machineHostView === 'home' || !state.wash.roomData) {
            el.washView.innerHTML = renderWashHomeView();
            return;
        }
        el.washView.innerHTML = renderWashRoomView();
        return;
    }
    if (state.wash.view === 'room') {
        el.washView.innerHTML = renderWashRoomView();
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
                    <h3>收藏</h3>
                </div>
                <div class="compact-summary-side">
                    <span class="chip warning">${scanMachines.length} 台</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="scan-list">
                ${scanMachines.length ? scanMachines.map(renderScanMachineCard).join('') : renderEmptyState('暂无收藏机器', '请先在机器详情页点击星号收藏。')}
            </div>
        </section>
        ${state.wash.machineHostView === 'home' ? renderWashMachineView() : ''}
    `;
}

function renderWashRoomView() {
    const payload = state.wash.roomData || {};
    const room = payload.room || {};
    const machines = payload.machines || [];
    const categories = getRoomCategoryOptions(payload, machines);
    const selectedCategoryCode = resolveSelectedRoomCategoryCode(categories);
    const totalMachines = categories.length ? Number((categories[0] || {}).total || machines.length) : machines.length;
    const filteredMachines = selectedCategoryCode
        ? machines.filter(machine => String(machine.categoryCode || '') === selectedCategoryCode)
        : machines;
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
                    <span class="chip pending">${totalMachines} 台</span>
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
                <div class="compact-summary-side">
                    <span class="chip pending">${filteredMachines.length} 台</span>
                </div>
            </div>
            ${renderRoomCategoryMenu(categories, selectedCategoryCode)}
            <div class="spacer-sm"></div>
            <div class="machine-list">
                ${filteredMachines.length ? filteredMachines.map(renderRoomMachineCard).join('') : renderEmptyState('暂无设备', '当前分类下没有可显示的设备。')}
            </div>
        </section>
        ${renderWashMachineView()}
    `;
}

function renderWashMachineView() {
    const machine = state.wash.machineDetail;
    if (!machine) {
        return '';
    }

    const locationText = machine.shopName || machine.shopAddress || '暂无洗衣房信息';
    const subtitle = uniqueSubtitle(machine.name || '设备', locationText);
    const modeOptions = (machine.modes || []).map(mode => ({
        value: String(mode.id),
        label: `${mode.label} · ${mode.price} 元`,
    }));
    return `
        <div class="wash-machine-modal" aria-hidden="false">
            <button class="wash-machine-modal-backdrop" type="button" data-action="close-machine-modal" aria-label="关闭设备详情"></button>
            <section class="wash-machine-modal-panel panel-card" role="dialog" aria-modal="true" aria-label="${escapeHtml(machine.name || '设备详情')}">
                <div class="wash-machine-modal-toolbar">
                    <button class="btn btn-light" type="button" data-action="close-machine-modal">关闭</button>
                    <button class="btn btn-light" type="button" data-action="back-home">返回洗衣首页</button>
                </div>
                <div class="spacer-sm"></div>
                <div class="compact-summary">
                    <div class="compact-main">
                        <div class="machine-detail-title-row">
                            <h3>${escapeHtml(machine.name || '设备')}</h3>
                            <button
                                class="favorite-toggle${machine.isFavorite ? ' active' : ''}"
                                type="button"
                                data-action="toggle-favorite"
                                aria-pressed="${machine.isFavorite ? 'true' : 'false'}"
                            >
                                <span class="favorite-toggle-icon">${machine.isFavorite ? '★' : '☆'}</span>
                                <span>${machine.isFavorite ? '已收藏' : '收藏'}</span>
                            </button>
                        </div>
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
                ${machine.supportsVirtualScan && machine.scanCode ? `
                    <div class="spacer-sm"></div>
                    <div class="action-row">
                        <button class="btn btn-primary" type="button" data-action="open-machine-scan">扫码下单（手动流程）</button>
                    </div>
                ` : ''}
                ${renderWashResult()}
            </section>
        </div>
    `;
}

function renderWashScanView() {
    const machine = state.wash.scanMachine;
    if (!machine) {
        return renderEmptyState('扫码设备未找到', '请返回洗衣首页重新选择收藏设备。');
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
                    <h3>${escapeHtml(machine.label || '收藏设备')}</h3>
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
        return renderEmptyState('流程不存在', '请从收藏设备重新开始，或到订单页继续已有流程。');
    }

    const currentStep = Number(process.currentStep || 1);
    const displayStep = displayProcessStepId(currentStep) || 1;
    const displayStepLabel = processDisplayStepLabel(process);
    const order = process.order || null;
    const orderNo = process.orderNo || ((process.contextSummary || {}).orderNo) || '';
    const stepList = PROCESS_STEPS.map(step => {
        let className = 'pending';
        let text = '待执行';
        if (process.completed || displayStep > step.id) {
            className = 'success';
            text = '完成';
        } else if (!process.completed && !process.terminated && displayStep === step.id) {
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
            : renderStatusCard('warning', '手动扫码流程', `当前步骤：${displayStepLabel}`);

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
                ${!process.completed && !process.terminated ? `<button class="btn btn-primary" type="button" data-action="next-process">下一步：${escapeHtml(displayStepLabel || '继续')}</button>` : ''}
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
        updateReservationComposerVisibility();
        return;
    }

    const items = state.reservations.items || [];
    const scheduler = state.scheduler || {};
    el.reservationList.innerHTML = `
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>新建预约</h3>
                    <p>收藏机器和洗衣房机器都可以在弹层里创建预约。</p>
                </div>
                <div class="compact-summary-side">
                    <button class="btn btn-primary" type="button" data-action="open-reservation-composer">新建预约</button>
                </div>
            </div>
        </section>
        <section class="panel-card">
            <div class="compact-summary">
                <div class="compact-main">
                    <h3>预约任务</h3>
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
    `;
    updateReservationComposerVisibility();
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
                </div>
                <div class="compact-summary-side">
                    <span class="chip pending">${state.orders.total || items.length} 条</span>
                </div>
            </div>
            <div class="spacer-sm"></div>
            <div class="order-list order-history-list">
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
        <article
            class="list-card row-card clickable-card"
            role="button"
            tabindex="0"
            data-clickable-card="true"
            data-action="open-room"
            data-room-id="${escapeHtml(room.id)}"
            aria-label="查看${escapeHtml(room.name || '洗衣房')}设备"
        >
            <div class="row-card-main">
                <div class="row-card-head">
                    <h4>${escapeHtml(room.name || '洗衣房')}</h4>
                    <div class="row-card-pills">
                        <span class="chip info">${escapeHtml(stringOrFallback(room.idleCount, '--'))} 空闲</span>
                        <span class="chip ${room.enableReserve ? 'success' : 'pending'}">${room.enableReserve ? '可预约' : '普通房间'}</span>
                    </div>
                </div>
                ${subtitle ? `<p class="row-card-subtitle">${escapeHtml(subtitle)}</p>` : ''}
            </div>
            ${renderCardIcon()}
        </article>
    `;
}

function renderScanMachineCard(machine) {
    const linkedStatus = machine.linkedStatus || null;
    const linkedMachine = linkedStatus && linkedStatus.matched ? linkedStatus.machine || null : null;
    const linkedRoom = linkedStatus && linkedStatus.matched ? linkedStatus.room || null : null;
    const statusChipClass = linkedMachine ? machineChipClass(linkedMachine) : 'pending';
    const statusChipText = linkedMachine
        ? (linkedMachine.statusLabel || linkedMachine.stateDesc || '未知')
        : (linkedStatus ? '未匹配' : '读取中');
    const timeChipText = linkedMachine
        ? machineAvailabilityText(linkedMachine)
        : (linkedStatus ? '暂无时间' : '计算中');
    const goodsId = linkedMachine && linkedMachine.goodsId ? linkedMachine.goodsId : (machine.goodsId || '');
    const roomId = linkedRoom && linkedRoom.id ? linkedRoom.id : (machine.shopId || '');
    return `
        <article
            class="list-card row-card clickable-card"
            role="button"
            tabindex="0"
            data-clickable-card="true"
            data-action="open-scan"
            data-qr-code="${escapeHtml(machine.qrCode)}"
            data-goods-id="${escapeHtml(goodsId)}"
            data-room-id="${escapeHtml(roomId)}"
            aria-label="查看${escapeHtml(machine.label || '收藏设备')}详情"
        >
            <div class="row-card-main">
                <div class="row-card-head">
                    <h4>${escapeHtml(machine.label || '收藏设备')}</h4>
                    <div class="row-card-pills">
                        <span class="chip ${statusChipClass}">${escapeHtml(statusChipText)}</span>
                        <span class="chip info">${escapeHtml(timeChipText)}</span>
                    </div>
                </div>
                <p class="row-card-subtitle">二维码：${escapeHtml(maskCode(machine.qrCode || ''))}</p>
            </div>
            ${renderCardIcon()}
        </article>
    `;
}

function renderRoomMachineCard(machine) {
    const subtitle = joinCompactText([machine.floorCode || '', machineAvailabilityDetail(machine)]);
    const timeText = machineAvailabilityText(machine);
    return `
        <article
            class="list-card row-card clickable-card machine-row-card"
            role="button"
            tabindex="0"
            data-clickable-card="true"
            data-action="open-machine"
            data-goods-id="${escapeHtml(machine.goodsId)}"
            aria-label="查看${escapeHtml(machine.name || '设备')}详情"
        >
            <div class="row-card-main">
                <div class="row-card-head">
                    <h4>${escapeHtml(machine.name || '设备')}</h4>
                    <div class="row-card-pills">
                        <span class="chip ${machineChipClass(machine)}">${escapeHtml(machine.statusLabel || machine.stateDesc || '未知')}</span>
                        <span class="chip info">${escapeHtml(timeText)}</span>
                    </div>
                </div>
                ${subtitle ? `<p class="row-card-subtitle">${escapeHtml(subtitle)}</p>` : ''}
            </div>
            ${renderCardIcon()}
        </article>
    `;
}

function getDefaultRoomCategoryCode(roomId) {
    const room = (state.wash.rooms || []).find(item => item.id === roomId);
    const codes = Array.isArray((room || {}).categoryCodeList) ? room.categoryCodeList : [];
    const firstCode = codes.find(code => String(code || '').trim());
    return String(firstCode || '').trim();
}

function getRoomCategoryOptions(payload, machines) {
    const categoryMap = new Map();
    const hasServerCategories = Array.isArray(payload.categories) && payload.categories.length > 0;
    (payload.categories || []).forEach(category => {
        const code = String((category || {}).categoryCode || '').trim();
        if (!code) {
            return;
        }
        categoryMap.set(code, {
            categoryCode: code,
            categoryName: String(category.categoryName || code),
            total: Number(category.total || 0),
            idleCount: Number(category.idleCount || 0),
        });
    });
    (machines || []).forEach(machine => {
        const code = String((machine || {}).categoryCode || '').trim();
        if (!code) {
            return;
        }
        if (!categoryMap.has(code)) {
            categoryMap.set(code, {
                categoryCode: code,
                categoryName: String(machine.categoryName || code),
                total: 0,
                idleCount: 0,
            });
        }
        if (!hasServerCategories) {
            const item = categoryMap.get(code);
            item.total += 1;
            if (String(machine.statusLabel || '').includes('空闲')) {
                item.idleCount += 1;
            }
        }
    });

    const totalCount = hasServerCategories
        ? Array.from(categoryMap.values()).reduce((sum, item) => sum + Number(item.total || 0), 0)
        : (machines || []).length;
    const idleCount = hasServerCategories
        ? Array.from(categoryMap.values()).reduce((sum, item) => sum + Number(item.idleCount || 0), 0)
        : Number((((payload || {}).room) || {}).idleCount || 0);

    return [
        {
            categoryCode: '',
            categoryName: '全部',
            total: totalCount,
            idleCount,
        },
        ...Array.from(categoryMap.values()),
    ];
}

function resolveSelectedRoomCategoryCode(categories) {
    const availableCodes = new Set((categories || []).map(item => String(item.categoryCode || '')));
    if (availableCodes.has(state.wash.roomCategoryCode || '')) {
        return state.wash.roomCategoryCode || '';
    }
    state.wash.roomCategoryCode = '';
    return '';
}

function renderRoomCategoryMenu(categories, selectedCategoryCode) {
    if (!categories || categories.length <= 1) {
        return '';
    }
    return `
        <div class="category-menu" role="tablist" aria-label="设备分类">
            ${categories.map(category => {
                const code = String(category.categoryCode || '');
                const active = code === selectedCategoryCode;
                const countText = Number.isFinite(Number(category.total)) ? `${category.total} 台` : '--';
                return `
                    <button
                        class="category-menu-btn${active ? ' active' : ''}"
                        type="button"
                        role="tab"
                        aria-selected="${active ? 'true' : 'false'}"
                        data-action="set-room-category"
                        data-category-code="${escapeHtml(code)}"
                    >
                        <span>${escapeHtml(category.categoryName || code || '全部')}</span>
                        <strong>${escapeHtml(countText)}</strong>
                    </button>
                `;
            }).join('')}
        </div>
    `;
}

function renderReservationCard(task) {
    const lastEvent = task.lastEvent || {};
    const statusClass = reservationStatusClass(task.status);
    const currentOrder = task.currentOrder || null;
    const title = String(task.title || task.machineName || '预约任务').trim() || '预约任务';
    const titleKey = normalizeComparableText(title);
    const subtitle = uniqueSubtitle(title, joinCompactText(
        [task.machineName || '', task.modeName || ''].filter(part => normalizeComparableText(part) !== titleKey)
    ));
    const scheduleText = reservationScheduleText(task);
    const notices = [];
    const noticeMessages = dedupeCompactTextList([task.lastError, lastEvent.message]);
    if (task.lastError && textAppearsInMessages(noticeMessages, task.lastError)) {
        notices.push({ className: 'warning', text: String(task.lastError).trim() });
    }
    if (lastEvent.message && !textAppearsInMessages(notices.map(item => item.text), lastEvent.message)) {
        notices.push({ className: '', text: String(lastEvent.message).trim() });
    }
    const orderStateText = String((currentOrder || {}).stateDesc || '').trim();
    const activeOrderText = String(task.activeOrderNo || '').trim();
    const metaItems = [
        renderMetaPill('计划', scheduleText),
        renderMetaPill('保单窗口', formatWindow(task.startAt, task.holdUntil))
    ];
    if (activeOrderText && !textAppearsInMessages(noticeMessages, activeOrderText)) {
        metaItems.push(renderMetaPill('活跃订单', maskCode(activeOrderText)));
    }
    if (orderStateText && !textAppearsInMessages(noticeMessages, orderStateText)) {
        metaItems.push(renderMetaPill('订单状态', orderStateText));
    }
    metaItems.push(renderMetaPill('最近检查', formatDateTime(task.lastCheckedAt)));
    const toggleButton = task.status === 'paused'
        ? `<button class="btn btn-secondary" type="button" data-action="resume-reservation" data-task-id="${escapeHtml(String(task.id))}">恢复</button>`
        : `<button class="btn btn-light" type="button" data-action="pause-reservation" data-task-id="${escapeHtml(String(task.id))}">暂停</button>`;
    return `
        <article class="order-card reservation-card">
            <div class="compact-card-head">
                <div class="compact-main reservation-main">
                    <div class="reservation-title-row">
                        <h4>${escapeHtml(title)}</h4>
                        <span class="chip ${statusClass}">${escapeHtml(reservationStatusLabel(task.status))}</span>
                    </div>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
            </div>
            <div class="compact-meta-row reservation-meta-row">
                ${metaItems.filter(Boolean).join('')}
            </div>
            ${notices.length ? `
                <div class="reservation-notices">
                    ${notices.map(item => `<div class="callout reservation-callout${item.className ? ` ${item.className}` : ''}">${escapeHtml(item.text)}</div>`).join('')}
                </div>
            ` : ''}
            <div class="reservation-actions">
                ${task.processId ? `
                    <div class="compact-actions reservation-actions-primary">
                        <button class="btn btn-primary" type="button" data-action="continue-reservation-process" data-task-id="${escapeHtml(String(task.id))}" data-process-id="${escapeHtml(task.processId)}">继续流程</button>
                    </div>
                ` : ''}
                <div class="compact-actions reservation-actions-secondary">
                    ${toggleButton}
                    <button class="btn btn-danger" type="button" data-action="delete-reservation" data-task-id="${escapeHtml(String(task.id))}">删除</button>
                </div>
            </div>
        </article>
    `;
}

function renderActiveProcessCard(process) {
    const order = process.order || {};
    const timeMeta = getOrderTimeMeta(order);
    const metaRow = [
        renderMetaPill('订单状态', order.stateDesc || ''),
        timeMeta ? renderMetaPill(timeMeta.countdownLabel, timeMeta.countdownText) : '',
    ].filter(Boolean).join('');
    return `
        <article class="order-card process-card">
            <div class="compact-card-head">
                <div class="compact-main">
                    <h4>${escapeHtml(order.machineName || '待继续流程')}</h4>
                    <p>最近更新 ${escapeHtml(formatDateTime(process.updatedAt))}</p>
                </div>
                <div class="compact-side">
                    <span class="chip warning">${escapeHtml(processDisplayStepLabel(process))}</span>
                    ${timeMeta ? `<span class="compact-side-value">${escapeHtml(formatDateTime(timeMeta.value))}</span>` : ''}
                </div>
            </div>
            ${metaRow ? `<div class="compact-meta-row">${metaRow}</div>` : ''}
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
    const rawProcessOrder = activeProcess ? (activeProcess.order || null) : null;
    const processOrder = shouldUseHistoryOrderOverlay(order, rawProcessOrder)
        ? rawProcessOrder
        : null;
    const rawDetail = cache.orderDetails.get(order.orderNo);
    const detail = shouldUseHistoryOrderOverlay(order, rawDetail)
        ? rawDetail
        : null;
    const mergedOrder = {
        ...(order || {}),
        ...(processOrder || {}),
        ...(detail || {}),
    };
    const buttonSwitch = mergedOrder.buttonSwitch || {};
    const timeMeta = getOrderTimeMeta(mergedOrder);
    const summaryTime = formatDateTime((timeMeta && timeMeta.value) || mergedOrder.createTime || order.createTime);
    return `
        <article
            class="order-card history-order-row clickable-card ${expanded ? 'expanded' : ''}"
            role="button"
            tabindex="0"
            aria-expanded="${expanded ? 'true' : 'false'}"
            data-clickable-card="true"
            data-action="toggle-order-detail"
            data-order-no="${escapeHtml(order.orderNo)}"
            aria-label="${expanded ? '收起' : '展开'}${escapeHtml(mergedOrder.machineName || order.machineName || '订单')}详情"
        >
            <div class="history-order-summary">
                <div class="history-order-main">
                    <h4>${escapeHtml(mergedOrder.machineName || order.machineName || '订单')}</h4>
                    <p>${escapeHtml(mergedOrder.modeName || order.modeName || '未知模式')}</p>
                </div>
                <div class="history-order-side">
                    <div class="inline-row history-order-pills">
                        ${activeProcess ? '<span class="chip warning">流程进行中</span>' : ''}
                        <span class="chip ${orderChipClass(mergedOrder.state)}">${escapeHtml(mergedOrder.stateDesc || order.stateDesc || '未知状态')}</span>
                    </div>
                    <span class="history-order-time">${escapeHtml(summaryTime)}</span>
                </div>
                ${renderCardIcon()}
            </div>
            ${expanded ? renderOrderDetail(detail || processOrder || mergedOrder, { orderNo: order.orderNo, buttonSwitch }) : ''}
        </article>
    `;
}

function renderOrderDetail(detail, options = {}) {
    const { orderNo = '', buttonSwitch = {} } = options;
    if (!detail) {
        return `
            <div class="spacer-sm"></div>
            <div class="callout">正在加载订单详情...</div>
        `;
    }
    const timeMeta = getOrderTimeMeta(detail);
    const resolvedOrderNo = detail.orderNo || orderNo || '--';
    const actionButtons = [
        buttonSwitch.canCloseOrder ? `<button class="btn btn-danger" type="button" data-action="finish-order" data-order-no="${escapeHtml(resolvedOrderNo)}">结束订单</button>` : '',
        buttonSwitch.canCancel ? `<button class="btn btn-light" type="button" data-action="cancel-order" data-order-no="${escapeHtml(resolvedOrderNo)}">取消订单</button>` : '',
    ].filter(Boolean).join('');
    return `
        <div class="spacer-sm"></div>
        <div class="history-order-detail">
            <div class="detail-grid">
                <div><span>订单号</span><span>${escapeHtml(resolvedOrderNo)}</span></div>
                <div><span>状态</span><span>${escapeHtml(detail.stateDesc || '--')}</span></div>
                <div><span>页面状态</span><span>${escapeHtml(detail.pageCode || '--')}</span></div>
                <div><span>价格</span><span>${escapeHtml(stringOrFallback(detail.price, '--'))}</span></div>
                <div><span>支付时间</span><span>${escapeHtml(formatDateTime(detail.payTime))}</span></div>
                <div><span>${escapeHtml(timeMeta ? timeMeta.label : '时间')}</span><span>${escapeHtml(formatDateTime(timeMeta ? timeMeta.value : ''))}</span></div>
                <div><span>${escapeHtml(timeMeta ? timeMeta.countdownLabel : '当前阶段')}</span><span>${escapeHtml(timeMeta ? timeMeta.countdownText : '--')}</span></div>
                <div><span>洗衣房</span><span>${escapeHtml(detail.shopName || '--')}</span></div>
                <div><span>模式</span><span>${escapeHtml(detail.modeName || '--')}</span></div>
            </div>
            ${actionButtons ? `<div class="spacer-sm"></div><div class="compact-actions detail-actions">${actionButtons}</div>` : ''}
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
    if (action === 'back-room' || action === 'close-machine-modal') {
        const hostView = state.wash.machineHostView === 'room' && state.wash.roomId && state.wash.roomData ? 'room' : 'home';
        state.wash.view = hostView;
        state.wash.machineId = null;
        state.wash.machineDetail = null;
        state.wash.machineHostView = null;
        state.wash.result = null;
        if (hostView === 'home') {
            state.wash.roomId = null;
            state.wash.roomCategoryCode = '';
            state.wash.roomData = null;
        }
        renderWash();
        renderGlobalRefresh();
        scheduleActiveAutoRefresh();
        pushHistoryState();
        return;
    }
    if (action === 'set-room-category') {
        await switchRoomCategory(button.dataset.categoryCode || '', { pushHistory: true });
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
            const goodsId = String(button.dataset.goodsId || '').trim();
            if (goodsId) {
                await openMachine(goodsId, { hostView: 'home' });
                return;
            }
            const qrCode = String(button.dataset.qrCode || '').trim();
            if (qrCode) {
                const linkedStatus = await getScanMachineStatus(qrCode, { forceRefresh: true });
                const linkedMachine = linkedStatus && linkedStatus.matched ? linkedStatus.machine || null : null;
                const linkedGoodsId = String((linkedMachine || {}).goodsId || '').trim();
                if (linkedGoodsId) {
                    await openMachine(linkedGoodsId, { hostView: 'home' });
                    return;
                }
            }
            await openScanMachine(button.dataset.qrCode);
            return;
        }
        if (action === 'toggle-favorite') {
            await toggleFavoriteForCurrentMachine();
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
    const requestId = ++state.ui.roomRequestId;
    try {
        const nextCategoryCode = options.roomCategoryCode != null
            ? String(options.roomCategoryCode || '')
            : getDefaultRoomCategoryCode(roomId);
        const payload = await getRoomMachines(roomId, {
            forceRefresh: Boolean(options.forceRefresh),
            categoryCode: nextCategoryCode,
        });
        if (requestId !== state.ui.roomRequestId) {
            return;
        }
        state.wash.view = 'room';
        state.wash.machineHostView = null;
        state.wash.roomId = roomId;
        state.wash.roomCategoryCode = String((payload && payload.selectedCategoryCode) || nextCategoryCode || '');
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

async function switchRoomCategory(categoryCode, options = {}) {
    const roomId = state.wash.roomId;
    if (!roomId) {
        return;
    }
    const nextCategoryCode = String(categoryCode || '');
    const keepHistory = options.pushHistory !== false;
    const requestId = ++state.ui.roomRequestId;
    setTabRefreshing('washTab', true);
    try {
        const payload = await getRoomMachines(roomId, {
            forceRefresh: Boolean(options.forceRefresh),
            categoryCode: nextCategoryCode,
        });
        if (requestId !== state.ui.roomRequestId) {
            return;
        }
        state.wash.roomCategoryCode = String((payload && payload.selectedCategoryCode) || nextCategoryCode || '');
        state.wash.roomData = payload;
        renderWash();
        if (keepHistory) {
            pushHistoryState();
        } else {
            replaceHistoryState();
        }
    } finally {
        if (requestId === state.ui.roomRequestId) {
            setTabRefreshing('washTab', false);
        }
    }
}

async function openMachine(goodsId, options = {}) {
    state.wash.loading = true;
    renderWashLoading('正在加载设备详情...');
    try {
        let targetRoomId = String(options.roomId || state.wash.roomId || '').trim();
        const hostView = options.hostView || (targetRoomId ? 'room' : 'home');
        state.wash.machineDetail = await getMachineDetail(goodsId);
        const detailRoomId = String((state.wash.machineDetail || {}).shopId || '').trim();
        const detailCategoryCode = String((state.wash.machineDetail || {}).categoryCode || '').trim();
        if (hostView !== 'home' && !targetRoomId && detailRoomId) {
            targetRoomId = detailRoomId;
        }
        let nextRoomCategoryCode = options.roomCategoryCode != null
            ? String(options.roomCategoryCode || '')
            : (state.wash.roomId === targetRoomId ? state.wash.roomCategoryCode || '' : '');
        if (hostView !== 'home' && targetRoomId) {
            if (!nextRoomCategoryCode) {
                nextRoomCategoryCode = detailCategoryCode || getDefaultRoomCategoryCode(targetRoomId);
            }
            if (
                !state.wash.roomData
                || state.wash.roomId !== targetRoomId
                || String(state.wash.roomCategoryCode || '') !== String(nextRoomCategoryCode || '')
            ) {
                state.wash.roomData = await getRoomMachines(targetRoomId, { categoryCode: nextRoomCategoryCode });
            }
            state.wash.roomId = targetRoomId;
            state.wash.roomCategoryCode = String((state.wash.roomData && state.wash.roomData.selectedCategoryCode) || nextRoomCategoryCode || '');
        } else {
            state.wash.roomId = null;
            state.wash.roomData = null;
            state.wash.roomCategoryCode = '';
        }
        state.wash.view = 'machine';
        state.wash.machineHostView = hostView;
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
    renderWashLoading('正在读取可用模式和设备状态...');
    try {
        const [modes, linkedStatus] = await Promise.all([
            getScanModes(qrCode),
            getScanMachineStatus(qrCode),
        ]);
        state.wash.scanMachine = {
            ...(localMachine || { label: options.label || '收藏设备', qrCode }),
            modes,
            linkedStatus,
        };
        state.wash.view = 'scan';
        state.wash.machineHostView = null;
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

async function toggleFavoriteForCurrentMachine() {
    const machine = state.wash.machineDetail;
    if (!machine) {
        return;
    }
    if (!machine.scanCode) {
        await showAlertDialog('当前设备暂未返回二维码，无法加入收藏。');
        return;
    }

    const favorites = machine.isFavorite
        ? await removeFavoriteMachine(machine.scanCode)
        : await addFavoriteMachine(machine);
    await syncFavoriteMachines(favorites, { hydrateStatus: true, forceRefresh: true });

    state.wash.machineDetail = {
        ...machine,
        isFavorite: !machine.isFavorite,
    };
    cache.machineDetails.set(machine.goodsId, state.wash.machineDetail);
    renderWash();
    await refreshReservationMachineOptions();
    showToastMessage(machine.isFavorite ? '已取消收藏。' : '已加入收藏。');
}

async function openMachineScan() {
    const machine = state.wash.machineDetail;
    if (!machine || !machine.supportsVirtualScan || !machine.scanCode) {
        await showAlertDialog('当前设备暂不支持扫码下单。');
        return;
    }
    const modeId = getSelectedValue('washModeSelect');
    if (!modeId) {
        await showAlertDialog('请先选择模式。');
        return;
    }
    await startScanProcess({ qrCode: machine.scanCode, modeId });
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
        state.wash.machineHostView = null;
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

async function startScanProcess(options = {}) {
    const scanMachine = state.wash.scanMachine;
    const qrCode = String(options.qrCode || (scanMachine ? scanMachine.qrCode : '') || '').trim();
    const modeId = String(options.modeId || getSelectedValue('scanModeSelect') || '').trim();
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

    try {
        const data = await apiPost('/api/process/next', { processId: process.processId });
        await openProcess(process.processId, { pushHistory: false });
        await loadActiveProcesses();
        const latestOrderNo = (state.wash.process && (state.wash.process.orderNo || ((state.wash.process.contextSummary || {}).orderNo))) || '';
        if (latestOrderNo) {
            refreshOrdersOverview(true, { silent: true, preserveItems: true });
        } else if ((data.process || {}).completed) {
            refreshOrdersOverview(true, { silent: true, preserveItems: true });
        }
        showToastMessage(normalizeProcessToastMessage(data.msg));
    } catch (error) {
        try {
            await openProcess(process.processId, { pushHistory: false });
            await loadActiveProcesses();
            const latestOrderNo = (state.wash.process && (state.wash.process.orderNo || ((state.wash.process.contextSummary || {}).orderNo))) || '';
            if (latestOrderNo) {
                refreshOrdersOverview(true, { silent: true, preserveItems: true });
            }
        } catch (refreshError) {
            handleRequestError(refreshError, '刷新流程状态失败。', true);
        }
        throw error;
    }
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
        payload.timeZone = getBrowserTimeZone();
    }

    if (source === 'scan') {
        const qrCode = el.reservationScanMachine.value;
        const machine = (state.wash.scanMachines || []).find(item => item.qrCode === qrCode);
        if (!qrCode || !machine) {
            await showAlertDialog('请先选择收藏机器。');
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
        const detail = await getMachineDetail(machine.goodsId);
        if (!detail.supportsVirtualScan || !detail.scanCode) {
            await showAlertDialog('当前设备暂不支持扫码下单。');
            return;
        }
        payload.machineId = machine.goodsId;
        payload.machineName = detail.name || machine.name;
        payload.roomId = room.id;
        payload.roomName = room.name;
        payload.qrCode = detail.scanCode;
    }

    const data = await apiPost('/api/reservations', payload);
    showToastMessage(data.msg || '预约任务已创建。');
    el.reservationTitle.value = '';
    setReservationComposerOpen(false);
    await loadReservations();
}

async function handleReservationClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) {
        return;
    }

    const action = button.dataset.action;
    if (action === 'open-reservation-composer') {
        if (!ensureTokenReady()) {
            return;
        }
        setReservationComposerOpen(true);
        return;
    }
    if (action === 'close-reservation-composer') {
        setReservationComposerOpen(false);
        return;
    }
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
    await loadOrders(true, { preserveItems: true });
}

function updateOrderFromDetail(orderNo, detail) {
    const item = state.orders.items.find(order => order.orderNo === orderNo);
    if (!item || !detail) {
        return;
    }
    if (isTerminalHistoryOrder(item) && !isTerminalHistoryOrder(detail)) {
        cache.orderDetails.delete(orderNo);
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
    if (!order || isTerminalHistoryOrder(order)) {
        return false;
    }
    const stateCode = Number(order && order.state);
    const stateDesc = String((order && order.stateDesc) || '');
    const pageCode = String((order && order.pageCode) || '');
    const buttonSwitch = (order && order.buttonSwitch) || {};
    const invalidTime = parseDateValue(order && order.invalidTime);
    const hasPendingSignals = stateCode === 50
        || Boolean(buttonSwitch.canPay)
        || stateDesc.includes('待支付')
        || stateDesc.includes('待验证')
        || ['waiting_check', 'place_clothes', 'waiting_choose_ump'].includes(pageCode);
    if (!hasPendingSignals) {
        return false;
    }
    return !(invalidTime && invalidTime.getTime() <= Date.now());
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

function machineAvailabilityText(machine) {
    if (!machine) {
        return '--';
    }
    if (machine.finishTimeText) {
        return `预计 ${machine.finishTimeText}`;
    }
    if (machine.statusLabel === '空闲') {
        return '现在可用';
    }
    return machine.statusDetail || '--';
}

function machineAvailabilityDetail(machine) {
    const detailText = String((machine || {}).statusDetail || '').trim();
    const timeText = machineAvailabilityText(machine);
    if (!detailText || detailText === '--' || detailText === timeText) {
        return '';
    }
    return detailText;
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
    const response = await fetch(withBasePath(url), {
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
