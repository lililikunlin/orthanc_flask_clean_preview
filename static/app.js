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
  if (!currentUser) {
    roleInfo.innerHTML = '<span class="muted">尚未登入</span>';
    permissionBadges.innerHTML = '<span class="permission-chip chip-muted">請先登入</span>';
    uploadSection.hidden = true;
    return;
  }

  roleInfo.innerHTML = `<strong>${escapeHtml(currentUser.username)}</strong> / ${escapeHtml(currentUser.role)}`;
  permissionBadges.innerHTML = currentPermissions
    .map((permission) => `<span class="permission-chip">${escapeHtml(permission)}</span>`)
    .join("");

  uploadSection.hidden = !hasPermission("upload");
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

document.getElementById("loginForm").addEventListener("submit", async (event) => {
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

checkHealth();
refreshMe();
resetDetail();
setUploadMessage("等待上傳");
