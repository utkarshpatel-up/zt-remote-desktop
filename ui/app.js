// Desktop window of the host app. Talks to Rust through Tauri commands.
// On Android / iOS the app is the viewer instead.
if (/Android|iPhone|iPad/i.test(navigator.userAgent)) location.replace("viewer.html");
const { invoke } = window.__TAURI__.core;
const $ = id => document.getElementById(id);
let state = null, showPw = false, editingAv = false;

function msg(text, bad) { $("msg").textContent = text || ""; $("msg").style.color = bad ? "var(--bad)" : ""; }
async function call(cmd, args) {
  try { const r = await invoke(cmd, args); return r; }
  catch (e) { msg(String(e), true); throw e; }
}
const fmtTime = s => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function render(s) {
  state = s;
  $("ver").textContent = "v" + s.version + (s.demo ? " · DEMO" : "");

  // status
  const dot = $("statusDot");
  if (s.error) { dot.className = "dot bad"; $("statusText").textContent = s.error; }
  else if (s.listening.length) { dot.className = "dot ok"; $("statusText").textContent = "Ready. On your phone (with ZeroTier on), open:"; }
  else if (s.waiting_for_zerotier) { dot.className = "dot warn"; $("statusText").textContent = "Waiting for ZeroTier to connect… check the ZeroTier app shows this computer as Connected."; }
  else { dot.className = "dot"; $("statusText").textContent = "Starting…"; }
  $("addrs").textContent = s.listening.join("\n");

  $("pw").textContent = showPw ? s.password : "•".repeat(Math.min(12, s.password.length));
  $("pwShow").textContent = showPw ? "Hide" : "Show";

  // AV Backup
  $("avStatus").textContent = s.av_server
    ? `On: team members sign in with their AV Backup e-mail and password (server ${s.av_server}, from ${s.av_source}).`
    : "Off: AV Backup isn't set up on this computer. Enter the team server address to turn it on.";
  if (!editingAv) {
    $("avServer").value = s.av_server_setting;
    $("roleAdmin").checked = s.roles.includes("admin");
    $("roleOperator").checked = s.roles.includes("operator");
  }

  // sessions
  $("sessions").innerHTML = "";
  if (!s.sessions.length) $("sessions").textContent = "Nobody";
  else {
    const ul = document.createElement("ul");
    for (const x of s.sessions) {
      const li = document.createElement("li");
      li.textContent = `${x.who} — ${x.ip} — since ${fmtTime(x.since)}`;
      ul.appendChild(li);
    }
    $("sessions").appendChild(ul);
  }
  $("sessions").classList.toggle("muted", !s.sessions.length);
  $("kick").disabled = !s.sessions.length;

  // update
  $("updateBox").classList.toggle("hidden", !s.update);
  if (s.update) {
    $("updateText").textContent = `Version ${s.update.version} is available (you have ${s.version}).`;
    $("updateBtn").classList.toggle("hidden", !s.can_self_update);
  }

  // macOS permissions
  const p = s.permissions;
  const needPerm = p.screen === false || p.accessibility === false;
  $("permBox").classList.toggle("hidden", !needPerm);
  if (needPerm) {
    $("permScreen").textContent = (p.screen ? "✓" : "✗") + " Screen Recording (to see the screen)";
    $("permAx").textContent = (p.accessibility ? "✓" : "✗") + " Accessibility (to move the mouse and type)";
    $("permScreenBtn").classList.toggle("hidden", !!p.screen);
    $("permAxBtn").classList.toggle("hidden", !!p.accessibility);
  }

  $("cfgPath").textContent = "Settings: " + s.config_path;
  $("log").textContent = s.log.join("\n");
}

async function refresh() { try { render(await invoke("get_state")); } catch (_) {} }

$("pwShow").onclick = () => { showPw = !showPw; state && render(state); };
$("pwCopy").onclick = async () => {
  try { await navigator.clipboard.writeText(state.password); msg("Password copied."); }
  catch (_) { showPw = true; render(state); msg("Copy it from the box above."); }
};
$("pwSet").onclick = async () => {
  await call("set_password", { password: $("pwNew").value });
  $("pwNew").value = ""; msg("Password changed. Phones must use the new one."); refresh();
};
$("pwRandom").onclick = async () => {
  await call("set_password", { password: null }); showPw = true; msg("New random password set."); refresh();
};
["avServer", "roleAdmin", "roleOperator"].forEach(id => $(id).addEventListener("input", () => editingAv = true));
$("avSave").onclick = async () => {
  const roles = [];
  if ($("roleAdmin").checked) roles.push("admin");
  if ($("roleOperator").checked) roles.push("operator");
  await call("set_av_backup", { server: $("avServer").value, roles });
  editingAv = false; msg("AV Backup sign-in saved."); refresh();
};
$("kick").onclick = async () => { await call("disconnect_all"); msg("Everyone was disconnected."); };
$("autostart").onchange = async e => {
  try { e.target.checked = await call("autostart", { enable: e.target.checked }); } catch (_) { e.target.checked = !e.target.checked; }
};
$("checkUpd").onclick = async () => {
  msg("Checking…");
  const u = await call("check_update");
  msg(u ? `Version ${u.version} is available.` : "You have the latest version."); refresh();
};
$("updateBtn").onclick = async () => {
  $("updateBtn").disabled = true; msg("Downloading the update… the app restarts by itself.");
  try { await call("install_update"); } finally { $("updateBtn").disabled = false; }
};
$("updateLink").onclick = () => call("open_link", { which: "releases" });
$("permScreenBtn").onclick = async () => { await call("request_screen_access"); call("open_link", { which: "screen" }); };
$("permAxBtn").onclick = () => call("open_link", { which: "accessibility" });

call("autostart", { enable: null }).then(v => $("autostart").checked = v).catch(() => {});
refresh();
setInterval(refresh, 1500);
