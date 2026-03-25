const STEPS = [
    '解析机器',
    '创建订单',
    '确认放衣',
    '生成结算单与预支付',
    '支付并启动'
];

const state = {
    processId: null,
    currentStep: 1,
    completed: false,
    running: false,
    debugVisible: false,
    machines: [],
    orders: [],
    modeReady: false,
    lastToken: '',
};

const el = {};

document.addEventListener('DOMContentLoaded', async () => {
    bindElements();
    bindEvents();
    renderSteps();
    await loadConfig();
    setStatus('当前状态：待机', '请先输入 Token，选择机器并提取模式。');
});

function bindElements() {
    el.token = document.getElementById('token');
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
        userLog('❌ 初始化配置失败');
        return;
    }
    state.machines = data.machines || [];
    el.machine.innerHTML = '';
    state.machines.forEach(machine => {
        const option = document.createElement('option');
        option.value = machine.value;
        option.textContent = machine.label;
        el.machine.appendChild(option);
    });
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
    if (state.completed) {
        el.nextBtn.textContent = '🎉 洗涤已开始';
        el.nextBtn.disabled = true;
        return;
    }
    if (!state.processId) {
        el.nextBtn.textContent = '▶ 开始流程';
        el.nextBtn.disabled = false;
        return;
    }
    const label = STEPS[state.currentStep - 1] || '继续执行';
    el.nextBtn.textContent = `▶ 执行第 ${state.currentStep} 步：${label}`;
    el.nextBtn.disabled = state.running;
}

function setBusy(running, actionLabel = '处理中...') {
    state.running = running;
    el.nextBtn.disabled = running || state.completed;
    el.fetchModesBtn.disabled = running;
    el.fetchOrdersBtn.disabled = running;
    el.killBtn.disabled = running;
    el.resetBtn.disabled = running;
    if (running) {
        el.nextBtn.dataset.originalText = el.nextBtn.textContent;
        el.nextBtn.textContent = actionLabel;
    } else {
        updateNextButton();
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

function requireTokenAndMode(requireMode = false) {
    const token = el.token.value.trim() || state.lastToken;
    if (!token) {
        window.alert('请先输入 Token');
        return null;
    }
    state.lastToken = token;
    if (requireMode && !el.mode.value) {
        window.alert('请先提取并选择洗涤模式');
        return null;
    }
    return token;
}

async function fetchModes() {
    const token = requireTokenAndMode(false);
    if (!token) return;
    setBusy(true, '正在提取模式...');
    try {
        userLog('正在拉取机器可用模式...');
        const data = await apiPost('/api/get_modes', { token, qr_code: el.machine.value });
        if (data.status !== 'success') {
            setStatus('当前状态：模式提取失败', data.msg || '请检查 Token 或机器状态');
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
    const token = requireTokenAndMode(true);
    if (!token) return;

    if (!state.processId) {
        await startProcess(token);
        if (!state.processId) return;
    }

    setBusy(true, '正在执行...');
    try {
        const data = await apiPost('/api/process/next', { token, process_id: state.processId });
        if (data.status !== 'success') {
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

async function startProcess(token) {
    try {
        const data = await apiPost('/api/process/start', {
            token,
            qr_code: el.machine.value,
            mode_id: el.mode.value
        });
        if (data.status !== 'success') {
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
    updateNextButton();
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
    const processId = state.processId;
    const token = el.token.value.trim() || state.lastToken;
    try {
        if (processId) {
            const data = await apiPost('/api/process/reset', {
                process_id: processId,
                token,
                cleanup_remote: true
            });
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
    updateNextButton();
    renderSteps(1, 'idle');
    setStatus('当前状态：已重置', '流程已清空，请重新开始。');
}

function releaseProcessOnUnload() {
    if (!state.processId || state.completed) return;
    const token = el.token?.value?.trim() || state.lastToken;
    if (!token) return;
    const payload = JSON.stringify({
        process_id: state.processId,
        token,
        cleanup_remote: true
    });
    const blob = new Blob([payload], { type: 'application/json' });
    navigator.sendBeacon('/api/process/reset', blob);
}

async function fetchOrders() {
    const token = requireTokenAndMode(false);
    if (!token) return;
    setBusy(true, '正在刷新订单...');
    try {
        const data = await apiPost('/api/get_orders', { token });
        if (data.status !== 'success') {
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
    const token = requireTokenAndMode(false);
    if (!token) return;
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
        const data = await apiPost('/api/kill_order', { token, order_no: orderNo });
        if (data.status !== 'success') {
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

async function apiPost(url, body) {
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
