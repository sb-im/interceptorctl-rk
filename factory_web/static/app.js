"use strict";

const state = {
  info: null,
  specs: new Map(),
  results: new Map(),
  busy: 0,
  refreshRunning: false,
  autoTimer: null,
  toastTimer: null,
};

const systemStatusIds = new Set(["version", "system", "estop", "ups", "environment", "switch", "button_angle"]);

const fieldLabels = {
  ok: "通讯",
  version: "版本",
  hardware_stop: "实体急停",
  soft_stop: "软件急停",
  active: "当前动作",
  enabled: "使能",
  moving: "运动中",
  position: "位置",
  position_0p1deg: "位置（0.1 度）",
  speed: "速度",
  speed_0p1rpm: "速度（0.1 RPM）",
  target: "目标",
  output_enabled: "输出使能",
  output_status: "输出状态",
  voltage: "电压",
  voltage_v: "电压（V）",
  current: "电流",
  current_a: "电流（A）",
  temperature: "温度",
  temperature_c: "温度（C）",
  humidity: "湿度",
  humidity_percent: "湿度（%）",
  alarm: "告警",
  fault: "故障",
  error: "错误",
  busy: "忙碌",
  raw: "原始值",
};

function byId(id) {
  return document.getElementById(id);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { detail: `HTTP ${response.status}` };
  }
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function toast(message, isError = false) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3600);
}

function updateTaskState() {
  byId("task-state").textContent = state.busy ? `执行中（${state.busy}）` : "空闲";
}

function beginTask() {
  state.busy += 1;
  updateTaskState();
}

function endTask() {
  state.busy = Math.max(0, state.busy - 1);
  updateTaskState();
}

function formatValue(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function degreeValue(value) {
  const angle = Number(value);
  return angle === 90 || angle === 120 ? `${angle}°` : "—";
}

function flattenJson(value, prefix = "", rows = []) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (key === "ok" || key === "messages" || key === "data_hex") continue;
      const current = fieldLabels[key] || key.replaceAll("_", " ");
      const label = prefix ? `${prefix} · ${current}` : current;
      if (child && typeof child === "object" && !Array.isArray(child)) {
        flattenJson(child, label, rows);
      } else {
        rows.push([label, formatValue(child)]);
      }
    }
  } else {
    rows.push([prefix || "返回值", formatValue(value)]);
  }
  return rows;
}

function nested(value, ...path) {
  let current = value;
  for (const key of path) {
    if (!current || typeof current !== "object") return undefined;
    current = current[key];
  }
  return current;
}

function scaled(value, divisor, suffix, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number / divisor).toFixed(digits)} ${suffix}` : "—";
}

function hexValue(value, width = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? `0x${Math.trunc(number).toString(16).padStart(width, "0").toUpperCase()}` : "—";
}

function onOff(value) {
  if (value === true) return "开启";
  if (value === false) return "关闭";
  return "—";
}

function communication(value) {
  if (value === true) return "正常";
  if (value === false) return "断开";
  return "未知";
}

function ledColor(group) {
  if (!group || typeof group !== "object") return "—";
  if (group.red && group.green) return "黄 / 红绿同时";
  if (group.red) return "红";
  if (group.green) return "绿";
  return "关闭";
}

function componentRows(id, parsed) {
  const motor = parsed.motor || {};
  const axis = motor.axis || {};
  const power = parsed.power || {};
  const ups = parsed.ups || {};
  const environment = parsed.environment || {};
  const led = parsed.led || {};
  const switches = parsed.switches || {};
  const ac = parsed.ac || {};

  switch (id) {
    case "version":
      return [["STM32 固件版本", parsed.version]];
    case "system":
      return [
        ["当前动作", motor.active], ["电机状态", axis.state],
        ["电机通讯", communication(axis.communicated)], ["电机位置", scaled(axis.position, 10, "°", 1)],
        ["电机使能", onOff(axis.enabled)], ["电源通讯", communication(power.is_communicated)],
        ["电源输出", onOff(power.output_enabled)], ["设定电压", scaled(power.set_volt, 100, "V", 2)],
        ["实际电压", scaled(power.output_volt, 100, "V", 2)], ["电源告警", power.alarm ?? "—"],
      ];
    case "estop":
      return [
        ["实体急停", onOff(parsed.hardware_stop)],
        ["软件急停", onOff(parsed.soft_stop)],
        ["急停生效", onOff(parsed.active)],
      ];
    case "button_angle":
      return [
        ["配置角度", degreeValue(parsed.configured_angle_deg ?? parsed.button_open_angle_deg)],
        ["MCU 生效角度", parsed.applied_angle_deg == null ? "尚未确认" : degreeValue(parsed.applied_angle_deg)],
        ["固件支持", parsed.supported === false ? "不支持" : parsed.supported === true ? "支持" : "待确认"],
        ["配置来源", parsed.source ?? "—"],
        ["持久化", parsed.persisted === true ? "已保存" : parsed.source === "settings_file" ? "未保存" : "使用启动配置"],
        ["最近错误", parsed.last_error ?? parsed.error ?? "无"],
      ];
    case "motor":
      return [
        ["当前动作", motor.active], ["轴状态", axis.state],
        ["正式通讯", communication(axis.communicated)], ["CAN 观察通讯", communication(axis.can_communicated)],
        ["当前位置", scaled(axis.position, 10, "°", 1)], ["CAN 观察位置", scaled(axis.can_position, 10, "°", 1)],
        ["目标位置", scaled(axis.target_position, 10, "°", 1)], ["驱动使能", onOff(axis.enabled)],
        ["驱动到位", formatValue(axis.reached)], ["最终到位", formatValue(axis.final_reached)],
        ["标定完成", formatValue(axis.calibed)], ["标定中", formatValue(axis.calibing)],
        ["堵转", formatValue(axis.stall)], ["标定失败", formatValue(axis.calib_failed)],
      ];
    case "power":
      return [
        ["通讯", communication(power.is_communicated)], ["输出", onOff(power.output_enabled)],
        ["设定电压", scaled(power.set_volt, 100, "V", 2)], ["设定电流", scaled(power.set_curr, 100, "A", 2)],
        ["实际电压", scaled(power.output_volt, 100, "V", 2)], ["实际电流", scaled(power.output_curr, 100, "A", 2)],
        ["温度", scaled(power.temperature, 10, "°C", 1)], ["告警字", hexValue(power.alarm, 4)],
        ["最近错误", power.last_error_name ?? power.last_error],
      ];
    case "ups":
      return [
        ["通讯", communication(ups.is_communicated)], ["电压", scaled(ups.volt, 100, "V", 2)],
        ["电流", scaled(ups.curr, 100, "A", 2)], ["温度", scaled(ups.temp, 100, "°C", 2)],
        ["状态", ups.status], ["输出状态", ups.output_status],
        ["软件版本", ups.software_version], ["硬件版本", ups.hardware_version],
        ["请求关机", ups.request_power_off],
      ];
    case "environment":
      return [
        ["通讯", communication(environment.is_communicated)], ["温度", scaled(environment.temperature, 100, "°C", 2)],
        ["相对湿度", scaled(environment.humidity, 100, "%RH", 2)], ["I2C 地址", hexValue(environment.address)],
        ["最近错误", environment.last_error_name ?? environment.last_error], ["有效样本数", environment.sample_count],
      ];
    case "led":
      return [
        ["通讯", communication(led.is_communicated)], ["输出掩码", hexValue(led.mask)],
        ["JC", ledColor(nested(led, "groups", "jc"))], ["CD", ledColor(nested(led, "groups", "cd"))],
        ["WZ", ledColor(nested(led, "groups", "wz"))], ["DP", ledColor(nested(led, "groups", "dp"))],
        ["最近错误", led.last_error_name ?? led.last_error], ["成功写入次数", led.write_count],
      ];
    case "switch":
      return [
        ["航空器在位（PSW4）", formatValue(switches.aircraft_present_switch)],
        ["航空器位置（PSW2）", formatValue(switches.aircraft_position_switch)],
        ["模块到位（PSW1）", formatValue(switches.module_reached_switch)],
        ["盖按钮（PSW3）", formatValue(switches.cover_button)],
        ["手动动作", switches.manual_action_name], ["有效掩码", hexValue(switches.active_mask)],
        ["原始电平掩码", hexValue(switches.raw_level_mask)],
      ];
    case "ac":
      return [
        ["通讯", communication(ac.is_communicated)], ["设备状态", ac.device_status_name ?? ac.device_status],
        ["忙碌", formatValue(ac.busy)], ["运行模式", ac.run_mode_name ?? ac.run_mode],
        ["回风温度", scaled(ac.return_air_temp, 10, "°C", 1)], ["外部温度", scaled(ac.external_temp, 10, "°C", 1)],
        ["冷凝器温度", scaled(ac.condenser_temp, 10, "°C", 1)], ["蒸发器温度", scaled(ac.evaporator_temp, 10, "°C", 1)],
        ["直流电压", scaled(ac.dc_voltage, 10, "V", 1)], ["直流电流", scaled(ac.dc_current, 10, "A", 1)],
        ["室内风机", `${formatValue(ac.indoor_fan_rpm)} rpm`], ["室外风机", `${formatValue(ac.outdoor_fan_rpm)} rpm`],
        ["压缩机", onOff(ac.compressor_status === undefined ? undefined : Boolean(ac.compressor_status))],
        ["加热器", onOff(ac.heater_status === undefined ? undefined : Boolean(ac.heater_status))],
        ["告警", Array.isArray(ac.alarm_names) && ac.alarm_names.length ? ac.alarm_names.join(", ") : hexValue(ac.alarms, 4)],
        ["最近错误", ac.last_error_name ?? ac.last_error],
      ];
    default:
      return flattenJson(parsed);
  }
}

function nonzero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number !== 0 : Boolean(value);
}

function componentState(id, result) {
  const parsed = result && result.json;
  if (!result || !result.ok || !parsed || typeof parsed !== "object" || parsed.ok === false) {
    return { tone: "bad", label: "异常" };
  }
  const motor = parsed.motor || {};
  const axis = motor.axis || {};
  const power = parsed.power || {};
  const rootById = {
    motor: axis,
    power,
    ups: parsed.ups || {},
    environment: parsed.environment || {},
    led: parsed.led || {},
    switch: parsed.switches || {},
    ac: parsed.ac || {},
  };
  const root = rootById[id];
  if (id === "version" && !parsed.version) return { tone: "warn", label: "数据缺失" };
  if (id === "system" && (!parsed.motor || !parsed.power)) return { tone: "warn", label: "数据缺失" };
  if (id === "estop" && typeof parsed.hardware_stop !== "boolean") return { tone: "warn", label: "数据缺失" };
  if (id === "button_angle") {
    const configured = parsed.configured_angle_deg ?? parsed.button_open_angle_deg;
    if (configured !== 90 && configured !== 120) return { tone: "warn", label: "数据缺失" };
    if (parsed.supported === false) return { tone: "warn", label: "固件不支持" };
    if (parsed.applied_angle_deg !== 90 && parsed.applied_angle_deg !== 120) return { tone: "warn", label: "等待下发" };
    if (parsed.applied_angle_deg !== configured) return { tone: "warn", label: "等待同步" };
  }
  if (root && Object.keys(root).length === 0) return { tone: "warn", label: "数据缺失" };
  if (root && root.is_communicated === false) return { tone: "bad", label: "断通讯" };
  if (id === "motor" && axis.communicated === false) return { tone: "bad", label: "断通讯" };
  if (id === "system" && (axis.communicated === false || power.is_communicated === false)) return { tone: "bad", label: "断通讯" };
  if (id === "estop" && (parsed.hardware_stop || parsed.soft_stop || parsed.active)) return { tone: "bad", label: "急停生效" };
  if ((id === "motor" || id === "system") && (axis.stall || axis.calib_failed || axis.state === "error")) return { tone: "bad", label: "电机异常" };
  if ((id === "power" || id === "system") && (nonzero(power.alarm) || nonzero(power.last_error))) return { tone: "bad", label: "电源告警" };
  if (id === "environment" && nonzero(root.last_error)) return { tone: "bad", label: "传感器异常" };
  if (id === "led" && nonzero(root.last_error)) return { tone: "bad", label: "LED 异常" };
  if (id === "ac" && (nonzero(root.alarms) || nonzero(root.last_error))) return { tone: "bad", label: "空调告警" };
  if ((id === "motor" || id === "system") && motor.active && motor.active !== "idle") return { tone: "warn", label: "运动中" };
  if (id === "ac" && root.busy) return { tone: "warn", label: "处理中" };
  if (id === "switch" && root.manual_action_name && root.manual_action_name !== "none") return { tone: "warn", label: "手动动作" };
  return { tone: "good", label: "正常" };
}

function buildStatusCards(specs, gridId = "status-grid", includedIds = null) {
  const grid = byId(gridId);
  if (!grid) return;
  grid.replaceChildren();
  const visibleSpecs = includedIds ? specs.filter((spec) => includedIds.has(spec.id)) : specs;
  visibleSpecs.forEach((spec, index) => {
    state.specs.set(spec.id, spec);
    const card = document.createElement("article");
    card.className = "status-card";
    card.dataset.statusId = spec.id;

    const head = document.createElement("div");
    head.className = "status-card-head";
    const titleWrap = document.createElement("div");
    titleWrap.className = "status-card-title";
    const number = document.createElement("span");
    number.textContent = String(index + 1).padStart(2, "0");
    const title = document.createElement("strong");
    title.textContent = spec.label;
    titleWrap.append(number, title);

    const actions = document.createElement("div");
    actions.className = "card-actions";
    const status = document.createElement("span");
    status.className = "state-label";
    status.textContent = "未读取";
    const refresh = document.createElement("button");
    refresh.className = "refresh-card";
    refresh.type = "button";
    refresh.textContent = "刷新";
    refresh.addEventListener("click", () => refreshOne(spec.id, refresh));
    actions.append(status, refresh);
    head.append(titleWrap, actions);

    const body = document.createElement("div");
    body.className = "status-body";
    const placeholder = document.createElement("p");
    placeholder.className = "status-placeholder";
    placeholder.textContent = "等待读取组件信息…";
    body.append(placeholder);
    card.append(head, body);
    grid.append(card);
  });
  updateSummary();
}

function statusCards(id) {
  return document.querySelectorAll(`.status-card[data-status-id="${id}"]`);
}

function setCardLoading(id, loading) {
  for (const card of statusCards(id)) {
    card.classList.toggle("loading", loading);
    const label = card.querySelector(".state-label");
    if (loading) label.textContent = "读取中";
  }
}

function renderStatus(id, result) {
  state.results.set(id, result);
  const parsed = result.json;
  const status = componentState(id, result);
  for (const card of statusCards(id)) {
    card.classList.remove("loading", "good", "bad", "warn");
    card.classList.add(status.tone);
    card.querySelector(".state-label").textContent = status.label;

    const body = card.querySelector(".status-body");
    body.replaceChildren();
    if (!parsed || typeof parsed !== "object" || parsed.ok === false || !result.ok) {
      const error = document.createElement("div");
      error.className = "status-error";
      error.textContent = result.stderr || (parsed && parsed.error) || result.stdout || "未获得有效 JSON";
      body.append(error);
    } else {
      const rows = componentRows(id, parsed);
      if (!rows.length) rows.push(["状态", "命令已成功响应"]);
      for (const [label, value] of rows) {
        const row = document.createElement("div");
        row.className = "status-row";
        const name = document.createElement("span");
        name.textContent = label;
        const content = document.createElement("strong");
        content.textContent = value;
        row.append(name, content);
        body.append(row);
      }
      const details = document.createElement("details");
      details.className = "status-details";
      const summary = document.createElement("summary");
      summary.textContent = "查看全部 JSON 字段";
      const raw = document.createElement("pre");
      raw.textContent = JSON.stringify(parsed, null, 2);
      details.append(summary, raw);
      body.append(details);
    }
  }
  if (id === "button_angle") {
    const indicator = byId("button-angle-current");
    const angle = parsed && (parsed.applied_angle_deg ?? parsed.configured_angle_deg ?? parsed.button_open_angle_deg);
    indicator.textContent = angle === 90 || angle === 120 ? `${angle}°` : status.label;
  }
  updateSummary();
}

function updateSummary() {
  const total = state.specs.size;
  let healthy = 0;
  for (const [id, result] of state.results) {
    if (!state.specs.has(id)) continue;
    if (componentState(id, result).tone === "good") healthy += 1;
  }
  byId("healthy-count").textContent = total ? `${healthy} / ${total}` : "—";
}

function localErrorResult(args, error) {
  return {
    ok: false,
    args,
    command: `interceptorctl ${args.join(" ")}`,
    returncode: -1,
    started_at: new Date().toISOString(),
    duration_ms: 0,
    stdout: "",
    stderr: error.message || String(error),
    json: null,
  };
}

function appendLog(label, result) {
  byId("log-empty")?.remove();
  const log = byId("command-log");
  const entry = document.createElement("article");
  entry.className = `log-entry ${result.ok ? "ok" : "failed"}`;

  const head = document.createElement("div");
  head.className = "log-entry-head";
  const titleLine = document.createElement("div");
  titleLine.className = "log-entry-title";
  const title = document.createElement("strong");
  title.textContent = label;
  const meta = document.createElement("span");
  const time = result.started_at ? new Date(result.started_at).toLocaleTimeString() : new Date().toLocaleTimeString();
  meta.textContent = `${time} · rc=${result.returncode} · ${result.duration_ms}ms`;
  titleLine.append(title, meta);
  const command = document.createElement("code");
  command.className = "log-command";
  command.textContent = `$ ${result.command}`;
  head.append(titleLine, command);
  entry.append(head);

  const stdout = document.createElement("pre");
  stdout.textContent = result.stdout || "（stdout 为空）";
  entry.append(stdout);
  if (result.stderr) {
    const stderr = document.createElement("pre");
    stderr.className = "stderr";
    stderr.textContent = `[stderr]\n${result.stderr}`;
    entry.append(stderr);
  }
  log.append(entry);
  while (log.querySelectorAll(".log-entry").length > 150) {
    log.querySelector(".log-entry")?.remove();
  }
  log.scrollTop = log.scrollHeight;
}

async function executeCommand(args, label, options = {}) {
  const manageTask = options.manageTask !== false;
  if (manageTask) beginTask();
  let result;
  try {
    result = await api("/api/command", {
      method: "POST",
      body: JSON.stringify({ args }),
    });
  } catch (error) {
    result = localErrorResult(args, error);
  } finally {
    if (manageTask) endTask();
  }
  appendLog(label, result);
  if (options.renderId) renderStatus(options.renderId, result);
  if (!result.ok) toast(`${label}失败，详情见命令日志。`, true);
  return result;
}

async function refreshOne(id, sourceButton = null, manageTask = true) {
  const spec = state.specs.get(id);
  if (!spec) return null;
  if (sourceButton) sourceButton.disabled = true;
  setCardLoading(id, true);
  const result = await executeCommand(spec.args, `刷新组件 · ${spec.label}`, {
    renderId: id,
    manageTask,
  });
  if (sourceButton) sourceButton.disabled = false;
  byId("last-refresh").textContent = new Date().toLocaleTimeString();
  return result;
}

async function refreshAll() {
  if (state.refreshRunning) return;
  state.refreshRunning = true;
  beginTask();
  const button = byId("refresh-all");
  button.disabled = true;
  button.textContent = "正在刷新…";
  for (const id of state.specs.keys()) setCardLoading(id, true);
  try {
    for (const id of state.specs.keys()) await refreshOne(id, null, false);
    byId("last-refresh").textContent = new Date().toLocaleTimeString();
  } catch (error) {
    toast(`刷新组件失败：${error.message}`, true);
    for (const id of state.specs.keys()) setCardLoading(id, false);
  } finally {
    state.refreshRunning = false;
    button.disabled = false;
    button.textContent = "刷新全部组件";
    endTask();
  }
}

async function refreshRelated(value) {
  const ids = Array.isArray(value) ? value : String(value).split(",");
  for (const id of ids.map((item) => item.trim()).filter(Boolean)) await refreshOne(id);
}

async function runFromButton(button) {
  const args = button.dataset.cli.trim().split(/\s+/);
  button.disabled = true;
  const result = await executeCommand(args, button.dataset.label || button.textContent, {
    renderId: button.dataset.status || null,
  });
  if (button.dataset.refreshAfter && result.ok) await refreshRelated(button.dataset.refreshAfter);
  button.disabled = false;
}

function bindForm(id, build) {
  const form = byId(id);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    const built = build(new FormData(form), form);
    if (!built) return;
    submit.disabled = true;
    const result = await executeCommand(built.args, built.label, {
      renderId: built.renderId || null,
    });
    if (built.refreshAfter && result.ok) await refreshRelated(built.refreshAfter);
    submit.disabled = false;
  });
}

function appendAcTiming(args, data) {
  if (data.get("no_wait")) args.push("--no-wait");
  else args.push("--timeout", String(data.get("timeout")));
}

function bindControls() {
  for (const button of document.querySelectorAll(".fixed-command")) {
    button.addEventListener("click", () => runFromButton(button));
  }

  bindForm("button-angle-form", (data) => {
    const angle = String(data.get("angle"));
    return {
      args: ["door", "angle", angle],
      label: `实体按钮开盖角度 · ${angle}°`,
      renderId: "button_angle",
      refreshAfter: ["button_angle"],
    };
  });

  const doorForm = byId("door-form");
  const doorWait = doorForm.elements.wait;
  const doorTimeout = doorForm.elements.timeout;
  doorWait.addEventListener("change", () => {
    doorTimeout.disabled = !doorWait.checked;
    doorTimeout.closest("label").classList.toggle("dimmed", !doorWait.checked);
  });
  bindForm("door-form", (data) => {
    const action = data.get("action");
    const args = ["door", action];
    if (data.get("wait")) args.push("--wait", "--timeout", String(data.get("timeout")));
    return { args, label: action === "open" ? "舱门 · 开门" : "舱门 · 关门", refreshAfter: ["motor", "system"] };
  });

  const motorAction = byId("motor-action");
  const motorForm = byId("motor-form");
  function updateMotorFields() {
    const action = motorAction.value;
    const isTrap = action === "trap";
    const canWait = action === "trap" || action === "home";
    for (const item of document.querySelectorAll(".motor-trap")) item.classList.toggle("hidden", !isTrap);
    for (const item of document.querySelectorAll(".motor-wait, .motor-timeout")) item.classList.toggle("hidden", !canWait);
    motorForm.elements.wait.checked = false;
    motorForm.elements.timeout.value = action === "home" ? "60" : "20";
    motorForm.elements.timeout.disabled = true;
    motorForm.elements.timeout.closest("label").classList.add("dimmed");
  }
  motorAction.addEventListener("change", updateMotorFields);
  motorForm.elements.wait.addEventListener("change", () => {
    motorForm.elements.timeout.disabled = !motorForm.elements.wait.checked;
    motorForm.elements.timeout.closest("label").classList.toggle("dimmed", !motorForm.elements.wait.checked);
  });
  updateMotorFields();
  bindForm("motor-form", (data) => {
    const action = data.get("action");
    const args = ["motor", data.get("target"), action];
    if (action === "trap") {
      args.push("--pos", String(data.get("position")), "--speed", String(data.get("speed")), "--accel", String(data.get("accel")));
    }
    if ((action === "home" || action === "trap") && data.get("wait")) {
      args.push("--wait", "--timeout", String(data.get("timeout")));
    }
    return { args, label: `低层电机 · ${data.get("target")} ${action}`, refreshAfter: ["motor", "system"] };
  });

  bindForm("power-set-form", (data) => ({
    args: ["power", "set", String(data.get("voltage")), String(data.get("current"))],
    label: `电源 · 设置 ${data.get("voltage")} V / ${data.get("current")} A`,
    refreshAfter: ["power", "system"],
  }));
  bindForm("power-raw-form", (data) => ({
    args: ["power", "raw", "--hex", data.get("hex"), "--timeout-ms", String(data.get("timeout")), "--idle-ms", String(data.get("idle"))],
    label: "电源 · 原始 Modbus 传输",
  }));

  bindForm("led-group-form", (data) => ({
    args: ["led", data.get("group"), data.get("color")],
    label: `LED · ${data.get("group")} ${data.get("color")}`,
    refreshAfter: "led",
  }));
  bindForm("led-mask-form", (data) => ({
    args: ["led", "mask", data.get("mask")],
    label: `LED · 写入掩码 ${data.get("mask")}`,
    refreshAfter: "led",
  }));

  for (const form of document.querySelectorAll(".ac-form")) {
    const noWait = form.elements.no_wait;
    noWait.addEventListener("change", () => {
      const timeout = form.elements.timeout;
      timeout.disabled = noWait.checked;
      timeout.closest("label").classList.toggle("dimmed", noWait.checked);
    });
  }
  bindForm("ac-switch-form", (data) => {
    const args = ["ac", data.get("action"), data.get("state")];
    appendAcTiming(args, data);
    return { args, label: `空调 · ${data.get("action")} ${data.get("state")}`, refreshAfter: "ac" };
  });
  bindForm("ac-mode-form", (data) => {
    const args = ["ac", "mode", data.get("mode")];
    appendAcTiming(args, data);
    return { args, label: `空调 · 模式 ${data.get("mode")}`, refreshAfter: "ac" };
  });

  const acRanges = {
    "cool-temp": { min: 20, max: 50, step: 0.1, value: 30 },
    "cool-diff": { min: 1, max: 10, step: 0.1, value: 3 },
    "heat-temp": { min: -40, max: 25, step: 0.1, value: 5 },
    "heat-diff": { min: 5, max: 15, step: 0.1, value: 8 },
    dehumid: { min: 10, max: 90, step: 1, value: 60 },
    humidity: { min: 0, max: 100, step: 1, value: 50 },
  };
  const acValueAction = byId("ac-value-action");
  const acValue = byId("ac-value");
  acValueAction.addEventListener("change", () => {
    const range = acRanges[acValueAction.value];
    acValue.min = range.min;
    acValue.max = range.max;
    acValue.step = range.step;
    acValue.value = range.value;
  });
  bindForm("ac-value-form", (data) => {
    const args = ["ac", data.get("action"), String(data.get("value"))];
    appendAcTiming(args, data);
    return { args, label: `空调 · ${data.get("action")} = ${data.get("value")}`, refreshAfter: "ac" };
  });

  bindForm("aircraft-read-form", (data) => ({
    args: ["aircraft", "read", "--timeout-ms", String(data.get("timeout")), "--max-len", String(data.get("max_len"))],
    label: "航空器 RS485 · 被动读取",
  }));

  const aircraftMode = byId("aircraft-mode");
  function updateAircraftMode() {
    const hex = aircraftMode.value === "hex";
    byId("aircraft-payload").placeholder = hex ? "例如：01 03 00 00" : "输入 UTF-8 文本（可为空）";
    byId("aircraft-ending").classList.toggle("hidden", hex);
  }
  aircraftMode.addEventListener("change", updateAircraftMode);
  updateAircraftMode();
  bindForm("aircraft-xfer-form", (data) => {
    const mode = data.get("mode");
    const payload = String(data.get("payload"));
    if (mode === "hex" && !payload.trim()) {
      toast("十六进制发送内容不能为空。", true);
      return null;
    }
    const args = ["aircraft", "xfer", mode === "hex" ? "--hex" : "--text", payload];
    if (mode === "text" && data.get("append_cr")) args.push("--append-cr");
    if (mode === "text" && data.get("append_lf")) args.push("--append-lf");
    args.push("--timeout-ms", String(data.get("timeout")), "--idle-ms", String(data.get("idle")));
    return { args, label: `航空器 RS485 · ${mode === "hex" ? "十六进制" : "文本"}发送` };
  });
}

function bindNavigation() {
  for (const button of document.querySelectorAll(".nav-item")) {
    button.addEventListener("click", () => {
      document.querySelector(".nav-item.active")?.classList.remove("active");
      document.querySelector(".workspace-panel.active")?.classList.remove("active");
      button.classList.add("active");
      byId(`panel-${button.dataset.panel}`).classList.add("active");
    });
  }
}

function fallbackCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  return copied;
}

function bindLogTools() {
  byId("clear-log").addEventListener("click", () => {
    byId("command-log").replaceChildren();
    const empty = document.createElement("div");
    empty.id = "log-empty";
    empty.className = "log-empty";
    empty.textContent = "命令执行后，CLI 原始 JSON 会显示在这里。";
    byId("command-log").append(empty);
  });
  byId("copy-log").addEventListener("click", async () => {
    const text = [...document.querySelectorAll(".log-entry")].map((item) => item.innerText).join("\n\n");
    if (!text) return toast("当前没有可复制的日志。");
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else if (!fallbackCopy(text)) throw new Error("copy failed");
      toast("日志已复制。");
    } catch (_error) {
      toast("复制失败，请手动选择日志文本。", true);
    }
  });
}

function bindRefresh() {
  byId("refresh-all").addEventListener("click", refreshAll);
  byId("refresh-system").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在刷新…";
    try {
      await refreshRelated([...systemStatusIds]);
    } finally {
      button.disabled = false;
      button.textContent = "刷新本页状态";
    }
  });
  byId("auto-refresh").addEventListener("change", (event) => {
    window.clearInterval(state.autoTimer);
    state.autoTimer = null;
    const seconds = Number(event.target.value);
    if (seconds > 0) {
      state.autoTimer = window.setInterval(() => {
        if (!state.refreshRunning) refreshAll();
      }, seconds * 1000);
      toast(`已启用 ${seconds} 秒自动刷新。`);
    }
  });
}

async function bootstrap() {
  bindNavigation();
  bindControls();
  bindLogTools();
  bindRefresh();
  try {
    state.info = await api("/api/info");
    byId("hostname").textContent = state.info.hostname || "未知设备";
    byId("cli-path").textContent = state.info.cli_path || "—";
    byId("socket-path").textContent = state.info.socket_path || "—";
    const ready = Boolean(state.info.cli_present && state.info.socket_present);
    byId("socket-indicator").className = `status-dot ${ready ? "good" : "bad"}`;
    const specs = state.info.status_commands || [];
    buildStatusCards(specs);
    buildStatusCards(specs, "system-status-grid", systemStatusIds);
    await refreshAll();
  } catch (error) {
    byId("hostname").textContent = "网页后端不可用";
    byId("socket-indicator").className = "status-dot bad";
    toast(`初始化失败：${error.message}`, true);
  }
}

bootstrap();
