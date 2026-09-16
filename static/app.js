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
  const adminLogsSection = document.getElementById("adminLogsSection"); // 抓取日誌區塊
  const patientCameraSection = document.getElementById("patientCameraSection"); // 抓取病患相機面板

  // 1. 處理「尚未登入」的狀態
  if (!currentUser) {
    roleInfo.innerHTML = '<span class="muted">尚未登入</span>';
    permissionBadges.innerHTML = '<span class="permission-chip chip-muted">請先登入</span>';
    uploadSection.hidden = true;
    adminSection.hidden = true;
    if (adminLogsSection) adminLogsSection.hidden = true; // 未登入時隱藏日誌
    if (patientCameraSection) patientCameraSection.hidden = true; // 未登入時隱藏相機面板
    return;
  }

  // 2. 處理「已登入」的使用者資訊與權限標籤
  roleInfo.innerHTML = `<strong>${escapeHtml(currentUser.username)}</strong> / ${escapeHtml(currentUser.role)}`;
  permissionBadges.innerHTML = currentPermissions
    .map((permission) => `<span class="permission-chip">${escapeHtml(permission)}</span>`)
    .join("");

  // 3. 處理「上傳權限」面板顯示與否
  uploadSection.hidden = !hasPermission("upload");

  // 4. 處理「管理員專屬面板 (帳號管理 + 系統日誌)」顯示與否
  if (currentUser.role === 'admin' || currentPermissions.includes('manage_users')) {
      adminSection.hidden = false;
      if (adminLogsSection) adminLogsSection.hidden = false; // 顯示日誌
      
      loadAdminUsers(); // 呼叫載入清單 API
      loadSystemLogs(); // 呼叫載入日誌 API
  } else {
      adminSection.hidden = true;
      if (adminLogsSection) adminLogsSection.hidden = true; // 隱藏日誌
  }
  // 5. 處理「病患專屬相機面板」顯示與否，並自動填入 ID
  if (currentUser.role === 'patient') {
      if (patientCameraSection) {
          patientCameraSection.hidden = false;
          // 自動把目前登入的病患帳號填入輸入框，省去手動輸入
          document.getElementById("patientId").value = currentUser.username;
      }
  } else {
      if (patientCameraSection) patientCameraSection.hidden = true;
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

  // 如果確認是登入狀態，就自動幫忙把清單載入回來
  if (currentUser) {
      await loadStudies();
  }
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
      refreshRegisteredUsers();// 管理員清除別人人臉後，前端聯想名單立刻更新
    } else {
      alert(`清除失敗: ${res.message}`);
    }
  } catch (err) {
    alert(`系統錯誤: ${err.message}`);
  }
}

async function loadStudies() {
    // 1. 抓取表格的 tbody 元素
    const tbody = document.getElementById("studiesTableBody");
    if (!tbody) return;
    
    // 2. 顯示載入中的提示
    tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">載入中...</td></tr>';
    
    try {
        // 3. 向後端請求 DICOM 清單
        const res = await apiFetch("/api/studies", { method: "GET" });
        
        if (res.ok && res.data) {
            tbody.innerHTML = ""; // 清空表格
            
            if (res.data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">目前沒有任何影像資料</td></tr>';
                return;
            }
            
            // 4. 迴圈處理每一筆 DICOM 檔案
            res.data.forEach(study => {
                const tr = document.createElement("tr");
                
                // 只有 admin 和 doctor 看得到「改名按鈕」
                let renameBtn = "";
                if (currentUser && (currentUser.role === 'admin' || currentUser.role === 'doctor')) {
                    renameBtn = `<button class="secondary-btn inline-btn" onclick="renameDicomStudy('${study.id}', '${escapeHtml(study.patient_name)}')" style="margin-left: 8px; font-size: 0.8rem;">✏️ 改名</button>`;
                }

                // 準備縮圖的 HTML
                // 呼叫我們後端既有的 preview API，如果載入失敗，就顯示灰底文字
                let thumbnailHtml = '<div class="thumb-placeholder muted">無影像</div>';
                if (study.cover_instance_id) {
                    const previewUrl = `/api/instances/${study.cover_instance_id}/preview`;
                    thumbnailHtml = `<img src="${previewUrl}" class="study-thumbnail" loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                                     <div class="thumb-placeholder muted" style="display:none;">無法預覽</div>`;
                }
                
                // 組合該列的 HTML (配合 HTML 新增的縮圖欄位)
                tr.innerHTML = `
                    <td style="width: 80px; text-align: center;">
                        ${thumbnailHtml}
                    </td>
                    <td>
                        <strong>${escapeHtml(study.patient_name || "無名稱")}</strong> 
                        ${renameBtn} <br>
                        <span class="muted" style="font-size: 0.85rem;">${escapeHtml(study.patient_id || "未知")}</span>
                    </td>
                    <td>${escapeHtml(study.study_date)}</td>
                    <td>${escapeHtml(study.study_description || "-")}</td>
                    <td>${study.instances_count}</td>
                    <td>
                        <button class="primary-btn inline-btn" style="white-space: nowrap;" onclick="loadStudyDetail('${study.id}')">查看影像</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            
        } else {
            tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">載入失敗：${res.message}</td></tr>`;
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">系統錯誤：${err.message}</td></tr>`;
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
            // 註冊專屬動作：加進已知清單、清空輸入框
            registeredUsersList.push(usernameInput);
            document.getElementById("fidoUsername").value = ""; 
        }
        
        // 不論是「剛註冊完」還是「單純登入」，後端都會給 user 資訊，直接更新畫面！
        currentUser = completeRes.user;
        currentPermissions = completeRes.permissions || [];
        
        const actionName = isReg ? "註冊並自動登入" : "登入";
        setAuthMessage(`✅ FIDO2 ${actionName}成功：${currentUser.username}（${completeRes.role_label}）`);
        
        renderRoleInfo();
        resetDetail();
        await loadStudies();
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

async function refreshRegisteredUsers() {
    try {
        const res = await apiFetch(`/api/users/registered?t=${Date.now()}`, { method: 'GET' });
        if (res.ok && res.users) {
            registeredUsersList = res.users; // 更新記憶體名單
            
            const datalist = document.getElementById('registered-users');
            datalist.innerHTML = ''; // 先清空舊的選項，避免重複疊加
            
            res.users.forEach(username => {
                const option = document.createElement('option');
                option.value = username;
                datalist.appendChild(option);
            });
        }
    } catch (err) {
        console.error("無法載入帳號聯想清單:", err);
    }
}

// 網頁載入時，呼叫一次
document.addEventListener("DOMContentLoaded", refreshRegisteredUsers);

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
  let isReset = false;

  // 1. 如果登出的是 guest 帳號，跳出選擇視窗
  if (currentUser && currentUser.username === 'guest') {
    // confirm 會跳出內建的對話框，按「確定」回傳 true，按「取消」回傳 false
    isReset = confirm("【測試專用】\n您正在登出 guest 帳號。是否要一併「清除人臉紀錄」，以便下一位測試？\n\n👉 按 [確定]：登出並清除人臉\n👉 按 [取消]：僅一般登出 (保留人臉)");
  }

  try {
    // 2. 把 isReset 的結果包成 JSON 傳給後端
    const data = await apiFetch("/api/logout", { 
        method: "POST",
        body: JSON.stringify({ reset_guest: isReset })
    });

    // 3. 執行後續的畫面清空動作 (維持原樣)
    currentUser = null;
    currentPermissions = [];
    setAuthMessage(data.message);
    setUploadMessage("等待上傳");
    renderRoleInfo();

    studiesTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">尚未載入資料</td></tr>';
    resetDetail();

    // 登出成功後，立刻重新向後端要一次最新的已註冊名單！
    await refreshRegisteredUsers();
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

// 修改 DICOM 檔案名稱
async function renameDicomStudy(studyId, oldName) {
    // 1. 跳出內建的輸入框讓使用者填寫新名字
    const newName = prompt(`請輸入新的病患名稱\n(原名稱: ${oldName}):`, oldName);
    
    // 如果按取消，或是沒有改變，就直接結束
    if (!newName || newName.trim() === "" || newName === oldName) {
        return; 
    }
    
    try {
        // 2. 呼叫我們剛寫好的 Flask 修改 API
        const res = await apiFetch(`/api/studies/${studyId}/modify`, {
            method: "POST",
            body: JSON.stringify({ new_patient_name: newName.trim() })
        });
        
        if (res.ok) {
            alert(res.message);
            // 3. 修改成功後，重新載入畫面上的 DICOM 清單
            await loadStudies();
        } else {
            alert(`修改失敗: ${res.message}`);
        }
    } catch (err) {
        alert(`系統錯誤: ${err.message}`);
    }
}

// ==========================================
// 📜 系統日誌 (Admin Only)
// ==========================================
async function loadSystemLogs() {
    const tbody = document.getElementById("logsTableBody");
    if (!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">載入中...</td></tr>';
    
    try {
        const res = await apiFetch("/api/admin/logs", { method: "GET" });
        if (res.ok) {
            if (res.logs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">目前尚無任何紀錄</td></tr>';
                return;
            }
            
            tbody.innerHTML = res.logs.map(log => `
                <tr>
                    <td style="white-space: nowrap; font-size: 0.85rem;" class="muted">${escapeHtml(log.timestamp)}</td>
                    <td><strong>${escapeHtml(log.username)}</strong></td>
                    <td><span class="permission-chip chip-muted">${escapeHtml(log.action)}</span></td>
                    <td style="word-break: break-word;">${escapeHtml(log.details)}</td>
                    <td style="font-size: 0.85rem;" class="muted">${escapeHtml(log.ip_address)}</td>
                </tr>
            `).join("");
        }
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-cell err">載入失敗：${err.message}</td></tr>`;
    }
}

// ==========================================
// 🔍 Study 清單即時搜尋功能
// ==========================================
const studySearchInput = document.getElementById("studySearchInput");

if (studySearchInput) {
    // 當使用者在搜尋框打字的瞬間 (input 事件) 就會觸發
    studySearchInput.addEventListener("input", function(e) {
        // 1. 取得使用者輸入的關鍵字，並全部轉成小寫 (避免大小寫差異找不到)
        const searchTerm = e.target.value.toLowerCase().trim();
        
        // 2. 抓取 Study 清單表格裡面的所有資料列 (tr)
        const studyTableBody = document.querySelector("#studiesTableBody");
        if (!studyTableBody) return;
        
        const rows = studyTableBody.querySelectorAll("tr");

        // 3. 逐列檢查
        rows.forEach(row => {
            // 如果是空資料的提示文字 ("尚未載入資料"等)，就跳過不處理
            if (row.classList.contains("empty-cell") || row.querySelector("td[colspan]")) return;

            // 將整列的文字內容 (包含病患名稱、ID、日期等) 抓出來轉小寫
            const rowText = row.textContent.toLowerCase();

            // 4. 比對！如果這列的文字包含關鍵字，就顯示；否則就隱藏
            if (rowText.includes(searchTerm)) {
                row.style.display = ""; // 恢復顯示
            } else {
                row.style.display = "none"; // 隱藏
            }
        });
    });
}

// 綁定重新整理按鈕
const refreshLogsBtn = document.getElementById("refreshLogsBtn");
if(refreshLogsBtn) refreshLogsBtn.addEventListener("click", loadSystemLogs);

checkHealth();
refreshMe();
resetDetail();
setUploadMessage("等待上傳");
