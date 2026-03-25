const STEPS = [
    '解析机器',
    '创建订单',
    '确认放衣',
    '生成结算单与预支付',
    '支付并启动'
];

const DEFAULT_TOKEN_STATUS = {
    source: 'env',
    configured: false,
    valid: false,
    reason: 'missing',
    message: '页面加载后会自动检查 .env 中的 HAILE_TOKEN。'
};

const state = {
    processId: null,
    currentStep: 1,
    completed: false,
    running: false,
    debugVisible: false,
    machines: [],
    orders: [],
    modeReady: false,
    tokenStatus: { ...DEFAULT_TOKEN_STATUS },
};

const el = {};

document.addEventListener('DOMContentLoaded', async () => {
    bindElements();
    bindEvents();
    renderSteps();
    applyTokenStatus(DEFAULT_TOKEN_STATUS);

    try {
        await loadConfig();
    } catch (err) {
        userLog(`❌ 初始化配置异常：${err.message}`);
        debugLog({ message: err.message });
        applyTokenStatus(
            {
                source: 'env',
                configured: true,
                valid: false,
                reason: 'check_failed',
                message: `初始化配置失败：${err.message}`,
            },
            true
        );
        return;
    }

    if (isTokenAvailable()) {
        setStatus('当前状态：待机', '请选择机器并提取模式。');
    }
});

function bindElements() {
    el.tokenStatusCard = document.getElementById('tokenStatusCard');
    el.tokenStatusText = document.getElementById('tokenStatusText');
    el.tokenStatusHint = document.getElementById('tokenStatusHint');
    el.machine = document.getElementById('machine');
    el.mode = document.getElementById('mode');
    el.stepStatus = document.getElementById('stepStatus');
    el.stepHint = document.getElementById('stepHint');
    el.stepList = document.getElementById('stepList');
    el.logBox = document.getElementById('logBox');
    el.debugBox = document.getElementById('debugBox');
    el.debugWrap = document.getElementById('debugWrap');
    el.nextBtn = document.getElementById('nextBtn');
    el.resetBtn = document.getElementById('resetBtn');
    el.fetchModesBtn = document.getElementById('fetchModesBtn');
    el.fetchOrdersBtn = document.getElementById('fetchOrdersBtn');
    el.killBtn = document.getElementById('killBtn');
    el.orderCombo = document.getElementById('orderCombo');
    el.orderMeta = document.getElementById('orderMeta');
    el.toggleDebugBtn = document.getElementById('toggleDebugBtn');
}

function bindEvents() {
    el.fetchModesBtn.addEventListener('click', fetchModes);
    el.nextBtn.addEventListener('click', runNextStep);
    el.resetBtn.addEventListener('click', resetProcess);
    el.fetchOrdersBtn.addEventListener('click', fetchOrders);
    el.killBtn.addEventListener('click', killOrder);
    el.toggleDebugBtn.addEventListener('click', toggleDebug);
    window.addEventListener('beforeunload', releaseProcessOnUnload);
}

async function loadConfig() {
    const data = await apiGet('/api/config');
    if (data.status !== 'success') {
        throw new Error(data.msg || '初始化配置失败');
    }

    state.machines = data.machines || [];
    renderMachineOptions();
    applyTokenStatus(data.tokenStatus || DEFAULT_TOKEN_STATUS, !((data.tokenStatus || {}).valid));
    debugLog(data);

    if (isTokenAvailable()) {
        userLog('✅ 已从服务器配置读取 Token，校验通过。');
    } else {
        userLog(`⚠️ 服务端 Token 不可用：${state.tokenStatus.message}`);
    }
}

function renderMachineOptions() {
    el.machine.innerHTML = '';
    if (!state.machines.length) {
        el.machine.innerHTML = '<option value="">暂无机器配置</option>';
        return;
    }

    state.machines.forEach(machine => {
        const option = document.createElement('option');
        option.value = machine.value;
        option.textContent = machine.label;
        el.machine.appendChild(option);
    });
}

function normalizeTokenStatus(tokenStatus = {}) {
    return {
        source: tokenStatus.source || 'env',
        configured: Boolean(tokenStatus.configured),
        valid: Boolean(tokenStatus.valid),
        reason: tokenStatus.reason || 'missing',
        message: tokenStatus.message || '服务端 Token 状态未知。',
    };
}

function isTokenAvailable() {
    return state.tokenStatus.valid === true && state.tokenStatus.reason === 'ok';
}

function getTokenCardClass(reason) {
    if (reason === 'ok') {
        return 'ok';
    }
    if (reason === 'check_failed') {
        return 'pending';
    }
    return 'warn';
}

function getTokenSourceLabel(source) {
    if (source === 'env') {
        return '.env';
    }
    return '服务器配置';
}

function getTokenAlertMessage() {
    const tokenStatus = state.tokenStatus;
    if (tokenStatus.reason === 'missing') {
        return tokenStatus.message || '未在 .env 中配置 HAILE_TOKEN，请更新后刷新页面。';
    }
    if (tokenStatus.reason === 'invalid') {
        return tokenStatus.message || '配置中的 token 无效或已失效，请更新 .env 后刷新页面。';
    }
    if (tokenStatus.reason === 'check_failed') {
        return tokenStatus.message || '暂时无法校验配置 token，请稍后刷新页面。';
    }
    return '服务端 Token 当前不可用，请先修复配置。';
}

function applyTokenStatus(tokenStatus, notify = false) {
    state.tokenStatus = normalizeTokenStatus(tokenStatus);

    el.tokenStatusCard.className = `info-card ${getTokenCardClass(state.tokenStatus.reason)}`;
    el.tokenStatusText.textContent = `Token 来源：${getTokenSourceLabel(state.tokenStatus.source)}`;
    el.tokenStatusHint.textContent = state.tokenStatus.message;

    if (isTokenAvailable()) {
        if (!state.processId && !state.completed) {
            setStatus('当前状态：待机', '请选择机器并提取模式。');
        }
    } else {
        setStatus('当前状态：服务端 Token 不可用', state.tokenStatus.message);
        renderSteps(state.currentStep, 'error');
        if (notify) {
            window.alert(getTokenAlertMessage());
        }
    }

    updateActionButtons();
}

function guardTokenAvailability() {
    if (isTokenAvailable()) {
        return true;
    }
    window.alert(getTokenAlertMessage());
    return false;
}

function setStatus(title, hint) {
    el.stepStatus.textContent = title;
    el.stepHint.textContent = hint;
}

function renderSteps(current = state.currentStep, status = 'idle') {
    el.stepList.innerHTML = '';
    STEPS.forEach((name, index) => {
        const stepNumber = index + 1;
        const item = document.createElement('div');
        item.className = 'step-item';
        if (state.completed && stepNumber <= 5) {
            item.classList.add('done');
        } else if (stepNumber < current) {
            item.classList.add('done');
        } else if (stepNumber === current) {
            item.classList.add(status === 'error' ? 'error' : 'active');
        }
        item.innerHTML = `<div class="step-index">步骤 ${stepNumber}</div><div class="step-name">${name}</div>`;
        el.stepList.appendChild(item);
    });
}

function updateNextButton() {
    const disabledByToken = !isTokenAvailable();
    if (state.completed) {
        el.nextBtn.textContent = '🎉 洗涤已开始';
        el.nextBtn.disabled = true;
        return;
    }
    if (!state.processId) {
        el.nextBtn.textContent = '▶ 开始流程';
        el.nextBtn.disabled = state.running || disabledByToken;
        return;
    }
    const label = STEPS[state.currentStep - 1] || '继续执行';
    el.nextBtn.textContent = `▶ 执行第 ${state.currentStep} 步：${label}`;
    el.nextBtn.disabled = state.running || disabledByToken;
}

function updateActionButtons() {
    const disabledByToken = !isTokenAvailable();
    updateNextButton();
    el.fetchModesBtn.disabled = state.running || disabledByToken;
    el.fetchOrdersBtn.disabled = state.running || disabledByToken;
    el.killBtn.disabled = state.running || disabledByToken;
    el.resetBtn.disabled = state.running || disabledByToken;
}

function setBusy(running, actionLabel = '处理中...') {
    state.running = running;
    updateActionButtons();
    if (running) {
        el.nextBtn.textContent = actionLabel;
    }
}

function userLog(message) {
    const prefix = `[${new Date().toLocaleTimeString()}] `;
    const existing = el.logBox.textContent === '准备就绪...' ? '' : `${el.logBox.textContent}\n`;
    el.logBox.textContent = `${existing}${prefix}${message}`;
    el.logBox.scrollTop = el.logBox.scrollHeight;
}

function debugLog(obj) {
    if (obj === undefined || obj === null) {
        return;
    }
    el.debugBox.textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
}

function toggleDebug() {
    state.debugVisible = !state.debugVisible;
    el.debugWrap.classList.toggle('hidden', !state.debugVisible);
    el.toggleDebugBtn.textContent = state.debugVisible ? '隐藏调试日志' : '显示调试日志';
}

function requireMachineSelection() {
    if (el.machine.value) {
        return true;
    }
    window.alert('请先选择一台机器');
    return false;
}

function requireModeSelection() {
    if (el.mode.value) {
        return true;
    }
    window.alert('请先提取并选择洗涤模式');
    return false;
}

function syncTokenFailure(data) {
    if (!data || !data.errorType) {
        return false;
    }

    const reasonMap = {
        token_missing: 'missing',
        token_invalid: 'invalid',
        token_check_failed: 'check_failed',
    };
    const reason = reasonMap[data.errorType];
    if (!reason) {
        return false;
    }

    applyTokenStatus(
        {
            source: 'env',
            configured: reason !== 'missing',
            valid: false,
            reason,
            message: data.msg || getTokenAlertMessage(),
        },
        false
    );
    window.alert(getTokenAlertMessage());
    return true;
}

async function fetchModes() {
    if (!guardTokenAvailability() || !requireMachineSelection()) return;
    setBusy(true, '正在提取模式...');
    try {
        userLog('正在拉取机器可用模式...');
        const data = await apiPost('/api/get_modes', { qr_code: el.machine.value });
        if (data.status !== 'success') {
            syncTokenFailure(data);
            setStatus('当前状态：模式提取失败', data.msg || '请检查机器状态');
            userLog(`❌ 模式提取失败：${data.msg}`);
            debugLog(data.debug || data);
            renderSteps(state.currentStep, 'error');
            return;
        }
        el.mode.innerHTML = '';
        (data.modes || []).forEach(mode => {
            const option = document.createElement('option');
            option.value = mode.id;
            option.textContent = mode.label;
            el.mode.appendChild(option);
        });
        state.modeReady = (data.modes || []).length > 0;
        setStatus('当前状态：模式已就绪', `已提取 ${(data.modes || []).length} 个可用模式，可以开始流程。`);
        userLog(`✅ 模式提取成功，共 ${(data.modes || []).length} 个模式。`);
        debugLog(data.debug || data);
    } catch (err) {
        userLog(`❌ 模式提取异常：${err.message}`);
        setStatus('当前状态：模式提取异常', err.message);
        renderSteps(state.currentStep, 'error');
    } finally {
        setBusy(false);
    }
}

async function runNextStep() {
    if (!guardTokenAvailability() || !requireModeSelection()) return;

    if (!state.processId) {
        if (!requireMachineSelection()) return;
        await startProcess();
        if (!state.processId) return;
    }

    setBusy(true, '正在执行...');
    try {
        const data = await apiPost('/api/process/next', { process_id: state.processId });
        if (data.status !== 'success') {
            syncTokenFailure(data);
            userLog(`❌ 第 ${state.currentStep} 步失败：${data.msg}`);
            setStatus(`当前状态：步骤 ${state.currentStep} 失败`, data.msg || '请根据提示重试或重置流程');
            debugLog(data.debug || data);
            renderSteps(state.currentStep, 'error');
            return;
        }
        applyProcess(data.process);
        userLog(`✅ ${data.msg}`);
        debugLog(data.debug);
        onStepSuccess(data);
    } catch (err) {
        userLog(`❌ 请求异常：${err.message}`);
        setStatus(`当前状态：步骤 ${state.currentStep} 异常`, err.message);
        renderSteps(state.currentStep, 'error');
    } finally {
        setBusy(false);
    }
}

async function startProcess() {
    try {
        const data = await apiPost('/api/process/start', {
            qr_code: el.machine.value,
            mode_id: el.mode.value
        });
        if (data.status !== 'success') {
            syncTokenFailure(data);
            userLog(`❌ 创建流程失败：${data.msg}`);
            setStatus('当前状态：流程创建失败', data.msg || '请检查输入参数');
            return;
        }
        applyProcess(data.process);
        const cleanedCount = (data.cleanup?.cleanedOrders || []).length;
        if (cleanedCount > 0) {
            userLog(`✅ 流程已创建，并自动清理 ${cleanedCount} 笔该机器遗留订单。`);
        } else {
            userLog('✅ 流程已创建。');
        }
        debugLog(data.debug || data.cleanup || data);
        setStatus(`当前状态：准备执行第 ${state.currentStep} 步`, cleanedCount > 0 ? '已自动清理该机器遗留订单，可以继续执行。' : '后端已托管流程状态，可以开始执行。');
    } catch (err) {
        userLog(`❌ 创建流程异常：${err.message}`);
        setStatus('当前状态：流程创建异常', err.message);
    }
}

function applyProcess(process) {
    state.processId = process.processId;
    state.currentStep = process.completed ? 6 : process.currentStep;
    state.completed = !!process.completed;
    renderSteps(Math.min(state.currentStep, 5), 'active');
    updateActionButtons();
}

function onStepSuccess(data) {
    const process = data.process || {};
    if (process.completed) {
        state.completed = true;
        setStatus('当前状态：任务圆满完成', '支付成功，设备已启动。');
        renderSteps(5, 'active');
        fetchOrders();
        return;
    }
    const step = process.currentStep;
    const hints = {
        2: '机器已解析，准备创建订单。',
        3: '订单已创建，请去洗衣机放衣并关门。',
        4: '已确认放衣，下一步将生成结算单与预支付。',
        5: '预支付凭证已就绪，下一步将支付并启动设备。'
    };
    setStatus(`当前状态：等待执行第 ${step} 步`, hints[step] || '可以继续执行下一步。');
}

async function resetProcess() {
    if (!guardTokenAvailability()) return;

    const processId = state.processId;
    try {
        if (processId) {
            const data = await apiPost('/api/process/reset', {
                process_id: processId,
                cleanup_remote: true
            });
            syncTokenFailure(data);
            const cleanedCount = (data.cleanup?.cleanedOrders || []).length;
            if (cleanedCount > 0) {
                userLog(`流程已重置，并自动结束 ${cleanedCount} 笔云端遗留订单。`);
            } else {
                userLog(data.msg || '流程已重置。');
            }
            debugLog(data.debug || data.cleanup || data);
        } else {
            userLog('流程已重置。');
        }
    } catch (err) {
        userLog(`❌ 重置时清理订单失败：${err.message}`);
        setStatus('当前状态：重置失败', err.message);
        return;
    }
    state.processId = null;
    state.currentStep = 1;
    state.completed = false;
    state.running = false;
    updateActionButtons();
    renderSteps(1, 'idle');
    setStatus('当前状态：已重置', '流程已清空，请重新开始。');
}

function releaseProcessOnUnload() {
    if (!state.processId || state.completed || !isTokenAvailable()) return;
    const payload = JSON.stringify({
        process_id: state.processId,
        cleanup_remote: true
    });
    const blob = new Blob([payload], { type: 'application/json' });
    navigator.sendBeacon('/api/process/reset', blob);
}

async function fetchOrders() {
    if (!guardTokenAvailability()) return;
    setBusy(true, '正在刷新订单...');
    try {
        const data = await apiPost('/api/get_orders', {});
        if (data.status !== 'success') {
            syncTokenFailure(data);
            userLog(`❌ 获取订单失败：${data.msg}`);
            debugLog(data.debug || data);
            return;
        }
        state.orders = data.orders || [];
        el.orderCombo.innerHTML = '';
        if (!state.orders.length) {
            el.orderCombo.innerHTML = '<option value="">暂无活跃订单</option>';
            el.orderMeta.textContent = '当前没有进行中订单。';
            userLog('未发现活跃订单。');
            debugLog(data.debug || data);
            return;
        }
        state.orders.forEach(order => {
            const option = document.createElement('option');
            option.value = order.orderNo;
            option.textContent = order.displayText;
            el.orderCombo.appendChild(option);
        });
        el.orderMeta.textContent = `已发现 ${state.orders.length} 个进行中订单；下拉项已附带状态和订单尾号。`;
        userLog(`已发现 ${state.orders.length} 个进行中订单。`);
        debugLog(data.debug || data);
    } catch (err) {
        userLog(`❌ 获取订单异常：${err.message}`);
    } finally {
        setBusy(false);
    }
}

async function killOrder() {
    if (!guardTokenAvailability()) return;
    const orderNo = el.orderCombo.value;
    if (!orderNo) {
        window.alert('请先选择一个订单');
        return;
    }
    const selectedText = el.orderCombo.options[el.orderCombo.selectedIndex]?.textContent || orderNo;
    if (!window.confirm(`确认强杀该订单吗？\n\n${selectedText}`)) {
        return;
    }
    setBusy(true, '正在强杀订单...');
    try {
        const data = await apiPost('/api/kill_order', { order_no: orderNo });
        if (data.status !== 'success') {
            syncTokenFailure(data);
            userLog(`❌ 强杀失败：${data.msg}`);
            debugLog(data.debug || data);
            return;
        }
        userLog('🎉 强杀成功，订单已结束。');
        debugLog(data.debug || data);
        await fetchOrders();
    } catch (err) {
        userLog(`❌ 强杀异常：${err.message}`);
    } finally {
        setBusy(false);
    }
}

async function apiGet(url) {
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    return parseResponse(res);
}

async function apiPost(url, body = {}) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    return parseResponse(res);
}

async function parseResponse(res) {
    let data;
    try {
        data = await res.json();
    } catch (err) {
        throw new Error(`响应解析失败: ${err.message}`);
    }
    if (!res.ok && data && data.msg) {
        return data;
    }
    if (!res.ok) {
        throw new Error(`请求失败: HTTP ${res.status}`);
    }
    return data;
}
