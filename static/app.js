const authStatus = document.getElementById("authStatus");
const studiesTableBody = document.getElementById("studiesTableBody");
const studyMeta = document.getElementById("studyMeta");
const instancesList = document.getElementById("instancesList");
const previewImage = document.getElementById("previewImage");
const previewPlaceholder = document.getElementById("previewPlaceholder");
const previewStatus = document.getElementById("previewStatus");
const backendStatus = document.getElementById("backendStatus");
const orthancStatus = document.getElementById("orthancStatus");
const roleInfo = document.getElementById("roleInfo");
const permissionBadges = document.getElementById("permissionBadges");
const uploadSection = document.getElementById("uploadSection");
const uploadForm = document.getElementById("uploadForm");
const uploadStatus = document.getElementById("uploadStatus");
const clearPreviewBtn = document.getElementById("clearPreviewBtn");

let currentUser = null;
let currentPermissions = [];
let currentStudyId = null;
let previewObjectUrl = null;

async function apiFetch(url, options = {}) {
  const hasBody = Boolean(options.body);
  const isFormData = hasBody && options.body instanceof FormData;

  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });

  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return response;
  }

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.message || "請求失敗");
  }
  return data;
}

function bufferDecode(value) {
  let base64 = value.replace(/-/g, '+').replace(/_/g, '/');
  const pad = base64.length % 4;
  if (pad) {
    if (pad === 1) throw new Error('無效的 Base64 字串');
    base64 += new Array(5 - pad).join('=');
  }
  return Uint8Array.from(atob(base64), c => c.charCodeAt(0));
}

function bufferEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let str = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    str += String.fromCharCode(bytes[i]);
  }
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function hasPermission(permission) {
  return currentPermissions.includes(permission);
}

function setStatusBox(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("err", isError);
  element.classList.toggle("ok", !isError);
}

function setAuthMessage(message, isError = false) {
  setStatusBox(authStatus, message, isError);
}

function setUploadMessage(message, isError = false) {
  setStatusBox(uploadStatus, message, isError);
}

function setPreviewMessage(message, isError = false) {
  setStatusBox(previewStatus, message, isError);
}

function resetPreview(message = "尚未選擇預覽影像") {
  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }

  previewImage.hidden = true;
  previewImage.removeAttribute("src");
  previewPlaceholder.hidden = false;
  previewPlaceholder.textContent = message;
  setPreviewMessage(message === "尚未選擇預覽影像" ? "尚未預覽" : message, message !== "尚未選擇預覽影像");
}

function resetDetail() {
  currentStudyId = null;
  studyMeta.innerHTML = '<div class="meta-empty">尚未選擇 study</div>';
  instancesList.innerHTML = '<div class="meta-empty">尚未載入 instance</div>';
  resetPreview();
}

function renderRoleInfo() {
  const infoEl = document.getElementById("roleInfo");
  const badgesEl = document.getElementById("permissionBadges");
  const adminSection = document.getElementById("adminSection"); // 抓取管理面板
  if (!currentUser) {
    roleInfo.innerHTML = '<span class="muted">尚未登入</span>';
    permissionBadges.innerHTML = '<span class="permission-chip chip-muted">請先登入</span>';
    uploadSection.hidden = true;
    adminSection.hidden = true;
    return;
  }

  roleInfo.innerHTML = `<strong>${escapeHtml(currentUser.username)}</strong> / ${escapeHtml(currentUser.role)}`;
  permissionBadges.innerHTML = currentPermissions
    .map((permission) => `<span class="permission-chip">${escapeHtml(permission)}</span>`)
    .join("");

  uploadSection.hidden = !hasPermission("upload");

  //檢查是否為管理員，是的話就秀出後台，並載入使用者清單
  if (currentUser.role === 'admin' || currentPermissions.includes('manage_users')) {
      adminSection.hidden = false;
      loadAdminUsers(); // 呼叫載入清單 API
  } else {
      adminSection.hidden = true;
  }
}

function renderStudies(studies) {
  if (!studies.length) {
    studiesTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">Orthanc 目前沒有 study</td></tr>';
    return;
  }

  studiesTableBody.innerHTML = studies
    .map(
      (study) => `
        <tr>
          <td>${escapeHtml(study.id)}</td>
          <td>
            ${escapeHtml(study.patient_name || "-")}<br>
            <span class="muted">${escapeHtml(study.patient_id || "")}</span>
          </td>
          <td>${escapeHtml(study.study_date || "-")}</td>
          <td>${escapeHtml(study.study_description || "-")}</td>
          <td>${escapeHtml(study.instances_count)}</td>
          <td>
            <button class="primary-btn inline-btn" onclick="loadStudyDetail('${escapeHtml(study.id)}')">查看</button>
          </td>
        </tr>
      `
    )
    .join("");
}

function renderStudyMeta(study) {
  const items = [
    ["Study ID", study.id],
    ["Patient Name", study.patient_name || "-"],
    ["Patient ID", study.patient_id || "-"],
    ["Study Date", study.study_date || "-"],
    ["Study Description", study.study_description || "-"],
    ["Study UID", study.study_instance_uid || "-"],
    ["Instance 數量", String(study.instances_count ?? "-")],
  ];

  studyMeta.innerHTML = items
    .map(
      ([label, value]) => `
        <div class="meta-item">
          <div class="meta-label">${escapeHtml(label)}</div>
          <div class="meta-value">${escapeHtml(value)}</div>
        </div>
      `
    )
    .join("");
}

function renderInstances(instances) {
  if (!instances.length) {
    instancesList.innerHTML = '<div class="meta-empty">這個 study 沒有 instance</div>';
    return;
  }

  instancesList.innerHTML = instances
    .map((instance) => {
      const instanceId = escapeHtml(instance.id);
      const extraButtons = [
        hasPermission("modify")
          ? `<button class="secondary-btn inline-btn" onclick="modifyInstance('${instanceId}')">修改</button>`
          : "",
        hasPermission("delete")
          ? `<button class="danger-btn inline-btn" onclick="deleteInstance('${instanceId}')">移除</button>`
          : "",
      ]
        .filter(Boolean)
        .join("");

      return `
        <div class="instance-card">
          <div><strong>Instance ID：</strong>${instanceId}</div>
          <div><strong>Series：</strong>${escapeHtml(instance.series_description || "-")}（${escapeHtml(instance.series_number || "-")}）</div>
          <div><strong>Instance Number：</strong>${escapeHtml(instance.instance_number || "-")}</div>
          <div><strong>Modality：</strong>${escapeHtml(instance.modality || "-")}</div>
          <div><strong>SOP UID：</strong>${escapeHtml(instance.sop_instance_uid || "-")}</div>
          <div class="instance-actions">
            <button class="primary-btn inline-btn" onclick="previewInstance('${instanceId}')">預覽</button>
            <button class="secondary-btn inline-btn" onclick="downloadInstance('${instanceId}')">下載 DICOM</button>
            ${extraButtons}
          </div>
        </div>
      `;
    })
    .join("");
}

async function checkHealth() {
  try {
    const data = await apiFetch("/api/health", { method: "GET" });
    backendStatus.textContent = data.status.backend;
    orthancStatus.textContent = data.status.orthanc;
    backendStatus.className = data.status.backend === "ok" ? "ok" : "err";
    orthancStatus.className = data.status.orthanc === "ok" ? "ok" : "err";
  } catch {
    backendStatus.textContent = "error";
    orthancStatus.textContent = "error";
    backendStatus.className = "err";
    orthancStatus.className = "err";
  }
}

async function refreshMe() {
  try {
    const data = await apiFetch("/api/me", { method: "GET" });
    currentUser = data.user;
    currentPermissions = data.permissions || [];
    setAuthMessage(`已登入：${data.user.username}（${data.role_label}）`);
  } catch {
    currentUser = null;
    currentPermissions = [];
    setAuthMessage("尚未登入", true);
  }

  renderRoleInfo();
}

// ==========================================
// 👑 管理員後台專用邏輯
// ==========================================

// 1. 載入並渲染所有使用者清單
async function loadAdminUsers() {
  const tbody = document.getElementById("usersTableBody");
  try {
    const res = await apiFetch("/api/admin/users", { method: "GET" });
    if (!res.ok) throw new Error(res.message);

    tbody.innerHTML = "";
    if (res.users.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-cell">目前系統無任何帳號</td></tr>';
      return;
    }

    res.users.forEach(u => {
      const tr = document.createElement("tr");
      // 用 emoji 標示綁定狀態
      const fidoStatus = u.has_fido ? "✅ 已綁定" : "❌ 未註冊";
      // 避免管理員刪除/重置自己
      const isSelf = (currentUser && currentUser.username === u.username);
      
      // 動態產生操作按鈕
      let actionButtons = "";
      if (isSelf) {
          actionButtons = `<span class="muted" style="font-size:0.8rem;">(目前登入中)</span>`;
      } else {
          // 如果已經綁定人臉，就顯示「清除人臉」按鈕
          const unbindBtn = u.has_fido 
            ? `<button class="secondary-btn inline-btn" onclick="resetUserFido('${u.username}')" style="margin-right: 8px;">清除人臉</button>` 
            : "";
          const deleteBtn = `<button class="danger-btn inline-btn" onclick="deleteUser('${u.username}')">刪除帳號</button>`;
          
          actionButtons = unbindBtn + deleteBtn;
      }

      tr.innerHTML = `
        <td><strong>${u.username}</strong></td>
        <td><span class="permission-chip">${u.role}</span></td>
        <td>${fidoStatus}</td>
        <td>${actionButtons}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell" style="color:red;">載入失敗：${err.message}</td></tr>`;
  }
}

// 2. 刪除使用者
async function deleteUser(username) {
  if (!confirm(`⚠️ 警告：確定要徹底刪除帳號 [${username}] 與其綁定的人臉資料嗎？此操作無法還原！`)) return;
  
  try {
    const res = await apiFetch(`/api/admin/users/${username}`, { method: "DELETE" });
    if (res.ok) {
      alert(res.message);
      loadAdminUsers(); // 重新整理表格
    } else {
      alert(`刪除失敗: ${res.message}`);
    }
  } catch (err) {
    alert(`系統錯誤: ${err.message}`);
  }
}

// 3. 清除使用者人臉綁定 (保留帳號)
async function resetUserFido(username) {
  if (!confirm(`確定要清除帳號 [${username}] 的人臉綁定紀錄嗎？\n(帳號與權限將會保留，該人員下次操作需重新綁定人臉)`)) return;
  
  try {
    const res = await apiFetch(`/api/admin/users/${username}/fido`, { method: "DELETE" });
    if (res.ok) {
      alert(res.message);
      loadAdminUsers(); // 重新整理表格
    } else {
      alert(`清除失敗: ${res.message}`);
    }
  } catch (err) {
    alert(`系統錯誤: ${err.message}`);
  }
}

async function loadStudies() {
  try {
    const data = await apiFetch("/api/studies", { method: "GET" });
    renderStudies(data.data || []);
  } catch (error) {
    studiesTableBody.innerHTML = `<tr><td colspan="6" class="empty-cell err">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadStudyDetail(studyId) {
  currentStudyId = studyId;
  resetPreview();

  try {
    const data = await apiFetch(`/api/studies/${encodeURIComponent(studyId)}`, { method: "GET" });
    renderStudyMeta(data.study);
    renderInstances(data.instances || []);
  } catch (error) {
    studyMeta.innerHTML = `<div class="meta-empty err">${escapeHtml(error.message)}</div>`;
    instancesList.innerHTML = '<div class="meta-empty">尚未載入 instance</div>';
  }
}

window.loadStudyDetail = loadStudyDetail;

async function previewInstance(instanceId) {
  resetPreview("預覽載入中...");
  setPreviewMessage("預覽載入中...");

  try {
    const response = await fetch(`/api/instances/${encodeURIComponent(instanceId)}/preview?t=${Date.now()}`, {
      method: "GET",
      credentials: "same-origin",
    });

    if (!response.ok) {
      let message = "此 DICOM 目前無法預覽";
      const contentType = response.headers.get("content-type") || "";

      if (contentType.includes("application/json")) {
        const data = await response.json();
        message = data.message || message;
      }

      throw new Error(message);
    }

    const blob = await response.blob();

    if (!blob || blob.size === 0) {
      throw new Error("預覽圖內容是空的");
    }

    const objectUrl = URL.createObjectURL(blob);
    const testImage = new Image();

    testImage.onload = () => {
      if (previewObjectUrl) {
        URL.revokeObjectURL(previewObjectUrl);
      }

      previewObjectUrl = objectUrl;
      previewImage.src = previewObjectUrl;
      previewImage.hidden = false;
      previewPlaceholder.hidden = true;
      setPreviewMessage(`預覽成功：${instanceId}`);
    };

    testImage.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      resetPreview("此 DICOM 目前無法預覽");
      setPreviewMessage("此 DICOM 回傳內容不是瀏覽器可顯示的圖片", true);
    };

    testImage.src = objectUrl;
  } catch (error) {
    resetPreview(error.message || "此 DICOM 目前無法預覽");
  }
}

window.previewInstance = previewInstance;

function downloadInstance(instanceId) {
  window.open(`/api/instances/${encodeURIComponent(instanceId)}/download`, "_blank");
}

window.downloadInstance = downloadInstance;

async function modifyInstance(instanceId) {
  const patientName = window.prompt("輸入新的 Patient Name（可留空）", "");
  if (patientName === null) {
    return;
  }

  const studyDescription = window.prompt("輸入新的 Study Description（可留空）", "");
  if (studyDescription === null) {
    return;
  }

  if (!patientName.trim() && !studyDescription.trim()) {
    window.alert("至少要填一個欄位才能修改。");
    return;
  }

  try {
    const data = await apiFetch(`/api/instances/${encodeURIComponent(instanceId)}/modify`, {
      method: "POST",
      body: JSON.stringify({
        patient_name: patientName,
        study_description: studyDescription,
        replace_original: true,
      }),
    });

    window.alert(data.message);
    await loadStudies();

    if (currentStudyId) {
      await loadStudyDetail(currentStudyId);
    }
  } catch (error) {
    window.alert(error.message);
  }
}

window.modifyInstance = modifyInstance;

async function deleteInstance(instanceId) {
  const confirmed = window.confirm("確定要移除這個 DICOM instance 嗎？");
  if (!confirmed) {
    return;
  }

  try {
    const data = await apiFetch(`/api/instances/${encodeURIComponent(instanceId)}`, {
      method: "DELETE",
    });

    window.alert(data.message);
    await loadStudies();

    if (currentStudyId) {
      await loadStudyDetail(currentStudyId);
    }
  } catch (error) {
    window.alert(error.message);
  }
}

window.deleteInstance = deleteInstance;

/*document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value.trim();

  try {
    const data = await apiFetch("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });

    currentUser = data.user;
    currentPermissions = data.permissions || [];
    setAuthMessage(`登入成功：${data.user.username}（${data.role_label}）`);
    renderRoleInfo();
    resetDetail();
  } catch (error) {
    currentUser = null;
    currentPermissions = [];
    setAuthMessage(error.message, true);
    renderRoleInfo();
  }
});*/
async function doAction(type) {
  const isReg = type === 'register';
  // 1. 【關鍵】取得使用者輸入的帳號
  const usernameInput = document.getElementById("fidoUsername").value.trim();
  
  if (!usernameInput) {
      setAuthMessage("❌ 請先輸入操作帳號！", true);
      return;
  }

  try {
    setAuthMessage(`正在初始化${isReg ? '註冊' : '驗證'}...`);

    // 2. 【關鍵】將 username 作為 Payload 傳給後端
    const beginRes = await apiFetch(`/api/${type}/begin`, { 
        method: 'POST',
        body: JSON.stringify({ username: usernameInput }) // 傳送帳號
    });
    
    const options = beginRes; 

    // --- 翻譯選項 ---
    options.challenge = bufferDecode(options.challenge);
    if (isReg) {
      options.user.id = bufferDecode(options.user.id);
    } else if (options.allowCredentials) {
      options.allowCredentials.forEach(c => c.id = bufferDecode(c.id));
    }

    setAuthMessage("請看向鏡頭進行掃描...");

    // --- 呼叫硬體 ---
    const credential = isReg 
        ? await navigator.credentials.create({ publicKey: options })
        : await navigator.credentials.get({ publicKey: options });

    setAuthMessage("正在進行資安校驗...");
    
    // --- 打包成績單 ---
    const body = {
      // 這裡也要把 username 傳過去，讓後端知道是誰的成績單
      username: usernameInput, 
      id: credential.id,
      rawId: bufferEncode(credential.rawId),
      type: credential.type,
      response: isReg ? {
          attestationObject: bufferEncode(credential.response.attestationObject),
          clientDataJSON: bufferEncode(credential.response.clientDataJSON)
      } : {
          authenticatorData: bufferEncode(credential.response.authenticatorData),
          clientDataJSON: bufferEncode(credential.response.clientDataJSON),
          signature: bufferEncode(credential.response.signature),
          userHandle: credential.response.userHandle ? bufferEncode(credential.response.userHandle) : null
      }
    };

    // --- 收卷校驗 ---
    const completeRes = await apiFetch(`/api/${type}/complete`, {
        method: 'POST',
        body: JSON.stringify(body)
    });

    if (completeRes.ok || completeRes.status === "success") {
        if (isReg) {
            setAuthMessage(`✅ 帳號 [${usernameInput}] 人臉綁定成功！`);
            document.getElementById("fidoUsername").value = ""; // 清空輸入框
            registeredUsersList.push(usernameInput);
        } else {
            currentUser = completeRes.user;
            currentPermissions = completeRes.permissions || [];
            setAuthMessage(`✅ FIDO2 登入成功：${currentUser.username}（${completeRes.role_label}）`);
            renderRoleInfo();
            resetDetail();
            await loadStudies(); 
        }
    }
  } catch (error) {
    currentUser = null;
    currentPermissions = [];
    setAuthMessage(`❌ 錯誤: ${error.message}`, true);
    renderRoleInfo();
  }
}

// ==========================================
// 🧠 智慧單一按鈕與聯想清單邏輯
// ==========================================

// 用來儲存「已經綁定過人臉」的帳號清單
let registeredUsersList = [];

// 當網頁載入時，自動去後端撈取已註冊的帳號清單
document.addEventListener("DOMContentLoaded", async () => {
    try {
        const res = await apiFetch('/api/users/registered', { method: 'GET' });
        if (res.ok && res.users) {
            registeredUsersList = res.users; // 存入記憶體供按鈕判斷使用
            
            // 將名單塞入 HTML 的 datalist 中，製作下拉聯想效果
            const datalist = document.getElementById('registered-users');
            res.users.forEach(username => {
                const option = document.createElement('option');
                option.value = username;
                datalist.appendChild(option);
            });
        }
    } catch (err) {
        console.error("無法載入帳號聯想清單:", err);
    }
});

// 綁定智慧按鈕事件
document.getElementById("smart-login-btn").addEventListener("click", () => {
    const usernameInput = document.getElementById("fidoUsername").value.trim();
    
    if (!usernameInput) {
        setAuthMessage("❌ 請先輸入操作帳號！", true);
        return;
    }

    // 關鍵判斷：輸入的帳號有沒有在已註冊清單裡面？
    if (registeredUsersList.includes(usernameInput)) {
        // 在清單內 -> 已經綁過人臉，直接執行登入 (authenticate)
        doAction('authenticate');
    } else {
        // 不在清單內 -> 尚未綁過人臉，執行註冊綁定 (register)
        doAction('register');
    }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    const data = await apiFetch("/api/logout", { method: "POST" });

    currentUser = null;
    currentPermissions = [];
    setAuthMessage(data.message);
    setUploadMessage("等待上傳");
    renderRoleInfo();

    studiesTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">尚未載入資料</td></tr>';
    resetDetail();
  } catch (error) {
    setAuthMessage(error.message, true);
  }
});

document.getElementById("loadStudiesBtn").addEventListener("click", loadStudies);
document.getElementById("refreshHealthBtn").addEventListener("click", checkHealth);
clearPreviewBtn.addEventListener("click", () => resetPreview());

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const fileInput = document.getElementById("dicomFile");
  const file = fileInput.files[0];

  if (!file) {
    setUploadMessage("請先選擇 .dcm 檔案", true);
    return;
  }

  const formData = new FormData();
  formData.append("dicom", file);

  try {
    const data = await apiFetch("/api/instances/upload", {
      method: "POST",
      body: formData,
    });

    setUploadMessage(data.message);
    uploadForm.reset();
    await loadStudies();
  } catch (error) {
    setUploadMessage(error.message, true);
  }
});

// 綁定「新增/修改帳號」表單
const adminUserForm = document.getElementById("adminUserForm");
if (adminUserForm) {
  adminUserForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("newUsername").value.trim();
    const role = document.getElementById("newUserRole").value;

    try {
      const res = await apiFetch("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ username, role })
      });

      if (res.ok) {
        alert(res.message);
        document.getElementById("newUsername").value = ""; // 清空輸入框
        loadAdminUsers(); // 重新整理表格
      } else {
        alert(`設定失敗: ${res.message}`);
      }
    } catch (err) {
      alert(`系統錯誤: ${err.message}`);
    }
  });
}

checkHealth();
refreshMe();
resetDetail();
setUploadMessage("等待上傳");
